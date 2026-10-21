"""Clean step: aa_clean_v1.0.parquet from the AA base table (data-v1.0).

Rules:
  * Every base row is kept (no deletions). The step fails if the row count is not EXPECTED_ROWS.
  * Original BTS columns are never modified. Structural nulls (delay columns on cancelled flights, delay-cause
    columns on flights that are not late) are NOT imputed; they are flagged.
  * Added columns are listed in NEW_COLUMNS. Capping (winsorising) is written to a separate column,
    ``arr_delay_min_capped``, with thresholds taken from TRAIN-period operated flights only.
  * Output is zstd level 19, rows in a fixed order, so repeated runs give identical bytes.

Usage:
    python -m bufferengine.pipeline.clean
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bufferengine.config import base_table_path, interim_dir, processed_dir, reference_dir
from bufferengine.pipeline.timeconv import scheduled_times_utc

VERSION = "1.0"
CLEAN_FILE = f"aa_clean_v{VERSION}.parquet"
DQ_LOG_FILE = "dq_treatment_log.csv"
EXPECTED_ROWS = 1_641_071
TRAIN_END = pd.Timestamp("2025-09-30")
VALIDATE_END = pd.Timestamp("2025-12-31")
CAP_QUANTILES = (0.005, 0.995)  # winsorising limits for ArrDelay, estimated on train operated flights only
ZSTD_LEVEL = 19
DUP_KEY = ["FlightDate", "Flight_Number_Reporting_Airline", "Origin", "Dest", "CRSDepTime"]
EXTREME_DELAY_MIN = 1440  # 24 hours
TAXI_EXTREME_MIN = 180
ELAPSED_DIFF_EXTREME_MIN = 180
DISTANCE_MAX_MI = 5000
EARLY_DEP_MIN = -60
CAUSE_COLS = ["CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay"]

NEW_COLUMNS = [
    "flight_id", "is_operated", "struct_null_cancelled", "struct_null_diverted", "struct_null_delay_cause",
    "dq_missing_tail", "dq_extreme_delay_24h", "dq_time_conflict", "dq_duplicate_key", "dq_outlier_other",
    "crs_dep_utc", "crs_arr_utc", "split", "arr_delay_min_capped",
]


def clean_path(root: Path | None = None) -> Path:
    return (root or processed_dir()) / CLEAN_FILE


def _split(flight_date: pd.Series) -> pd.Series:
    d = pd.to_datetime(flight_date)
    out = np.where(d <= TRAIN_END, "train", np.where(d <= VALIDATE_END, "validate", "test"))
    return pd.Series(out, index=flight_date.index, dtype="string")


def build_clean_frame(base_path: Path | None = None, airports_path: Path | None = None):
    """Return (clean DataFrame, info dict with thresholds and component counts)."""
    base_path = base_path or base_table_path()
    airports_path = airports_path or reference_dir() / "airports_tz.csv"
    con = duckdb.connect()
    p = base_path.as_posix().replace("'", "''")
    df = con.execute(f"""
        SELECT * FROM read_parquet('{p}')
        ORDER BY FlightDate, Tail_Number NULLS LAST, CRSDepTime, Flight_Number_Reporting_Airline, Origin, Dest""").df()
    n_base = len(df)

    tz = pd.read_csv(airports_path).set_index("iata")["timezone"]
    t = scheduled_times_utc(df["FlightDate"], df["crs_dep_min"], df["crs_arr_min"], df["Origin"].map(tz), df["Dest"].map(tz))
    df["flight_id"] = (
        pd.to_datetime(df["FlightDate"]).dt.strftime("%Y%m%d") + "-" + df["Flight_Number_Reporting_Airline"].astype(str)
        + "-" + df["Origin"] + "-" + df["Dest"] + "-" + df["CRSDepTime"].astype(str).str.zfill(4)
    )
    cancelled, diverted = df["Cancelled"] == 1, df["Diverted"] == 1
    df["is_operated"] = ~(cancelled | diverted)
    df["struct_null_cancelled"] = cancelled
    df["struct_null_diverted"] = diverted & ~cancelled
    late = df["is_operated"] & (df["ArrDelay"] >= 15)
    df["struct_null_delay_cause"] = ~late

    comp = {
        "utc_block_mismatch": ((t["crs_block_min_utc"] - df["CRSElapsedTime"]).abs() >= 0.5).fillna(False),
        "airtime_gt_elapsed": (df["AirTime"] > df["ActualElapsedTime"]).fillna(False),
        "arrdel15_inconsistent": (df["ArrDel15"].notna() & df["ArrDelay"].notna()
                                  & (df["ArrDel15"].astype("float64") != (df["ArrDelay"] >= 15).astype("float64"))),
        "operated_without_arrdelay": df["is_operated"] & df["ArrDelay"].isna(),
        "cancelled_with_arrtime": cancelled & df["ArrTime"].notna(),
    }
    elapsed_diff = (df["ActualElapsedTime"] - df["CRSElapsedTime"]).abs()
    outl = {
        "taxi_extreme": ((df["TaxiOut"] > TAXI_EXTREME_MIN) | (df["TaxiIn"] > TAXI_EXTREME_MIN)).fillna(False),
        "early_departure": (df["DepDelay"] < EARLY_DEP_MIN).fillna(False),
        "elapsed_vs_scheduled_extreme": (elapsed_diff > ELAPSED_DIFF_EXTREME_MIN).fillna(False),
        "distance_invalid": ((df["Distance"] <= 0) | (df["Distance"] > DISTANCE_MAX_MI)).fillna(False),
    }
    extreme = ((df["DepDelay"] > EXTREME_DELAY_MIN) | (df["ArrDelay"] > EXTREME_DELAY_MIN)).fillna(False)
    df["dq_missing_tail"] = df["Tail_Number"].isna()
    df["dq_extreme_delay_24h"] = extreme
    df["dq_time_conflict"] = np.logical_or.reduce(list(comp.values()))
    df["dq_duplicate_key"] = df.duplicated(subset=DUP_KEY, keep=False)
    df["dq_outlier_other"] = np.logical_or.reduce(list(outl.values()))

    df["crs_dep_utc"] = t["crs_dep_utc"]
    df["crs_arr_utc"] = t["crs_arr_utc"]
    df["split"] = _split(df["FlightDate"])

    train_ops = df[(df["split"] == "train") & df["is_operated"] & df["ArrDelay"].notna()]["ArrDelay"]
    lo, hi = (float(train_ops.quantile(q)) for q in CAP_QUANTILES)
    df["arr_delay_min_capped"] = df["ArrDelay"].clip(lower=lo, upper=hi)

    info = {
        "n_base": n_base, "cap_low": lo, "cap_high": hi, "cap_quantiles": CAP_QUANTILES,
        "train_operated_n": int(len(train_ops)), "components": {k: int(v.sum()) for k, v in {**comp, **outl}.items()},
    }
    return df, info


def arrow_table(df: pd.DataFrame, base_path: Path | None = None) -> pa.Table:
    """Cast the frame to base-table types for the original columns plus explicit types for new columns."""
    base_schema = pq.read_schema(base_path or base_table_path())
    new = {
        "flight_id": pa.string(), "is_operated": pa.bool_(), "struct_null_cancelled": pa.bool_(),
        "struct_null_diverted": pa.bool_(), "struct_null_delay_cause": pa.bool_(), "dq_missing_tail": pa.bool_(),
        "dq_extreme_delay_24h": pa.bool_(), "dq_time_conflict": pa.bool_(), "dq_duplicate_key": pa.bool_(),
        "dq_outlier_other": pa.bool_(), "crs_dep_utc": pa.timestamp("us", tz="UTC"),
        "crs_arr_utc": pa.timestamp("us", tz="UTC"), "split": pa.string(), "arr_delay_min_capped": pa.float64(),
    }
    fields = list(base_schema) + [pa.field(k, new[k]) for k in NEW_COLUMNS]
    return pa.Table.from_pandas(df[[f.name for f in fields]], schema=pa.schema(fields), preserve_index=False)


def write_clean(df: pd.DataFrame, out_path: Path, base_path: Path | None = None) -> Path:
    table = arrow_table(df, base_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path, compression="zstd", compression_level=ZSTD_LEVEL, row_group_size=250_000,
                   use_dictionary=True, write_statistics=True, data_page_version="2.0")
    return out_path


# DQ treatment log -------------------------------------------------------------------------------------


def dq_treatment_log(df: pd.DataFrame, info: dict, base_build_log: pd.DataFrame | None = None) -> pd.DataFrame:
    n = len(df)
    c = info["components"]
    canc, div = df["Cancelled"] == 1, df["Diverted"] == 1
    rows = []

    def add(issue, count, treatment, reason):
        rows.append({"issue": issue, "count": int(count), "percent": round(100 * int(count) / n, 4),
                     "treatment": treatment, "reason": reason})

    if base_build_log is not None and "TOTAL" in set(base_build_log["file"]):
        tot = base_build_log[base_build_log["file"] == "TOTAL"].iloc[0]
        excl = int(tot["rows_all_carriers"] - tot["rows_aa"])
        rows.append({"issue": "Non-AA carrier rows in the raw BTS files (scope filter applied in the base step, before cleaning)",
                     "count": excl, "percent": round(100 * excl / int(tot["rows_all_carriers"]), 4),
                     "treatment": "Excluded by design (Reporting_Airline = 'AA'); percent is of all raw rows",
                     "reason": "Project scope is AA mainline; counts per month are in docs/d2/tables/aa_share_by_month.csv"})
    add("Cancelled flights: departure, arrival, delay, taxi and elapsed columns are null by definition", canc.sum(),
        "Kept as null (not imputed); flagged struct_null_cancelled", "Null means the flight did not operate, not that a value is missing")
    add("Diverted flights: arrival-side columns are null by definition", (div & ~canc).sum(),
        "Kept as null (not imputed); flagged struct_null_diverted", "BTS does not report arrival fields for diverted flights")
    add("Delay-cause columns null because the flight is not a late arrival (ArrDelay < 15) or did not operate",
        df["struct_null_delay_cause"].sum(), "Kept as null (not imputed); flagged struct_null_delay_cause",
        "BTS fills the five cause columns only for arrivals 15+ minutes late; zero would be a false value")
    add("CancellationCode null on flights that were not cancelled", (~canc).sum(), "Kept as null",
        "Only meaningful for cancelled flights")
    mt, mt_canc = int(df["dq_missing_tail"].sum()), int((df["dq_missing_tail"] & canc).sum())
    add("Missing Tail_Number", mt, "Row kept; flagged dq_missing_tail; cannot join a rotation",
        f"{mt_canc} of {mt} are cancelled flights; they count as 'cancelled' in rotations.parquet")
    add("Extreme delay: DepDelay or ArrDelay more than 24 hours", df["dq_extreme_delay_24h"].sum(),
        "Row kept and original delays untouched; flagged dq_extreme_delay_24h; arr_delay_min_capped added for modelling",
        "Real events (flights held overnight); deleting them would bias delay statistics downwards")
    n_capped = int(((df["ArrDelay"] < info["cap_low"]) | (df["ArrDelay"] > info["cap_high"])).sum())
    add(f"ArrDelay outside the winsorising limits [{info['cap_low']:.1f}, {info['cap_high']:.1f}] min "
        f"(train-period operated flights, quantiles {info['cap_quantiles'][0]} / {info['cap_quantiles'][1]})", n_capped,
        "Clipped in the new column arr_delay_min_capped; original ArrDelay unchanged",
        "Limits come from train data only, so no test information enters the cap; for models sensitive to extreme values")
    add("Duplicate flight key (FlightDate, flight number, Origin, Dest, CRSDepTime)", df["dq_duplicate_key"].sum(),
        "None needed; flagged dq_duplicate_key", "No duplicates found")
    add("Time conflict: UTC block time differs from BTS CRSElapsedTime by 0.5 min or more", c["utc_block_mismatch"],
        "Row kept; flagged dq_time_conflict", "Mismatching flights are on the US daylight-saving change date (see docs/d2/tables/time_conflicts.csv); the BTS value looks like it ignores the clock change. Not verified with BTS")
    add("Time conflict: AirTime greater than ActualElapsedTime", c["airtime_gt_elapsed"], "Row kept; flagged dq_time_conflict", "Physically inconsistent")
    add("Time conflict: ArrDel15 inconsistent with ArrDelay", c["arrdel15_inconsistent"], "Row kept; flagged dq_time_conflict", "Label and value must agree")
    add("Operated flight with missing ArrDelay", c["operated_without_arrdelay"], "Row kept; flagged dq_time_conflict", "Operated flights should always have an arrival delay")
    add("Cancelled flight with an ArrTime", c["cancelled_with_arrtime"], "Row kept; flagged dq_time_conflict", "A cancelled flight cannot arrive")
    add(f"Taxi time over {TAXI_EXTREME_MIN} minutes (TaxiOut or TaxiIn)", c["taxi_extreme"], "Row kept; flagged dq_outlier_other",
        "Plausibly real (long ground holds); not removed")
    add(f"Departure more than {abs(EARLY_DEP_MIN)} minutes early", c["early_departure"], "Row kept; flagged dq_outlier_other", "Extreme but possible")
    add(f"ActualElapsedTime differs from CRSElapsedTime by more than {ELAPSED_DIFF_EXTREME_MIN} minutes",
        c["elapsed_vs_scheduled_extreme"], "Row kept; flagged dq_outlier_other", "Holding, diversion-like routing or extreme taxi; not removed")
    add(f"Distance zero, negative or above {DISTANCE_MAX_MI} miles", c["distance_invalid"], "None needed; flagged dq_outlier_other", "No invalid distances found")
    unexpected = _unexpected_null_count(df)
    add("Nulls not explained by the structural rules (unexpected missing values), excluding Tail_Number which is counted above", unexpected, "None needed" if unexpected == 0 else "Row kept; investigate",
        "Every null in an operated flight's timing columns would be unexpected; see docs/d2/tables/missing_by_column.csv")
    return pd.DataFrame(rows)


STRUCT_RULES = {
    # column -> boolean Series factory describing where a null is expected
    "dep_cols": ["DepTime", "DepDelay", "DepDel15", "TaxiOut", "WheelsOff"],
    "arr_cols": ["WheelsOn", "TaxiIn", "ArrTime", "ArrDelay", "ArrDelayMinutes", "ArrDel15", "ActualElapsedTime", "AirTime"],
}


def expected_null_mask(df: pd.DataFrame, col: str) -> pd.Series:
    """True where a null in ``col`` is explained by the BTS reporting rules."""
    canc, div = df["Cancelled"] == 1, df["Diverted"] == 1
    if col in CAUSE_COLS:
        return df["struct_null_delay_cause"]
    if col == "CancellationCode":
        return ~canc
    if col in STRUCT_RULES["dep_cols"]:
        return canc
    if col in STRUCT_RULES["arr_cols"]:
        return canc | div
    if col == "arr_delay_min_capped":
        return canc | div
    return pd.Series(False, index=df.index)


def _unexpected_null_count(df: pd.DataFrame) -> int:
    total = 0
    for col in df.columns:
        if col in ("Tail_Number",):
            continue  # reported separately as dq_missing_tail
        total += int((df[col].isna() & ~expected_null_mask(df, col)).sum())
    return total


# Dictionary ------------------------------------------------------------------------------------------

# (description, unit, null rule, kind, feature_allowed)
BASE_META: dict[str, tuple] = {
    "Year": ("Calendar year of FlightDate", "year", "Never null", "real", "yes"),
    "Quarter": ("Quarter 1-4", "", "Never null", "real", "yes"),
    "Month": ("Month 1-12", "", "Never null", "real", "yes"),
    "DayofMonth": ("Day of month", "", "Never null", "real", "yes"),
    "DayOfWeek": ("Day of week, 1 = Monday ... 7 = Sunday", "", "Never null", "real", "yes"),
    "FlightDate": ("Scheduled departure date, local at origin", "date", "Never null", "real", "yes"),
    "Reporting_Airline": ("Carrier code; always AA", "", "Never null", "real", "no"),
    "Tail_Number": ("Aircraft registration that operated the flight (trimmed, upper-case)", "", "Null for 11 cancelled flights (flag dq_missing_tail)", "real", "no"),
    "Flight_Number_Reporting_Airline": ("Flight number", "", "Never null", "real", "yes"),
    "OriginAirportID": ("BTS airport ID of origin", "", "Never null", "real", "yes"),
    "Origin": ("Origin airport IATA code", "", "Never null", "real", "yes"),
    "OriginCityName": ("Origin city, state", "", "Never null", "real", "yes"),
    "OriginState": ("Origin state code", "", "Never null", "real", "yes"),
    "DestAirportID": ("BTS airport ID of destination", "", "Never null", "real", "yes"),
    "Dest": ("Destination airport IATA code", "", "Never null", "real", "yes"),
    "DestCityName": ("Destination city, state", "", "Never null", "real", "yes"),
    "DestState": ("Destination state code", "", "Never null", "real", "yes"),
    "CRSDepTime": ("Scheduled departure, local hhmm", "hhmm", "Never null", "real", "yes"),
    "DepTime": ("Actual departure, local hhmm", "hhmm", "Structural null: cancelled flights", "real", "no"),
    "DepDelay": ("Actual minus scheduled departure; negative = early", "min", "Structural null: cancelled flights", "real", "no"),
    "DepDel15": ("1 if DepDelay >= 15", "0/1", "Structural null: cancelled flights", "real", "no"),
    "DepTimeBlk": ("Scheduled departure hour block", "", "Never null", "real", "yes"),
    "TaxiOut": ("Taxi-out time", "min", "Structural null: cancelled flights", "real", "no"),
    "WheelsOff": ("Wheels-off time, local hhmm", "hhmm", "Structural null: cancelled flights", "real", "no"),
    "WheelsOn": ("Wheels-on time, local hhmm", "hhmm", "Structural null: cancelled or diverted flights", "real", "no"),
    "TaxiIn": ("Taxi-in time", "min", "Structural null: cancelled or diverted flights", "real", "no"),
    "CRSArrTime": ("Scheduled arrival, local hhmm at destination", "hhmm", "Never null", "real", "yes"),
    "ArrTime": ("Actual arrival, local hhmm", "hhmm", "Structural null: cancelled or diverted flights", "real", "no"),
    "ArrDelay": ("Actual minus scheduled arrival; negative = early (outcome)", "min", "Structural null: cancelled or diverted flights", "real", "no"),
    "ArrDelayMinutes": ("max(ArrDelay, 0) (outcome)", "min", "Structural null: cancelled or diverted flights", "real", "no"),
    "ArrDel15": ("1 if ArrDelay >= 15 (prediction target)", "0/1", "Structural null: cancelled or diverted flights", "real", "no"),
    "Cancelled": ("1 if the flight was cancelled", "0/1", "Never null", "real", "no"),
    "CancellationCode": ("A carrier, B weather, C national air system, D security", "", "Structural null: flights that were not cancelled", "real", "no"),
    "Diverted": ("1 if the flight was diverted", "0/1", "Never null", "real", "no"),
    "CRSElapsedTime": ("Scheduled block time", "min", "Never null", "real", "yes"),
    "ActualElapsedTime": ("Actual block time", "min", "Structural null: cancelled or diverted flights", "real", "no"),
    "AirTime": ("Airborne time", "min", "Structural null: cancelled or diverted flights", "real", "no"),
    "Distance": ("Great-circle distance between airports", "mi", "Never null", "real", "yes"),
    "CarrierDelay": ("Arrival delay attributed to the carrier", "min", "Structural null: flights that are not 15+ min late arrivals", "real", "no"),
    "WeatherDelay": ("Arrival delay attributed to extreme weather", "min", "Structural null: flights that are not 15+ min late arrivals", "real", "no"),
    "NASDelay": ("Arrival delay attributed to the National Airspace System", "min", "Structural null: flights that are not 15+ min late arrivals", "real", "no"),
    "SecurityDelay": ("Arrival delay attributed to security", "min", "Structural null: flights that are not 15+ min late arrivals", "real", "no"),
    "LateAircraftDelay": ("Arrival delay attributed to the previous flight of the same aircraft arriving late", "min", "Structural null: flights that are not 15+ min late arrivals", "real", "no"),
    "crs_dep_min": ("CRSDepTime as minutes after local midnight (2400 = 1440)", "min", "Never null", "derived", "yes"),
    "crs_arr_min": ("CRSArrTime as minutes after local midnight (2400 = 1440)", "min", "Never null", "derived", "yes"),
    "flight_id": ("Stable key: YYYYMMDD-flightnumber-origin-dest-CRSDepTime (unique)", "", "Never null", "derived", "no"),
    "is_operated": ("True when the flight was neither cancelled nor diverted", "", "Never null", "derived", "no"),
    "struct_null_cancelled": ("True for cancelled flights: their operating columns are structurally null", "", "Never null", "derived", "no"),
    "struct_null_diverted": ("True for diverted (not cancelled) flights: their arrival columns are structurally null", "", "Never null", "derived", "no"),
    "struct_null_delay_cause": ("True when the five delay-cause columns are structurally null (flight not a 15+ min late arrival)", "", "Never null", "derived", "no"),
    "dq_missing_tail": ("Data-quality flag: Tail_Number is null", "", "Never null", "derived", "no"),
    "dq_extreme_delay_24h": ("Data-quality flag: DepDelay or ArrDelay above 24 hours", "", "Never null", "derived", "no"),
    "dq_time_conflict": ("Data-quality flag: UTC block time differs from CRSElapsedTime, AirTime > ActualElapsedTime, ArrDel15 inconsistent, operated flight without ArrDelay, or cancelled flight with ArrTime", "", "Never null", "derived", "no"),
    "dq_duplicate_key": ("Data-quality flag: another row has the same FlightDate, flight number, Origin, Dest and CRSDepTime", "", "Never null", "derived", "no"),
    "dq_outlier_other": ("Data-quality flag: taxi time over 180 min, departure over 60 min early, elapsed time differing from schedule by over 180 min, or invalid distance", "", "Never null", "derived", "no"),
    "crs_dep_utc": ("Scheduled departure in UTC (origin time zone from airports_tz.csv)", "UTC timestamp", "Never null", "derived", "yes"),
    "crs_arr_utc": ("Scheduled arrival in UTC (arrival date inferred, overnight handled)", "UTC timestamp", "Never null", "derived", "yes"),
    "split": ("train (to 2025-09-30), validate (2025-10 to 2025-12), test (2026-01 to 2026-08), by FlightDate", "", "Never null", "derived", "no"),
    "arr_delay_min_capped": ("ArrDelay clipped to train-period limits [q0.5%, q99.5%] for modelling; ArrDelay itself is unchanged (outcome)", "min", "Null when ArrDelay is null (cancelled or diverted)", "derived", "no"),
}


def dictionary_frame(table_schema: pa.Schema) -> pd.DataFrame:
    rows = []
    for f in table_schema:
        d, unit, nulls, kind, feat = BASE_META[f.name]
        rows.append({"name": f.name, "type": str(f.type), "unit": unit, "null_rule": nulls, "kind": kind,
                     "description": d, "feature_allowed": feat})
    return pd.DataFrame(rows)


def run(out_dir: Path | None = None, base_path: Path | None = None, airports_path: Path | None = None) -> dict:
    out_dir = out_dir or processed_dir()
    df, info = build_clean_frame(base_path, airports_path)
    if len(df) != EXPECTED_ROWS:
        raise ValueError(f"clean step has {len(df):,} rows, expected {EXPECTED_ROWS:,}: rows were added or lost")
    path = write_clean(df, clean_path(out_dir), base_path)
    log_path = interim_dir() / "build_base_log.csv"
    base_log = pd.read_csv(log_path) if log_path.exists() else None
    log = dq_treatment_log(df, info, base_log)
    log.to_csv(out_dir / DQ_LOG_FILE, index=False)
    info.update({"path": path, "bytes": path.stat().st_size, "rows": len(df), "columns": df.shape[1]})
    print(f"wrote {path.name}: {info['rows']:,} rows x {info['columns']} columns, {info['bytes'] / 1e6:.1f} MB "
          f"(cap [{info['cap_low']:.1f}, {info['cap_high']:.1f}] min)")
    return info


if __name__ == "__main__":
    run()


# Sample and dictionary files ---------------------------------------------------------------------------

SAMPLE_ROWS = 5000
SAMPLE_SEED = 42


def write_sample(clean_file: Path | None = None, out_path: Path | None = None) -> Path:
    """5,000-row sample CSV, stratified by month (equal rows per month, fixed seed), for repos that cannot hold the parquet."""
    clean_file = clean_file or clean_path()
    out_path = out_path or Path(__file__).resolve().parents[3] / "data" / "sample" / "aa_clean_sample.csv"
    df = pq.read_table(clean_file).to_pandas()
    df["_month"] = pd.to_datetime(df["FlightDate"]).dt.strftime("%Y-%m")
    months = sorted(df["_month"].unique())
    per = SAMPLE_ROWS // len(months)
    parts = [g.sample(n=min(per, len(g)), random_state=SAMPLE_SEED) for _, g in df.groupby("_month")]
    s = pd.concat(parts).sort_values(["FlightDate", "flight_id"]).drop(columns="_month")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    s.to_csv(out_path, index=False)
    return out_path


def write_dictionary(clean_file: Path | None = None, docs_dir: Path | None = None) -> pd.DataFrame:
    docs_dir = docs_dir or Path(__file__).resolve().parents[3] / "docs"
    d = dictionary_frame(pq.read_schema(clean_file or clean_path()))
    d.to_csv(docs_dir / "data_dictionary_clean.csv", index=False)
    lines = [
        f"# Data dictionary: aa_clean_v{VERSION}.parquet", "",
        "Generated by `bufferengine.pipeline.clean.write_dictionary`. One row per column of the cleaned dataset "
        f"({len(d)} columns, {EXPECTED_ROWS:,} rows). Original BTS columns keep their BTS names and are **never modified**; "
        "columns added by the clean step are in snake_case.", "",
        "`feature_allowed`: **yes** = known before departure (schedule and calendar); **no** = outcome, actual, label, key, flag or partition; "
        "**review** = would need a leakage review (none in this file; see `docs/feature_log.md`). The prediction target is `ArrDel15`.", "",
        "`kind`: real = BTS value unchanged; derived = computed from real data and public reference data.", "",
        "| name | type | unit | null rule | kind | description | feature_allowed |", "|---|---|---|---|---|---|---|",
    ]
    for r in d.itertuples():
        lines.append(f"| {r.name} | {r.type} | {r.unit} | {r.null_rule} | {r.kind} | {r.description} | {r.feature_allowed} |")
    lines += ["", "## Notes", "",
              "- **Structural nulls are not imputed.** Delay, time and taxi columns are null for cancelled flights; arrival columns are null for diverted flights; "
              "the five delay-cause columns are null unless the flight arrived 15+ minutes late. The `struct_null_*` flags mark these rows.",
              "- **`arr_delay_min_capped`** is `ArrDelay` clipped to limits estimated on train-period operated flights only (see `data/data_manifest.json` for the values). It exists for modelling; `ArrDelay` is unchanged.",
              "- **Row count is fixed at 1,641,071.** No row was deleted; `dq_*` flags identify rows to review or exclude in an analysis."]
    (docs_dir / "data_dictionary_clean.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d
