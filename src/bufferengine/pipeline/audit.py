"""Reusable audit functions for the AA base table (used by notebooks/01_data_audit).

All functions take a DuckDB connection on which ``open_base`` has registered the view ``base``; the
rotation and time checks also use ``legs`` (registered by ``register_legs``), which adds UTC times.
Everything returns pandas DataFrames so the notebook can display or plot them.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from bufferengine.config import base_table_path, reference_dir
from bufferengine.pipeline.timeconv import scheduled_times_utc

DELAY_CAUSES = ["CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay"]
DUP_KEY = ["FlightDate", "Flight_Number_Reporting_Airline", "Origin", "Dest", "CRSDepTime"]
OPERATED = "Cancelled = 0 AND Diverted = 0"


def open_base(path: Path | None = None, con: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    con = con or duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")  # display and compare timestamps in UTC regardless of the machine
    p = (path or base_table_path()).as_posix().replace("'", "''")
    con.execute(f"CREATE OR REPLACE VIEW base AS SELECT * FROM read_parquet('{p}')")
    return con


def q(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


# 1. Volume ---------------------------------------------------------------------------------------


def volume_by_month(con) -> pd.DataFrame:
    return q(con, """
        SELECT Year, Month, COUNT(*) AS rows, COUNT(DISTINCT FlightDate) AS days,
               ROUND(COUNT(*) / COUNT(DISTINCT FlightDate), 0) AS flights_per_day,
               COUNT(DISTINCT Tail_Number) AS tails,
               COUNT(DISTINCT Origin) AS origins
        FROM base GROUP BY ALL ORDER BY Year, Month""")


def volume_by_year(con) -> pd.DataFrame:
    return q(con, """
        SELECT Year, COUNT(*) AS rows, COUNT(DISTINCT FlightDate) AS days,
               ROUND(COUNT(*) / COUNT(DISTINCT FlightDate), 0) AS flights_per_day,
               COUNT(DISTINCT Tail_Number) AS tails,
               COUNT(DISTINCT Origin || '-' || Dest) AS routes
        FROM base GROUP BY ALL ORDER BY Year""")


def volume_summary(con) -> pd.DataFrame:
    return q(con, """
        WITH ap AS (SELECT Origin AS a FROM base UNION SELECT Dest FROM base),
             daily AS (SELECT FlightDate, COUNT(*) n FROM base GROUP BY 1)
        SELECT (SELECT COUNT(*) FROM base) AS rows,
               (SELECT MIN(FlightDate) FROM base) AS first_date,
               (SELECT MAX(FlightDate) FROM base) AS last_date,
               (SELECT COUNT(DISTINCT Tail_Number) FROM base) AS unique_tails,
               (SELECT COUNT(*) FROM ap) AS unique_airports,
               (SELECT COUNT(DISTINCT Origin || '-' || Dest) FROM base) AS unique_routes_directed,
               (SELECT ROUND(AVG(n), 0) FROM daily) AS flights_per_day_mean,
               (SELECT MIN(n) FROM daily) AS flights_per_day_min,
               (SELECT MAX(n) FROM daily) AS flights_per_day_max""")


# 2. Missingness ----------------------------------------------------------------------------------


def _status_case() -> str:
    return "CASE WHEN Cancelled = 1 THEN 'cancelled' WHEN Diverted = 1 THEN 'diverted' ELSE 'operated' END"


def missingness(con) -> pd.DataFrame:
    """% missing per column, overall and within operated / cancelled / diverted rows."""
    cols = [r[0] for r in con.execute("DESCRIBE base").fetchall()]
    exprs = ", ".join(f'ROUND(100 * AVG(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END), 2) AS "{c}"' for c in cols)
    by = q(con, f"SELECT {_status_case()} AS status, COUNT(*) AS n, {exprs} FROM base GROUP BY 1")
    overall = q(con, f"SELECT 'all' AS status, COUNT(*) AS n, {exprs} FROM base")
    wide = pd.concat([overall, by]).set_index("status")
    out = wide.drop(columns="n").T
    out.index.name = "column"
    out = out[[c for c in ["all", "operated", "cancelled", "diverted"] if c in out.columns]]
    return out.sort_values("all", ascending=False)


def delay_cause_pattern(con) -> pd.DataFrame:
    """How strictly are delay-cause columns populated only for late arrivals (ArrDelay >= 15)?"""
    any_cause = " OR ".join(f'"{c}" IS NOT NULL' for c in DELAY_CAUSES)
    cause_sum = " + ".join(f'COALESCE("{c}", 0)' for c in DELAY_CAUSES)
    return q(con, f"""
        SELECT CASE WHEN ArrDelay >= 15 THEN 'ArrDelay >= 15' ELSE 'ArrDelay < 15' END AS arrival,
               COUNT(*) AS flights,
               SUM(CASE WHEN {any_cause} THEN 1 ELSE 0 END) AS with_causes,
               ROUND(100 * AVG(CASE WHEN {any_cause} THEN 1 ELSE 0 END), 3) AS pct_with_causes,
               ROUND(100 * AVG(CASE WHEN ({any_cause}) AND ABS(({cause_sum}) - ArrDelay) < 0.5 THEN 1
                                    WHEN ({any_cause}) THEN 0 END), 2) AS pct_causes_sum_to_arrdelay
        FROM base WHERE {OPERATED} AND ArrDelay IS NOT NULL
        GROUP BY 1 ORDER BY 1 DESC""")


# 3. Quality --------------------------------------------------------------------------------------


def duplicates(con) -> pd.DataFrame:
    key = ", ".join(f'"{c}"' for c in DUP_KEY)
    return q(con, f"""
        WITH g AS (SELECT {key}, COUNT(*) n FROM base GROUP BY ALL HAVING COUNT(*) > 1)
        SELECT COUNT(*) AS duplicate_keys, COALESCE(SUM(n), 0) AS rows_in_duplicate_keys,
               COALESCE(SUM(n - 1), 0) AS excess_rows FROM g""")


def duplicate_examples(con, n: int = 10) -> pd.DataFrame:
    key = ", ".join(f'"{c}"' for c in DUP_KEY)
    return q(con, f"""
        SELECT {key}, Tail_Number, Cancelled, Diverted, DepTime, ArrTime FROM base
        WHERE ({key}) IN (SELECT ({key}) FROM base GROUP BY ALL HAVING COUNT(*) > 1)
        ORDER BY {key} LIMIT {n}""")


IMPOSSIBLE_CHECKS: dict[str, str] = {
    "Distance <= 0": "Distance <= 0",
    "CRSElapsedTime <= 0": "CRSElapsedTime <= 0",
    "CRSDepTime not a valid hhmm": "crs_dep_min IS NULL",
    "CRSArrTime not a valid hhmm": "crs_arr_min IS NULL",
    "DepDelay < -60 min (very early)": "DepDelay < -60",
    "DepDelay > 24 h": "DepDelay > 1440",
    "ArrDelay > 24 h": "ArrDelay > 1440",
    "ArrDel15 inconsistent with ArrDelay": "ArrDel15 IS NOT NULL AND ArrDel15 <> CAST(ArrDelay >= 15 AS INT)",
    "ArrDelayMinutes <> max(ArrDelay, 0)": "ABS(ArrDelayMinutes - GREATEST(ArrDelay, 0)) > 0.5",
    "AirTime > ActualElapsedTime": "AirTime > ActualElapsedTime",
    "TaxiOut > 180 min": "TaxiOut > 180",
    "Cancelled but has ArrTime": "Cancelled = 1 AND ArrTime IS NOT NULL",
    "Operated but ArrDelay missing": f"{OPERATED} AND ArrDelay IS NULL",
    "Origin = Dest": "Origin = Dest",
}


def impossible_values(con) -> pd.DataFrame:
    exprs = ", ".join(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) AS \"{name}\"" for name, cond in IMPOSSIBLE_CHECKS.items())
    row = q(con, f"SELECT COUNT(*) AS n, {exprs} FROM base").iloc[0]
    n = row.pop("n")
    out = row.rename("rows").to_frame()
    out["pct"] = (100 * out["rows"] / n).round(4)
    out.index.name = "check"
    return out


def numeric_ranges(con) -> pd.DataFrame:
    cols = ["DepDelay", "ArrDelay", "TaxiOut", "TaxiIn", "CRSElapsedTime", "AirTime", "Distance", *DELAY_CAUSES]
    parts = [
        f"SELECT '{c}' AS col, MIN(\"{c}\") min, QUANTILE_CONT(\"{c}\", 0.5) p50, QUANTILE_CONT(\"{c}\", 0.99) p99, "
        f"MAX(\"{c}\") max FROM base" for c in cols
    ]
    return q(con, " UNION ALL ".join(parts))


def status_rates(con) -> pd.DataFrame:
    return q(con, """
        SELECT Year, Month, COUNT(*) AS rows,
               ROUND(100 * AVG(Cancelled), 2) AS pct_cancelled,
               ROUND(100 * AVG(Diverted), 2) AS pct_diverted,
               ROUND(100 * AVG(CASE WHEN Tail_Number IS NULL THEN 1 ELSE 0 END), 2) AS pct_missing_tail
        FROM base GROUP BY ALL ORDER BY Year, Month""")


def overall_rates(con) -> pd.DataFrame:
    return q(con, """
        SELECT COUNT(*) AS rows,
               ROUND(100 * AVG(Cancelled), 3) AS pct_cancelled,
               ROUND(100 * AVG(Diverted), 3) AS pct_diverted,
               ROUND(100 * AVG(CASE WHEN Tail_Number IS NULL THEN 1 ELSE 0 END), 3) AS pct_missing_tail,
               ROUND(100 * AVG(CASE WHEN Tail_Number IS NULL THEN 1 ELSE 0 END) FILTER (WHERE Cancelled = 1), 2)
                 AS pct_missing_tail_among_cancelled,
               ROUND(100 * AVG(CASE WHEN Tail_Number IS NULL THEN 1 ELSE 0 END) FILTER (WHERE Cancelled = 0), 3)
                 AS pct_missing_tail_among_not_cancelled
        FROM base""")


def cancellation_codes(con) -> pd.DataFrame:
    return q(con, """
        SELECT CancellationCode,
               CASE CancellationCode WHEN 'A' THEN 'Carrier' WHEN 'B' THEN 'Weather'
                    WHEN 'C' THEN 'National Air System' WHEN 'D' THEN 'Security' END AS meaning,
               COUNT(*) AS rows, ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
        FROM base WHERE Cancelled = 1 GROUP BY ALL ORDER BY rows DESC""")


# 4. Delay profile --------------------------------------------------------------------------------


def delay_profile(con, by: str = "month") -> pd.DataFrame:
    """ArrDel15 rate among operated flights; also the on-time rate under two denominators."""
    keys = "Year, Month" if by == "month" else "Year"
    return q(con, f"""
        SELECT {keys}, COUNT(*) FILTER (WHERE {OPERATED}) AS operated_flights,
               ROUND(100 * AVG(ArrDel15) FILTER (WHERE {OPERATED}), 2) AS pct_arr_del15,
               ROUND(100 - 100 * AVG(ArrDel15) FILTER (WHERE {OPERATED}), 2) AS pct_on_time_operated,
               ROUND(100 * AVG(CASE WHEN {OPERATED} AND ArrDel15 = 0 THEN 1 ELSE 0 END), 2)
                 AS pct_on_time_of_scheduled
        FROM base GROUP BY ALL ORDER BY {keys}""")


# 5 & 7. Legs with UTC times ----------------------------------------------------------------------


def load_airports_tz(path: Path | None = None) -> pd.DataFrame:
    return pd.read_csv(path or reference_dir() / "airports_tz.csv")


def register_legs(con, airports_tz: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add scheduled UTC times to every base row and register the result as DuckDB view ``legs``."""
    tz = (airports_tz if airports_tz is not None else load_airports_tz()).set_index("iata")["timezone"]
    df = q(con, """
        SELECT FlightDate, Tail_Number, Flight_Number_Reporting_Airline AS flight_no, Origin, Dest,
               CRSDepTime, CRSArrTime, crs_dep_min, crs_arr_min, CRSElapsedTime, DepDelay, ArrDelay,
               ArrDel15, Cancelled, Diverted, LateAircraftDelay
        FROM base""")
    df["origin_tz"] = df["Origin"].map(tz)
    df["dest_tz"] = df["Dest"].map(tz)
    times = scheduled_times_utc(df["FlightDate"], df["crs_dep_min"], df["crs_arr_min"], df["origin_tz"], df["dest_tz"])
    legs = pd.concat([df, times], axis=1)
    con.register("legs_df", legs)
    con.execute("CREATE OR REPLACE VIEW legs AS SELECT * FROM legs_df")
    return legs


def utc_block_check(con) -> pd.DataFrame:
    """Does the UTC-derived scheduled block time equal BTS CRSElapsedTime?"""
    return q(con, """
        SELECT COUNT(*) AS rows,
               COUNT(*) FILTER (WHERE crs_block_min_utc IS NULL) AS no_utc,
               ROUND(100 * AVG(CASE WHEN ABS(crs_block_min_utc - CRSElapsedTime) < 0.5 THEN 1 ELSE 0 END)
                     FILTER (WHERE crs_block_min_utc IS NOT NULL), 3) AS pct_exact_match,
               COUNT(*) FILTER (WHERE ABS(crs_block_min_utc - CRSElapsedTime) >= 0.5) AS mismatches,
               COUNT(*) FILTER (WHERE arr_day_offset = 1) AS overnight_arrivals,
               COUNT(*) FILTER (WHERE arr_day_offset >= 2) AS arrivals_two_plus_days
        FROM legs""")


def utc_block_mismatches(con, n: int = 15) -> pd.DataFrame:
    return q(con, f"""
        SELECT FlightDate, flight_no, Origin, Dest, origin_tz, dest_tz, CRSDepTime, CRSArrTime, CRSElapsedTime,
               crs_block_min_utc, crs_block_min_utc - CRSElapsedTime AS diff_min, COUNT(*) OVER () AS total
        FROM legs WHERE ABS(crs_block_min_utc - CRSElapsedTime) >= 0.5
        ORDER BY ABS(crs_block_min_utc - CRSElapsedTime) DESC LIMIT {n}""")


def mismatch_by_date(con, n: int = 10) -> pd.DataFrame:
    return q(con, f"""
        SELECT FlightDate, COUNT(*) AS mismatches, ROUND(AVG(crs_block_min_utc - CRSElapsedTime), 1) AS mean_diff
        FROM legs WHERE ABS(crs_block_min_utc - CRSElapsedTime) >= 0.5
        GROUP BY 1 ORDER BY mismatches DESC LIMIT {n}""")


def worked_examples(con) -> pd.DataFrame:
    """One example each: overnight, DST spring-forward and fall-back dates, Hawaii, Arizona."""
    cols = ("FlightDate, flight_no, Origin, Dest, origin_tz, dest_tz, CRSDepTime, CRSArrTime, crs_dep_utc, "
            "crs_arr_utc, arr_day_offset, CRSElapsedTime, crs_block_min_utc")
    picks = {
        "overnight (red-eye)": "arr_day_offset = 1 AND Origin = 'LAX' AND Dest = 'JFK'",
        "DST start 2025-03-09": "FlightDate = DATE '2025-03-09' AND crs_dep_min < 360 AND arr_day_offset = 0",
        "night before DST start": "FlightDate = DATE '2025-03-08' AND arr_day_offset = 1",
        "DST end 2025-11-02": "FlightDate = DATE '2025-11-02' AND crs_dep_min < 360",
        "DST start 2026-03-08": "FlightDate = DATE '2026-03-08' AND crs_dep_min < 420",
        "Hawaii (no DST)": "Origin = 'HNL' AND Dest = 'DFW'",
        "Arizona (no DST), summer": "Origin = 'PHX' AND Dest = 'LAX' AND Month(FlightDate) = 7",
        "Arizona (no DST), winter": "Origin = 'PHX' AND Dest = 'LAX' AND Month(FlightDate) = 1",
    }
    frames = []
    for label, cond in picks.items():
        f = q(con, f"SELECT '{label}' AS example, {cols} FROM legs WHERE {cond} ORDER BY FlightDate, crs_dep_min LIMIT 1")
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


# 5. Rotation readiness ---------------------------------------------------------------------------


def _pairs_sql() -> str:
    """Consecutive legs per tail-day (ordered by scheduled UTC departure), with break classification."""
    return """
        WITH o AS (
          SELECT *, LEAD(Origin) OVER w AS next_origin, LEAD(Cancelled) OVER w AS next_cancelled,
                 LEAD(Diverted) OVER w AS next_diverted, LEAD(crs_dep_utc) OVER w AS next_dep_utc,
                 LEAD(crs_dep_min) OVER w AS next_dep_min,
                 ROW_NUMBER() OVER w AS leg_idx, COUNT(*) OVER (PARTITION BY Tail_Number, FlightDate) AS legs
          FROM legs WHERE Tail_Number IS NOT NULL
          WINDOW w AS (PARTITION BY Tail_Number, FlightDate ORDER BY crs_dep_utc, crs_dep_min, flight_no))
        SELECT *, Dest = next_origin AS continuous,
               CASE WHEN Dest = next_origin THEN 'continuous'
                    WHEN Cancelled = 1 OR next_cancelled = 1 THEN 'cancellation'
                    WHEN Diverted = 1 OR next_diverted = 1 THEN 'diversion'
                    ELSE 'unexplained (repositioning / leg not in BTS / tail swap)' END AS pair_status,
               DATEDIFF('minute', crs_arr_utc, next_dep_utc) AS sched_ground_min
        FROM o WHERE next_origin IS NOT NULL"""


def legs_per_tail_day(con) -> pd.DataFrame:
    return q(con, """
        WITH t AS (SELECT Tail_Number, FlightDate, COUNT(*) AS all_legs, SUM(1 - Cancelled) AS operated_legs
                   FROM legs WHERE Tail_Number IS NOT NULL GROUP BY 1, 2)
        SELECT legs, SUM(all_n) AS tail_days_scheduled, SUM(op_n) AS tail_days_operated FROM (
          SELECT all_legs AS legs, 1 AS all_n, 0 AS op_n FROM t
          UNION ALL SELECT operated_legs, 0, 1 FROM t WHERE operated_legs > 0)
        GROUP BY 1 ORDER BY 1""")


def chain_continuity(con) -> pd.DataFrame:
    return q(con, f"""
        SELECT pair_status, COUNT(*) AS pairs, ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 3) AS pct
        FROM ({_pairs_sql()}) GROUP BY 1 ORDER BY pairs DESC""")


def continuity_operated_only(con) -> pd.DataFrame:
    """Continuity after removing cancelled legs (the chain the aircraft actually flew)."""
    sql = _pairs_sql().replace("FROM legs WHERE Tail_Number IS NOT NULL",
                               "FROM legs WHERE Tail_Number IS NOT NULL AND Cancelled = 0")
    return q(con, f"""
        SELECT pair_status, COUNT(*) AS pairs, ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 3) AS pct
        FROM ({sql}) GROUP BY 1 ORDER BY pairs DESC""")


def local_vs_utc_order(con) -> pd.DataFrame:
    """How often does ordering legs by local CRSDepTime differ from ordering by UTC?"""
    return q(con, """
        WITH r AS (
          SELECT ROW_NUMBER() OVER (PARTITION BY Tail_Number, FlightDate ORDER BY crs_dep_utc, crs_dep_min, flight_no) a,
                 ROW_NUMBER() OVER (PARTITION BY Tail_Number, FlightDate ORDER BY crs_dep_min, flight_no) b,
                 Tail_Number, FlightDate
          FROM legs WHERE Tail_Number IS NOT NULL)
        SELECT COUNT(DISTINCT (Tail_Number, FlightDate)) AS tail_days,
               COUNT(DISTINCT (Tail_Number, FlightDate)) FILTER (WHERE a <> b) AS tail_days_order_differs
        FROM r""")


def usable_tail_days(con) -> pd.DataFrame:
    """Strict: >= 2 legs, none cancelled/diverted, every pair continuous.
    Relaxed: after dropping cancelled legs, >= 2 legs, none diverted, every pair continuous."""
    def block(where_extra: str, label: str) -> str:
        sql = _pairs_sql()
        if where_extra:
            sql = sql.replace("FROM legs WHERE Tail_Number IS NOT NULL", f"FROM legs WHERE Tail_Number IS NOT NULL {where_extra}")
        return f"""
          SELECT '{label}' AS definition, Tail_Number, FlightDate,
                 BOOL_AND(continuous) AND MAX(GREATEST(Cancelled, next_cancelled)) = 0
                   AND MAX(GREATEST(Diverted, next_diverted)) = 0 AS usable,
                 MAX(legs) AS legs
          FROM ({sql}) GROUP BY Tail_Number, FlightDate"""
    return q(con, f"""
        WITH td AS (SELECT COUNT(*) n, SUM(n_legs) legs FROM (
                      SELECT Tail_Number, FlightDate, COUNT(*) n_legs FROM legs
                      WHERE Tail_Number IS NOT NULL GROUP BY 1, 2)),
             u AS ({block('', 'strict')} UNION ALL {block('AND Cancelled = 0', 'relaxed (cancelled legs removed)')})
        SELECT definition,
               (SELECT n FROM td) AS all_tail_days,
               COUNT(*) AS tail_days_with_2plus_legs,
               SUM(CASE WHEN usable THEN 1 ELSE 0 END) AS usable_tail_days,
               ROUND(100 * SUM(CASE WHEN usable THEN 1 ELSE 0 END) / (SELECT n FROM td), 2) AS pct_of_all_tail_days,
               SUM(CASE WHEN usable THEN legs ELSE 0 END) AS legs_in_usable,
               ROUND(100 * SUM(CASE WHEN usable THEN legs ELSE 0 END) / (SELECT legs FROM td), 2) AS pct_of_all_legs
        FROM u GROUP BY 1 ORDER BY 1 DESC""")


def overnight_continuity(con) -> pd.DataFrame:
    """Does the last operated leg of a tail-day end where the next day's first leg starts?"""
    return q(con, """
        WITH d AS (
          SELECT Tail_Number, FlightDate,
                 ARG_MAX(Dest, crs_dep_utc) AS last_dest, ARG_MIN(Origin, crs_dep_utc) AS first_origin
          FROM legs WHERE Tail_Number IS NOT NULL AND Cancelled = 0 GROUP BY 1, 2),
        n AS (SELECT *, LEAD(first_origin) OVER (PARTITION BY Tail_Number ORDER BY FlightDate) AS next_first,
                     LEAD(FlightDate) OVER (PARTITION BY Tail_Number ORDER BY FlightDate) AS next_date FROM d)
        SELECT COUNT(*) AS consecutive_day_pairs,
               ROUND(100 * AVG(CASE WHEN last_dest = next_first THEN 1 ELSE 0 END), 2) AS pct_overnight_continuous
        FROM n WHERE CAST(next_date AS DATE) = CAST(FlightDate AS DATE) + 1""")


def ground_time_summary(con) -> pd.DataFrame:
    """Scheduled ground time between continuous consecutive operated legs (preview for MTT/buffer)."""
    sql = _pairs_sql().replace("FROM legs WHERE Tail_Number IS NOT NULL",
                               "FROM legs WHERE Tail_Number IS NOT NULL AND Cancelled = 0")
    return q(con, f"""
        SELECT COUNT(*) AS continuous_pairs,
               COUNT(*) FILTER (WHERE sched_ground_min < 0) AS negative_ground,
               COUNT(*) FILTER (WHERE sched_ground_min BETWEEN 0 AND 29) AS under_30,
               QUANTILE_CONT(sched_ground_min, 0.05) AS p05, QUANTILE_CONT(sched_ground_min, 0.25) AS p25,
               QUANTILE_CONT(sched_ground_min, 0.50) AS p50, QUANTILE_CONT(sched_ground_min, 0.75) AS p75,
               QUANTILE_CONT(sched_ground_min, 0.95) AS p95
        FROM ({sql}) WHERE continuous""")


def ground_time_values(con) -> pd.DataFrame:
    sql = _pairs_sql().replace("FROM legs WHERE Tail_Number IS NOT NULL",
                               "FROM legs WHERE Tail_Number IS NOT NULL AND Cancelled = 0")
    return q(con, f"SELECT sched_ground_min FROM ({sql}) WHERE continuous")


# 6. Propagation evidence -------------------------------------------------------------------------


def propagation_summary(con) -> pd.DataFrame:
    total = " + ".join(f'COALESCE("{c}", 0)' for c in DELAY_CAUSES)
    shares = ", ".join(
        f'ROUND(100 * SUM(COALESCE("{c}", 0)) / SUM({total}), 1) AS "share_{c}"' for c in DELAY_CAUSES
    )
    return q(con, f"""
        SELECT COUNT(*) AS late_arrivals,
               ROUND(100 * AVG(CASE WHEN LateAircraftDelay > 0 THEN 1 ELSE 0 END), 2) AS pct_with_late_aircraft,
               ROUND(AVG(LateAircraftDelay), 1) AS mean_late_aircraft_min,
               {shares}
        FROM base WHERE {OPERATED} AND ArrDel15 = 1""")


def late_aircraft_by_hour(con) -> pd.DataFrame:
    return q(con, f"""
        SELECT crs_dep_min // 60 AS dep_hour,
               COUNT(*) FILTER (WHERE {OPERATED}) AS operated,
               ROUND(100 * AVG(ArrDel15) FILTER (WHERE {OPERATED}), 2) AS pct_arr_del15,
               ROUND(AVG(COALESCE(LateAircraftDelay, 0)) FILTER (WHERE {OPERATED}), 2) AS mean_late_aircraft_all,
               ROUND(AVG(LateAircraftDelay) FILTER (WHERE {OPERATED} AND ArrDel15 = 1), 1) AS mean_late_aircraft_late,
               ROUND(100 * AVG(CASE WHEN LateAircraftDelay > 0 THEN 1 ELSE 0 END)
                     FILTER (WHERE {OPERATED} AND ArrDel15 = 1), 1) AS pct_late_with_late_aircraft
        FROM base GROUP BY 1 ORDER BY 1""")


def late_aircraft_by_leg_index(con) -> pd.DataFrame:
    """Late-aircraft delay by leg position in the tail-day (operated legs, UTC order)."""
    return q(con, f"""
        WITH l AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY Tail_Number, FlightDate ORDER BY crs_dep_utc, flight_no) idx
                   FROM legs WHERE Tail_Number IS NOT NULL AND Cancelled = 0)
        SELECT LEAST(idx, 7) AS leg_index, COUNT(*) AS legs,
               ROUND(100 * AVG(ArrDel15) FILTER (WHERE Diverted = 0), 2) AS pct_arr_del15,
               ROUND(AVG(COALESCE(LateAircraftDelay, 0)) FILTER (WHERE Diverted = 0), 2) AS mean_late_aircraft_all
        FROM l GROUP BY 1 ORDER BY 1""")
