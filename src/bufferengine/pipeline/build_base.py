"""Build the AA base table from the raw BTS monthly zips with DuckDB.

Steps per month: extract the CSV to a temp folder, read every column as text, keep Reporting_Airline =
'AA', select the base columns, cast to explicit types, normalise Tail_Number and add minutes-after-
midnight columns, and write a monthly part. Parts are then combined into one parquet file.
No rows are dropped other than the AA filter. Row counts and cast failures are logged.

Usage:
    python -m bufferengine.pipeline.build_base
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from bufferengine.config import base_table_path, interim_dir, raw_dir

CARRIER = "AA"

# Base columns and their DuckDB types, in output order.
BASE_COLUMNS: dict[str, str] = {
    "Year": "SMALLINT",
    "Quarter": "TINYINT",
    "Month": "TINYINT",
    "DayofMonth": "TINYINT",
    "DayOfWeek": "TINYINT",
    "FlightDate": "DATE",
    "Reporting_Airline": "VARCHAR",
    "Tail_Number": "VARCHAR",
    "Flight_Number_Reporting_Airline": "INTEGER",
    "OriginAirportID": "INTEGER",
    "Origin": "VARCHAR",
    "OriginCityName": "VARCHAR",
    "OriginState": "VARCHAR",
    "DestAirportID": "INTEGER",
    "Dest": "VARCHAR",
    "DestCityName": "VARCHAR",
    "DestState": "VARCHAR",
    "CRSDepTime": "SMALLINT",
    "DepTime": "SMALLINT",
    "DepDelay": "DOUBLE",
    "DepDel15": "TINYINT",
    "DepTimeBlk": "VARCHAR",
    "TaxiOut": "DOUBLE",
    "WheelsOff": "SMALLINT",
    "WheelsOn": "SMALLINT",
    "TaxiIn": "DOUBLE",
    "CRSArrTime": "SMALLINT",
    "ArrTime": "SMALLINT",
    "ArrDelay": "DOUBLE",
    "ArrDelayMinutes": "DOUBLE",
    "ArrDel15": "TINYINT",
    "Cancelled": "TINYINT",
    "CancellationCode": "VARCHAR",
    "Diverted": "TINYINT",
    "CRSElapsedTime": "DOUBLE",
    "ActualElapsedTime": "DOUBLE",
    "AirTime": "DOUBLE",
    "Distance": "DOUBLE",
    "CarrierDelay": "DOUBLE",
    "WeatherDelay": "DOUBLE",
    "NASDelay": "DOUBLE",
    "SecurityDelay": "DOUBLE",
    "LateAircraftDelay": "DOUBLE",
}
DERIVED_COLUMNS = {"crs_dep_min": "SMALLINT", "crs_arr_min": "SMALLINT"}
SORT_KEYS = ["FlightDate", "Tail_Number", "CRSDepTime", "Flight_Number_Reporting_Airline"]

# hhmm -> minutes after midnight; 2400 -> 1440; invalid -> NULL. Mirrors timeconv.hhmm_to_minutes.
_HHMM_MACRO = """
CREATE OR REPLACE MACRO hhmm_to_min(x) AS
  CASE WHEN x IS NULL OR x < 0 OR x % 100 >= 60 OR (x // 100 >= 24 AND x <> 2400) THEN NULL
       ELSE (x // 100) * 60 + x % 100 END
"""


def _cast_expr(col: str, typ: str) -> str:
    src = f'NULLIF(TRIM("{col}"), \'\')'
    if col == "Tail_Number":
        return f"NULLIF(UPPER(TRIM(\"{col}\")), '') AS \"{col}\""
    if typ == "VARCHAR":
        return f'{src} AS "{col}"'
    if typ == "DATE":
        return f'TRY_CAST({src} AS DATE) AS "{col}"'
    if typ == "DOUBLE":
        return f'TRY_CAST({src} AS DOUBLE) AS "{col}"'
    # Integer columns: BTS writes flags and some counts as "1.00", so go through DOUBLE.
    return f'TRY_CAST(TRY_CAST({src} AS DOUBLE) AS {typ}) AS "{col}"'


def select_sql(source: str) -> str:
    """SELECT that turns an all-VARCHAR BTS relation into the typed AA base rows."""
    casts = ",\n  ".join(_cast_expr(c, t) for c, t in BASE_COLUMNS.items())
    return f"""
    SELECT *, CAST(hhmm_to_min(CRSDepTime) AS SMALLINT) AS crs_dep_min,
              CAST(hhmm_to_min(CRSArrTime) AS SMALLINT) AS crs_arr_min
    FROM (SELECT
      {casts}
    FROM {source}
    WHERE TRIM(Reporting_Airline) = '{CARRIER}')
    """


def cast_failure_sql(source: str) -> str:
    """Count non-empty source values that failed to cast, per typed column (AA rows only)."""
    checks = []
    for c, t in BASE_COLUMNS.items():
        if t == "VARCHAR":
            continue
        target = "DATE" if t == "DATE" else "DOUBLE"
        checks.append(
            f"SUM(CASE WHEN NULLIF(TRIM(\"{c}\"), '') IS NOT NULL "
            f"AND TRY_CAST(TRIM(\"{c}\") AS {target}) IS NULL THEN 1 ELSE 0 END) AS \"{c}\""
        )
    return f"SELECT {', '.join(checks)} FROM {source} WHERE TRIM(Reporting_Airline) = '{CARRIER}'"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(_HHMM_MACRO)
    return con


def build_month(con: duckdb.DuckDBPyConnection, zip_path: Path, part_path: Path, tmp: Path) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        csv_path = Path(zf.extract(member, tmp))
    try:
        csv_sql = csv_path.as_posix().replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW src AS SELECT * FROM read_csv('{csv_sql}', header=true, "
            "all_varchar=true, quote='\"', null_padding=true)"
        )
        missing = [c for c in BASE_COLUMNS if c not in {r[0] for r in con.execute("DESCRIBE src").fetchall()}]
        if missing:
            raise ValueError(f"{zip_path.name}: columns missing from BTS file: {missing}")
        rows_all = con.execute("SELECT COUNT(*) FROM src").fetchone()[0]
        fails = con.execute(cast_failure_sql("src")).df().iloc[0]
        con.execute(f"COPY ({select_sql('src')}) TO '{part_path.as_posix()}' (FORMAT parquet)")
        rows_aa = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(part_path)]).fetchone()[0]
    finally:
        csv_path.unlink(missing_ok=True)
    return {
        "file": zip_path.name,
        "rows_all_carriers": rows_all,
        "rows_aa": rows_aa,
        "cast_failures": int(fails.sum()),
        "cast_failure_cols": ";".join(f"{k}={int(v)}" for k, v in fails.items() if v),
    }


def run(zip_dir: Path | None = None, out_path: Path | None = None) -> pd.DataFrame:
    zip_dir = zip_dir or raw_dir()
    out_path = out_path or base_table_path()
    zips = sorted(zip_dir.glob("bts_ontime_*.zip"))
    if not zips:
        raise FileNotFoundError(f"no bts_ontime_*.zip files in {zip_dir}; run download_bts first")
    parts_dir = interim_dir() / "base_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    con = connect()
    log = []
    with tempfile.TemporaryDirectory(dir=interim_dir()) as tmp:
        for z in zips:
            entry = build_month(con, z, parts_dir / z.with_suffix(".parquet").name, Path(tmp))
            print(f"{z.name}: {entry['rows_all_carriers']:>9,} rows -> {entry['rows_aa']:>7,} AA"
                  f"  cast failures: {entry['cast_failures']}")
            log.append(entry)

    order = ", ".join(f'"{k}"' for k in SORT_KEYS)
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{(parts_dir / '*.parquet').as_posix()}') ORDER BY {order}) "
        f"TO '{out_path.as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 250000)"
    )
    shutil.rmtree(parts_dir)

    log_df = pd.DataFrame(log)
    total = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(out_path)]).fetchone()[0]
    assert total == log_df["rows_aa"].sum(), "combined row count does not match monthly parts"
    log_df.loc[len(log_df)] = {
        "file": "TOTAL", "rows_all_carriers": log_df["rows_all_carriers"].sum(),
        "rows_aa": total, "cast_failures": log_df["cast_failures"].sum(), "cast_failure_cols": "",
    }
    log_df.to_csv(interim_dir() / "build_base_log.csv", index=False)
    print(f"wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB, {total:,} rows)")
    return log_df


if __name__ == "__main__":
    run()
