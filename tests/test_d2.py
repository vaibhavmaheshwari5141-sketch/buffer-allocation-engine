import json

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bufferengine.config import base_table_path, processed_dir
from bufferengine.pipeline import clean as C
from bufferengine.pipeline import feature_log, manifest
from bufferengine.pipeline.build_base import BASE_COLUMNS, DERIVED_COLUMNS
from bufferengine.pipeline.timeconv import hhmm_to_minutes

CLEAN = C.clean_path()
BASE = base_table_path()
have_data = CLEAN.exists() and BASE.exists()
needs_data = pytest.mark.skipif(not have_data, reason="clean dataset not built on this machine")

# Fields that describe what happened on or after the day, plus labels, keys, flags and partitions: never prediction inputs.
OUTCOME_FIELDS = [
    "DepTime", "DepDelay", "DepDel15", "TaxiOut", "WheelsOff", "WheelsOn", "TaxiIn", "ArrTime", "ArrDelay", "ArrDelayMinutes",
    "ArrDel15", "Cancelled", "CancellationCode", "Diverted", "ActualElapsedTime", "AirTime", "CarrierDelay", "WeatherDelay",
    "NASDelay", "SecurityDelay", "LateAircraftDelay", "arr_delay_min_capped", "is_operated", "struct_null_cancelled",
    "struct_null_diverted", "struct_null_delay_cause", "dq_missing_tail", "dq_extreme_delay_24h", "dq_time_conflict",
    "dq_duplicate_key", "dq_outlier_other", "split", "flight_id", "Tail_Number",
]


def q(sql: str):
    return duckdb.sql(sql).fetchall()


# Real dataset ------------------------------------------------------------------------------------------


@needs_data
def test_row_count_is_exactly_1641071():
    assert pq.ParquetFile(CLEAN).metadata.num_rows == C.EXPECTED_ROWS == 1_641_071
    assert pq.ParquetFile(BASE).metadata.num_rows == 1_641_071


@needs_data
def test_no_rows_deleted_or_added_and_original_columns_unchanged():
    cols = ", ".join(f'"{c}"' for c in list(BASE_COLUMNS) + list(DERIVED_COLUMNS))
    b, c = BASE.as_posix(), CLEAN.as_posix()
    only_base = q(f"SELECT COUNT(*) FROM (SELECT {cols} FROM read_parquet('{b}') EXCEPT SELECT {cols} FROM read_parquet('{c}'))")[0][0]
    only_clean = q(f"SELECT COUNT(*) FROM (SELECT {cols} FROM read_parquet('{c}') EXCEPT SELECT {cols} FROM read_parquet('{b}'))")[0][0]
    assert only_base == 0 and only_clean == 0
    assert q(f"SELECT COUNT(DISTINCT flight_id) FROM read_parquet('{c}')")[0][0] == C.EXPECTED_ROWS


@needs_data
def test_flags_are_consistent_with_the_base_table():
    c = CLEAN.as_posix()
    bad = q(f"""
      SELECT COUNT(*) FROM read_parquet('{c}') WHERE
        is_operated <> (Cancelled = 0 AND Diverted = 0)
        OR struct_null_cancelled <> (Cancelled = 1)
        OR struct_null_diverted <> (Diverted = 1 AND Cancelled = 0)
        OR struct_null_delay_cause <> NOT (Cancelled = 0 AND Diverted = 0 AND ArrDelay >= 15)
        OR dq_missing_tail <> (Tail_Number IS NULL)
        OR dq_extreme_delay_24h <> (COALESCE(DepDelay, 0) > 1440 OR COALESCE(ArrDelay, 0) > 1440)
        OR (Cancelled = 1 AND ArrDelay IS NOT NULL)""")[0][0]
    assert bad == 0
    # Structural nulls are not imputed: delay-cause columns stay null unless the arrival was 15+ minutes late.
    assert q(f"SELECT COUNT(*) FROM read_parquet('{c}') WHERE struct_null_delay_cause AND (CarrierDelay IS NOT NULL OR LateAircraftDelay IS NOT NULL)")[0][0] == 0
    assert q(f"SELECT COUNT(*) FROM read_parquet('{c}') WHERE NOT struct_null_delay_cause AND LateAircraftDelay IS NULL")[0][0] == 0


@needs_data
def test_capped_column_leaves_original_unchanged_and_uses_train_limits_only():
    c = CLEAN.as_posix()
    lo, hi = q(f"SELECT MIN(arr_delay_min_capped), MAX(arr_delay_min_capped) FROM read_parquet('{c}')")[0]
    assert lo < 0 < hi
    # within the limits the capped value equals the original; outside it is clipped; nulls stay null
    assert q(f"SELECT COUNT(*) FROM read_parquet('{c}') WHERE ArrDelay BETWEEN {lo} AND {hi} AND arr_delay_min_capped <> ArrDelay")[0][0] == 0
    assert q(f"SELECT COUNT(*) FROM read_parquet('{c}') WHERE ArrDelay > {hi} AND arr_delay_min_capped <> {hi}")[0][0] == 0
    assert q(f"SELECT COUNT(*) FROM read_parquet('{c}') WHERE (ArrDelay IS NULL) <> (arr_delay_min_capped IS NULL)")[0][0] == 0
    assert q(f"SELECT MAX(ArrDelay) FROM read_parquet('{c}')")[0][0] > hi  # the original still has its extremes
    # limits are the train-period quantiles, not whole-table quantiles
    tr = q(f"SELECT QUANTILE_CONT(ArrDelay, {C.CAP_QUANTILES[0]}), QUANTILE_CONT(ArrDelay, {C.CAP_QUANTILES[1]}) "
           f"FROM read_parquet('{c}') WHERE split = 'train' AND is_operated AND ArrDelay IS NOT NULL")[0]
    assert (lo, hi) == pytest.approx(tr)


@needs_data
def test_splits_follow_the_agreed_dates():
    c = CLEAN.as_posix()
    rows = dict(q(f"SELECT split, MIN(FlightDate)::VARCHAR || '..' || MAX(FlightDate)::VARCHAR FROM read_parquet('{c}') GROUP BY 1"))
    assert rows == {"train": "2025-01-01..2025-09-30", "validate": "2025-10-01..2025-12-31", "test": "2026-01-01..2026-08-31"}


@needs_data
def test_checksum_file_matches_the_files():
    assert manifest.verify_checksums() == []
    listed = {line.split("  ", 1)[1] for line in (manifest.DATA / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines()}
    assert "processed/aa_clean_v1.0.parquet" in listed and "interim/aa_base_2025_2026.parquet" in listed
    assert {"processed/rotations.parquet", "processed/rotation_turns.parquet", "processed/rotation_breaks.parquet", "processed/mtt_sensitivity.parquet"} <= listed


@needs_data
def test_manifest_matches_the_clean_file():
    m = json.loads((manifest.DATA / "data_manifest.json").read_text(encoding="utf-8"))
    pf = pq.ParquetFile(CLEAN)
    assert m["data_version"] == "data-v1.0"
    assert m["row_count_clean"] == pf.metadata.num_rows and m["column_count_clean"] == pf.metadata.num_columns
    assert m["schema_sha256_clean"] == manifest.schema_hash(pf.schema_arrow)


@needs_data
def test_dictionary_covers_every_column_and_marks_no_outcome_as_feature():
    d = pd.read_csv(C.Path(__file__).resolve().parents[1] / "docs" / "data_dictionary_clean.csv", keep_default_na=False)
    cols = [f.name for f in pq.read_schema(CLEAN)]
    assert list(d["name"]) == cols
    flagged = d[d["name"].isin(OUTCOME_FIELDS) & (d["feature_allowed"] != "no")]
    assert flagged.empty, f"outcome fields marked as features: {list(flagged['name'])}"
    assert set(d["feature_allowed"]) <= {"yes", "no", "review"}


def test_feature_log_marks_no_outcome_as_feature():
    df = feature_log.feature_frame()
    assert df["name"].is_unique
    assert set(df["status"]) == {"built", "planned"} and set(df["kind"]) <= {"real", "derived", "assumption", "model"}
    assert set(df["leakage_risk"]) <= {"none", "low", "review", "outcome"}
    assert set(df["allowed_as_prediction_feature"]) <= {"yes", "no", "review"}
    # anything with outcome risk must be 'no'; anything allowed 'yes' must have no or low leakage risk
    assert (df.loc[df["leakage_risk"] == "outcome", "allowed_as_prediction_feature"] == "no").all()
    assert df.loc[df["allowed_as_prediction_feature"] == "yes", "leakage_risk"].isin(["none", "low"]).all()
    for outcome in ["ArrDelay", "DepDelay", "TaxiOut", "TaxiIn", "LateAircraftDelay", "arr_delay_min_capped", "primary_delay_min", "actual_ground_min"]:
        rows = df[df["name"].str.contains(outcome, regex=False)]
        assert (rows["allowed_as_prediction_feature"] != "yes").all(), outcome
    # the agreed leakage rules are recorded
    assert (df.loc[df["name"] == "leg_idx", "allowed_as_prediction_feature"] == "review").all()
    assert (df.loc[df["name"].str.startswith("hist_"), "allowed_as_prediction_feature"] == "review").all()
    assert df.loc[df["name"].str.startswith("hist_"), "reason"].str.contains("training window only", case=False).all()


def test_every_built_clean_column_has_a_dictionary_entry():
    assert set(C.NEW_COLUMNS) <= set(C.BASE_META)
    assert set(BASE_COLUMNS) | set(DERIVED_COLUMNS) <= set(C.BASE_META)


# Synthetic unit tests of the clean step --------------------------------------------------------------

ARROW = {"SMALLINT": pa.int16(), "TINYINT": pa.int8(), "INTEGER": pa.int32(), "DOUBLE": pa.float64(), "DATE": pa.date32(), "VARCHAR": pa.string()}


def synthetic_base(tmp_path, rows):
    cols = {**BASE_COLUMNS, **DERIVED_COLUMNS}
    data = {c: [] for c in cols}
    for r in rows:
        dep, arr = hhmm_to_minutes([r["dep"]]).iloc[0], hhmm_to_minutes([r["arr"]]).iloc[0]
        canc, div = r.get("cancelled", 0), r.get("diverted", 0)
        operated = not (canc or div)
        ad = r.get("ad") if operated else None
        rec = {
            "Year": 2025, "Quarter": 1, "Month": 1, "DayofMonth": 1, "DayOfWeek": 3, "FlightDate": pd.Timestamp(r["date"]).date(),
            "Reporting_Airline": "AA", "Tail_Number": r["tail"], "Flight_Number_Reporting_Airline": r["fn"], "OriginAirportID": 1, "Origin": "DFW",
            "OriginCityName": "Dallas", "OriginState": "TX", "DestAirportID": 2, "Dest": "ORD", "DestCityName": "Chicago", "DestState": "IL",
            "CRSDepTime": r["dep"], "DepTime": r["dep"] if not canc else None, "DepDelay": r.get("dd", 0.0) if not canc else None, "DepDel15": 0 if not canc else None,
            "DepTimeBlk": "0800-0859", "TaxiOut": 15.0 if not canc else None, "WheelsOff": None, "WheelsOn": None, "TaxiIn": 8.0 if operated else None,
            "CRSArrTime": r["arr"], "ArrTime": r["arr"] if operated else None, "ArrDelay": ad,
            "ArrDelayMinutes": max(ad, 0) if ad is not None else None, "ArrDel15": int(ad >= 15) if ad is not None else None,
            "Cancelled": canc, "CancellationCode": "B" if canc else None, "Diverted": div, "CRSElapsedTime": 120.0,
            "ActualElapsedTime": 120.0 if operated else None, "AirTime": 100.0 if operated else None, "Distance": 800.0,
            "CarrierDelay": 10.0 if (ad is not None and ad >= 15) else None, "WeatherDelay": 0.0 if (ad is not None and ad >= 15) else None,
            "NASDelay": 5.0 if (ad is not None and ad >= 15) else None, "SecurityDelay": 0.0 if (ad is not None and ad >= 15) else None,
            "LateAircraftDelay": ad - 15.0 if (ad is not None and ad >= 15) else None, "crs_dep_min": dep, "crs_arr_min": arr,
        }
        for c in cols:
            data[c].append(rec[c])
    schema = pa.schema([pa.field(c, ARROW[t]) for c, t in cols.items()])
    base = tmp_path / "base.parquet"
    pq.write_table(pa.table({c: pa.array(v, type=schema.field(c).type) for c, v in data.items()}, schema=schema), base)
    airports = tmp_path / "airports_tz.csv"
    pd.DataFrame({"iata": ["DFW", "ORD"], "timezone": ["America/Chicago", "America/Chicago"]}).to_csv(airports, index=False)
    return base, airports


SYNTH = [
    {"date": "2025-01-10", "tail": "N1", "fn": 1, "dep": 800, "arr": 930, "ad": 5.0},
    {"date": "2025-01-10", "tail": "N1", "fn": 2, "dep": 1100, "arr": 1230, "ad": 45.0, "dd": 40.0},
    {"date": "2025-01-11", "tail": "N2", "fn": 3, "dep": 800, "arr": 930, "cancelled": 1},
    {"date": "2025-01-11", "tail": None, "fn": 4, "dep": 900, "arr": 1030, "cancelled": 1},
    {"date": "2025-01-12", "tail": "N3", "fn": 5, "dep": 800, "arr": 930, "diverted": 1},
    {"date": "2025-01-12", "tail": "N4", "fn": 6, "dep": 800, "arr": 930, "ad": 2000.0, "dd": 1990.0},
    {"date": "2025-10-05", "tail": "N5", "fn": 7, "dep": 800, "arr": 930, "ad": 900.0},   # validate period: must not set the cap
]


def test_clean_flags_nulls_and_cap_on_synthetic_data(tmp_path):
    base, airports = synthetic_base(tmp_path, SYNTH)
    df, info = C.build_clean_frame(base, airports)
    assert len(df) == len(SYNTH)                                    # nothing dropped
    by = df.set_index("Flight_Number_Reporting_Airline")
    assert by.loc[3, "struct_null_cancelled"] and pd.isna(by.loc[3, "ArrDelay"]) and pd.isna(by.loc[3, "arr_delay_min_capped"])  # not imputed
    assert by.loc[5, "struct_null_diverted"] and not by.loc[5, "struct_null_cancelled"]
    assert by.loc[4, "dq_missing_tail"] and not by.loc[1, "dq_missing_tail"]
    assert by.loc[6, "dq_extreme_delay_24h"] and not by.loc[2, "dq_extreme_delay_24h"]
    assert by.loc[1, "struct_null_delay_cause"] and not by.loc[2, "struct_null_delay_cause"] and pd.isna(by.loc[1, "LateAircraftDelay"])
    assert by["is_operated"].tolist() == [True, True, False, False, False, True, True]
    assert by["split"].tolist() == ["train"] * 6 + ["validate"]
    # cap limits come from train rows only: the 2000-minute train row sets the upper limit, the validate row (900) does not matter
    train_vals = pd.Series([5.0, 45.0, 2000.0])
    assert info["cap_high"] == pytest.approx(train_vals.quantile(C.CAP_QUANTILES[1]))
    assert by.loc[6, "ArrDelay"] == 2000.0 and by.loc[6, "arr_delay_min_capped"] <= info["cap_high"]   # original untouched
    assert by.loc[7, "arr_delay_min_capped"] == 900.0 or by.loc[7, "arr_delay_min_capped"] <= info["cap_high"]


def test_duplicate_key_flag_and_utc_columns(tmp_path):
    rows = SYNTH[:1] + [dict(SYNTH[0], tail="N9")]                  # same flight key twice
    base, airports = synthetic_base(tmp_path, rows)
    df, _ = C.build_clean_frame(base, airports)
    assert df["dq_duplicate_key"].all()
    assert df["crs_dep_utc"].iloc[0] == pd.Timestamp("2025-01-10 14:00", tz="UTC")   # 08:00 CST
    assert df["crs_arr_utc"].iloc[0] == pd.Timestamp("2025-01-10 15:30", tz="UTC")


def test_clean_run_fails_when_the_row_count_is_wrong(tmp_path):
    base, airports = synthetic_base(tmp_path, SYNTH)
    with pytest.raises(ValueError, match="expected 1,641,071"):
        C.run(out_dir=tmp_path, base_path=base, airports_path=airports)


def test_clean_output_is_deterministic(tmp_path):
    base, airports = synthetic_base(tmp_path, SYNTH)
    df, _ = C.build_clean_frame(base, airports)
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    C.write_clean(df, a, base)
    C.write_clean(C.build_clean_frame(base, airports)[0], b, base)
    assert manifest.file_sha256(a) == manifest.file_sha256(b)
    assert manifest.content_sha256(a) == manifest.content_sha256(b)
    assert pq.read_table(a).schema.names == list(BASE_COLUMNS) + list(DERIVED_COLUMNS) + C.NEW_COLUMNS
