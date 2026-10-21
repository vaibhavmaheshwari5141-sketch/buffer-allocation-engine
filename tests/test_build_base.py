import csv
import io
import zipfile

import duckdb
import pytest

from bufferengine.config import base_table_path
from bufferengine.pipeline.build_base import BASE_COLUMNS, DERIVED_COLUMNS, build_month, connect

EXPECTED_COLUMNS = list(BASE_COLUMNS) + list(DERIVED_COLUMNS)

# A few extra BTS columns that the base table must drop.
EXTRA = ["DOT_ID_Reporting_Airline", "IATA_CODE_Reporting_Airline", "Div1Airport"]


def _row(carrier, tail, flight, crs_dep, crs_arr, cancelled="0.00"):
    r = {c: "" for c in [*BASE_COLUMNS, *EXTRA]}
    r.update({
        "Year": "2025", "Quarter": "1", "Month": "1", "DayofMonth": "15", "DayOfWeek": "3",
        "FlightDate": "2025-01-15", "Reporting_Airline": carrier, "Tail_Number": tail,
        "Flight_Number_Reporting_Airline": str(flight), "OriginAirportID": "11298", "Origin": "DFW",
        "OriginCityName": "Dallas/Fort Worth, TX", "OriginState": "TX", "DestAirportID": "13930",
        "Dest": "ORD", "DestCityName": "Chicago, IL", "DestState": "IL", "CRSDepTime": crs_dep,
        "CRSArrTime": crs_arr, "Cancelled": cancelled, "Diverted": "0.00", "CRSElapsedTime": "135.00",
        "Distance": "802.00", "DepDelay": "-3.00", "ArrDelay": "20.00", "ArrDel15": "1.00",
        "DOT_ID_Reporting_Airline": "19805",
    })
    return r


@pytest.fixture
def tiny_zip(tmp_path):
    rows = [
        _row("AA", "  n123aa ", 1, "0705", "0920"),
        _row("AA", "", 2, "2400", "0215", cancelled="1.00"),
        _row("AA", "N456AA", 3, "1330", "1545"),
        _row("UA", "N999UA", 4, "0800", "1000"),
        _row("DL", "N111DL", 5, "0900", "1100"),
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), quoting=csv.QUOTE_NONNUMERIC)
    w.writeheader()
    w.writerows(rows)
    z = tmp_path / "bts_ontime_2025_01.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("On_Time_Test.csv", buf.getvalue())
    return z


@pytest.fixture
def built(tiny_zip, tmp_path):
    part = tmp_path / "part.parquet"
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    log = build_month(connect(), tiny_zip, part, tmp)
    df = duckdb.sql(f"SELECT * FROM read_parquet('{part.as_posix()}') ORDER BY Flight_Number_Reporting_Airline").df()
    return log, df


def test_only_aa_rows_and_counts(built):
    log, df = built
    assert set(df["Reporting_Airline"]) == {"AA"}
    assert log["rows_all_carriers"] == 5 and log["rows_aa"] == 3


def test_expected_columns_in_order(built):
    _, df = built
    assert list(df.columns) == EXPECTED_COLUMNS


def test_tail_number_normalised(built):
    _, df = built
    assert df["Tail_Number"].iloc[0] == "N123AA"  # trimmed and uppercased
    assert df["Tail_Number"].isna().iloc[1]  # empty -> null
    assert df["Tail_Number"].iloc[2] == "N456AA"


def test_hhmm_minutes_and_types(built):
    log, df = built
    assert df["crs_dep_min"].tolist() == [425, 1440, 810]
    assert df["crs_arr_min"].tolist() == [560, 135, 945]
    assert df["Cancelled"].tolist() == [0, 1, 0]
    assert str(df["FlightDate"].dtype).startswith("datetime64")
    assert log["cast_failures"] == 0


@pytest.mark.skipif(not base_table_path().exists(), reason="base table not built on this machine")
def test_real_base_table_is_aa_only_with_expected_columns():
    p = base_table_path().as_posix()
    cols = [r[0] for r in duckdb.sql(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    assert cols == EXPECTED_COLUMNS
    carriers = duckdb.sql(f"SELECT DISTINCT Reporting_Airline FROM read_parquet('{p}')").fetchall()
    assert carriers == [("AA",)]
