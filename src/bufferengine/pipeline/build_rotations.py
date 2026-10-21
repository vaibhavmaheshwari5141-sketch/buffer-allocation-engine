"""Build the rotation tables from the Phase 1 base table (data contract v1.1).

Outputs, all under DATA_ROOT/processed:
  rotations.parquet          one row per base flight: category, chain position, times, MTT and buffer
  rotation_turns.parquet     one row per consecutive leg pair inside a usable segment
  rotation_breaks.parquet    consecutive aircraft flights that were NOT linked for a flight-level reason
  mtt_sensitivity.parquet    planned buffer re-scored with MTT estimated at several percentiles
  mtt_estimates.csv, rotation_reconciliation.csv, rotation_rule_comparison.csv   small tables (committable)

Rules (docs/decisions.md, Phase 2):
  * Chains use OPERATED flights only. Cancelled and diverted flights stay in the aircraft's sequence at their
    scheduled position but END a chain.
  * Per aircraft, flights are ordered by scheduled UTC departure. Two consecutive flights are linked only if
    previous Dest = next Origin AND next scheduled departure >= previous scheduled arrival (UTC). A pair that
    fails the second test is a tail swap ("swap_suspect"): the chain breaks and the pair is counted.
  * Operating day: a new operating day starts when the scheduled ground time exceeds REST_GAP_MIN (the aircraft
    rests, so delay does not realistically carry over). A leg that crosses midnight, and an early leg that
    follows a short turn after it, stay in the same operating day. ``operating_day`` is the local FlightDate of
    the first flight of the run.
  * A segment is a maximal run of linked operated legs. Segments of 2+ legs are usable.

Usage:
    python -m bufferengine.pipeline.build_rotations
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from bufferengine.config import base_table_path, processed_dir, reference_dir
from bufferengine.pipeline.mtt import (
    SENSITIVITY_PERCENTILES,
    MttSettings,
    add_station_class,
    apply_mtt,
    estimate_mtt,
    tod_band,
)
from bufferengine.pipeline.timeconv import scheduled_times_utc

REST_GAP_MIN = 300  # minutes of scheduled ground time that start a new operating day (data valley 240-300)
TRAIN_END = date(2025, 9, 30)
VALIDATE_END = date(2025, 12, 31)
STORM_START, STORM_END = date(2026, 1, 24), date(2026, 1, 28)
HIGH_CANCEL_THRESHOLD = 0.10  # share of a day's flights cancelled

CATEGORIES = ["usable_segment", "single_leg_segment", "cancelled", "diverted", "missing_tail", "other"]
LINK_BREAKS = ["cancelled", "diverted", "airport_mismatch", "swap_suspect"]  # reasons stored in rotation_breaks
BUFFER_BUCKETS = ["0", "1-15", "16-30", "31-60", "60+"]

LEGS_FILE = "rotations.parquet"
TURNS_FILE = "rotation_turns.parquet"
BREAKS_FILE = "rotation_breaks.parquet"
MTT_SENS_FILE = "mtt_sensitivity.parquet"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    return con


def _sql_path(p: Path) -> str:
    return p.as_posix().replace("'", "''")


def _split_sql(col: str) -> str:
    return (f"CASE WHEN {col} <= DATE '{TRAIN_END}' THEN 'train' "
            f"WHEN {col} <= DATE '{VALIDATE_END}' THEN 'validate' ELSE 'test' END")


def _storm_sql(col: str) -> str:
    return f"({col} BETWEEN DATE '{STORM_START}' AND DATE '{STORM_END}')"


# 1. Flights with UTC times -------------------------------------------------------------------------


def load_flights(base_path: Path | None = None, airports_path: Path | None = None) -> pd.DataFrame:
    """Every base flight with a stable flight_id, scheduled UTC/local times and a node status."""
    base_path = base_path or base_table_path()
    airports_path = airports_path or reference_dir() / "airports_tz.csv"
    con = connect()
    df = con.execute(f"""
        SELECT FlightDate AS flight_date, Tail_Number AS tail_number, Flight_Number_Reporting_Airline AS flight_number,
               Origin AS origin, Dest AS dest, CRSDepTime AS crs_dep_time, CRSArrTime AS crs_arr_time,
               crs_dep_min, crs_arr_min, CRSElapsedTime AS crs_block_min, Distance AS distance_mi,
               DepDelay AS dep_delay_min, ArrDelay AS arr_delay_min, ArrDel15 AS arr_del15,
               LateAircraftDelay AS late_aircraft_delay_min, Cancelled AS cancelled, Diverted AS diverted
        FROM read_parquet('{_sql_path(base_path)}')""").df()
    tz = pd.read_csv(airports_path).set_index("iata")["timezone"]
    df["origin_tz"] = df["origin"].map(tz)
    df["dest_tz"] = df["dest"].map(tz)
    t = scheduled_times_utc(df["flight_date"], df["crs_dep_min"], df["crs_arr_min"], df["origin_tz"], df["dest_tz"])
    df["crs_dep_utc"] = t["crs_dep_utc"]
    df["crs_arr_utc"] = t["crs_arr_utc"]
    df["crs_dep_local"] = t["crs_dep_local"]
    df["crs_arr_local"] = t["crs_arr_local"]
    df["arr_day_offset"] = t["arr_day_offset"]
    df["flight_date"] = pd.to_datetime(df["flight_date"]).dt.date
    df["flight_id"] = (
        pd.to_datetime(df["flight_date"]).dt.strftime("%Y%m%d") + "-" + df["flight_number"].astype(str) + "-"
        + df["origin"] + "-" + df["dest"] + "-" + df["crs_dep_time"].astype(str).str.zfill(4)
    )
    df["in_sequence"] = df["tail_number"].notna() & df["crs_dep_utc"].notna() & df["crs_arr_utc"].notna()
    df["node_status"] = np.select(
        [df["cancelled"] == 1, df["diverted"] == 1], ["cancelled", "diverted"], default="operated"
    )
    return df.drop(columns=["crs_dep_min", "crs_arr_min"])


# 2. Chains -----------------------------------------------------------------------------------------

_ORDER = "ORDER BY crs_dep_utc, flight_number, flight_id"


def chain_legs(
    con: duckdb.DuckDBPyConnection, flights: pd.DataFrame, rest_gap_min: float = REST_GAP_MIN
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (legs, breaks) for the given flights (see the module docstring for the rules)."""
    con.register("flights_df", flights)
    con.execute("CREATE OR REPLACE TEMP TABLE flights_t AS SELECT * FROM flights_df")
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE nodes AS
    WITH s AS (
      SELECT *, LAG(flight_id) OVER w AS prev_flight_id, LAG(dest) OVER w AS prev_dest,
             LAG(crs_arr_utc) OVER w AS prev_arr, LAG(node_status) OVER w AS prev_status
      FROM flights_t WHERE in_sequence
      WINDOW w AS (PARTITION BY tail_number {_ORDER})
    ), r AS (
      SELECT *, (epoch(crs_dep_utc) - epoch(prev_arr)) / 60.0 AS gap_before_min,
        CASE WHEN prev_flight_id IS NULL THEN 'first_flight'
             WHEN (epoch(crs_dep_utc) - epoch(prev_arr)) / 60.0 > {rest_gap_min} THEN 'long_rest'
             WHEN node_status = 'cancelled' OR prev_status = 'cancelled' THEN 'cancelled'
             WHEN node_status = 'diverted' OR prev_status = 'diverted' THEN 'diverted'
             WHEN prev_dest <> origin THEN 'airport_mismatch'
             WHEN crs_dep_utc < prev_arr THEN 'swap_suspect'
             ELSE 'linked' END AS reason_before
      FROM s
    ), d AS (
      SELECT *,
        SUM(CASE WHEN reason_before IN ('first_flight', 'long_rest') THEN 1 ELSE 0 END)
          OVER (PARTITION BY tail_number {_ORDER} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS day_no,
        SUM(CASE WHEN reason_before <> 'linked' THEN 1 ELSE 0 END)
          OVER (PARTITION BY tail_number {_ORDER} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS seg_no,
        LEAD(reason_before) OVER (PARTITION BY tail_number {_ORDER}) AS reason_next
      FROM r
    )
    SELECT *,
      FIRST_VALUE(flight_date) OVER (PARTITION BY tail_number, day_no {_ORDER}) AS operating_day,
      COUNT(*) OVER (PARTITION BY tail_number, seg_no) AS seg_legs,
      ROW_NUMBER() OVER (PARTITION BY tail_number, seg_no {_ORDER}) AS seg_idx,
      tail_number || '_' || FIRST_VALUE(flight_id) OVER (PARTITION BY tail_number, seg_no {_ORDER}) AS seg_id
    FROM d""")

    daily = con.execute(
        "SELECT flight_date, AVG(cancelled) > ? AS high_cancel FROM flights_t GROUP BY 1", [HIGH_CANCEL_THRESHOLD]
    ).df()
    con.register("daily_df", daily)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE tail_day AS
    SELECT tail_number, flight_date,
           COUNT(DISTINCT seg_id) FILTER (WHERE node_status = 'operated') AS n_segments
    FROM nodes GROUP BY 1, 2""")
    con.execute("""
    CREATE OR REPLACE TEMP TABLE tail_day_canc AS
    SELECT tail_number, flight_date, SUM(cancelled) AS n_cancelled_in_tail_day
    FROM flights_t WHERE tail_number IS NOT NULL GROUP BY 1, 2""")

    legs = con.execute(f"""
    SELECT CASE WHEN f.tail_number IS NOT NULL THEN f.tail_number || '_' || strftime(f.flight_date, '%Y-%m-%d') END
             AS rotation_id,
           f.flight_id, f.tail_number, f.flight_date,
           CASE WHEN f.node_status = 'operated' THEN n.operating_day END AS operating_day,
           CASE WHEN f.node_status = 'operated' THEN n.seg_id END AS segment_id,
           CASE WHEN f.node_status = 'operated' THEN n.seg_idx END AS leg_idx,
           CASE WHEN f.node_status = 'operated' THEN n.seg_legs END AS n_legs,
           CASE WHEN f.node_status = 'operated' THEN td.n_segments END AS n_segments,
           CASE WHEN f.node_status = 'operated' THEN n.seg_legs - n.seg_idx END AS n_downstream_legs,
           tc.n_cancelled_in_tail_day,
           f.flight_number, f.origin, f.dest, f.origin_tz, f.dest_tz, f.crs_dep_time, f.crs_arr_time,
           f.crs_dep_local, f.crs_arr_local, f.crs_dep_utc, f.crs_arr_utc, f.arr_day_offset, f.crs_block_min,
           f.distance_mi,
           CASE WHEN f.node_status = 'operated' THEN f.crs_dep_utc + to_minutes(CAST(f.dep_delay_min AS BIGINT)) END
             AS act_dep_utc,
           CASE WHEN f.node_status = 'operated' THEN f.crs_arr_utc + to_minutes(CAST(f.arr_delay_min AS BIGINT)) END
             AS act_arr_utc,
           f.dep_delay_min, f.arr_delay_min, f.arr_del15, f.late_aircraft_delay_min,
           CASE WHEN f.node_status = 'operated'
                THEN GREATEST(f.arr_delay_min, 0) - COALESCE(f.late_aircraft_delay_min, 0) END AS primary_delay_min,
           f.cancelled, f.diverted,
           CASE WHEN f.cancelled = 1 THEN 'cancelled' WHEN f.diverted = 1 THEN 'diverted'
                WHEN f.tail_number IS NULL THEN 'missing_tail' WHEN NOT f.in_sequence THEN 'other'
                WHEN n.seg_legs >= 2 THEN 'usable_segment' WHEN n.seg_legs = 1 THEN 'single_leg_segment'
                ELSE 'other' END AS category,
           COALESCE(f.node_status = 'operated' AND n.seg_legs >= 2, FALSE) AS usable,
           {_split_sql("f.flight_date")} AS split,
           {_storm_sql("f.flight_date")} AS storm_day_flag,
           COALESCE(h.high_cancel, FALSE) AS high_cancel_day,
           CASE WHEN f.node_status = 'operated' THEN n.reason_before END AS break_reason_before,
           CASE WHEN f.node_status = 'operated' THEN COALESCE(n.reason_next, 'last_flight') END AS break_reason_after
    FROM flights_t f
    LEFT JOIN nodes n USING (flight_id)
    LEFT JOIN tail_day td ON td.tail_number = f.tail_number AND td.flight_date = f.flight_date
    LEFT JOIN tail_day_canc tc ON tc.tail_number = f.tail_number AND tc.flight_date = f.flight_date
    LEFT JOIN daily_df h ON h.flight_date = f.flight_date
    ORDER BY f.tail_number NULLS LAST, f.crs_dep_utc, f.flight_number, f.flight_id""").df()

    legs["operating_day"] = pd.to_datetime(legs["operating_day"]).dt.date
    breaks = con.execute(f"""
    SELECT n.flight_id AS next_flight_id, n.prev_flight_id, n.tail_number, n.reason_before AS reason,
           CASE WHEN n.prev_status <> 'operated' AND n.node_status <> 'operated' THEN 'both'
                WHEN n.prev_status <> 'operated' THEN 'prev' WHEN n.node_status <> 'operated' THEN 'next'
                ELSE 'neither' END AS non_operated_side,
           n.prev_dest, n.origin AS next_origin, n.dest AS next_dest, n.gap_before_min AS gap_min,
           n.prev_dest <> n.origin AS airport_mismatch, n.gap_before_min < 0 AS negative_gap,
           n.flight_date, {_split_sql("n.flight_date")} AS split,
           {_storm_sql("n.flight_date")} AS storm_day_flag,
           COALESCE(h.high_cancel, FALSE) AS high_cancel_day
    FROM nodes n LEFT JOIN daily_df h ON h.flight_date = n.flight_date
    WHERE n.reason_before IN ({", ".join(repr(r) for r in LINK_BREAKS)})
    ORDER BY n.tail_number, n.crs_dep_utc, n.flight_id""").df()
    return legs, breaks


# 3. Turns ------------------------------------------------------------------------------------------


def build_turns(con: duckdb.DuckDBPyConnection, legs: pd.DataFrame) -> pd.DataFrame:
    """One row per consecutive leg pair inside a usable segment (MTT columns are added by score_turns)."""
    con.register("usable_df", legs[legs["usable"]])
    return con.execute(f"""
    WITH p AS (
      SELECT segment_id, tail_number, operating_day, dest AS station, dest_tz AS station_tz,
             flight_id AS inbound_flight_id, LEAD(flight_id) OVER w AS outbound_flight_id,
             leg_idx AS turn_index_in_segment, n_legs,
             crs_arr_utc AS in_arr_utc, LEAD(crs_dep_utc) OVER w AS out_dep_utc,
             act_arr_utc AS in_act_arr_utc, LEAD(act_dep_utc) OVER w AS out_act_dep_utc,
             flight_date AS inbound_flight_date, LEAD(flight_date) OVER w AS outbound_flight_date,
             dep_delay_min AS inbound_dep_delay_min, arr_delay_min AS inbound_arr_delay_min,
             LEAD(dep_delay_min) OVER w AS outbound_dep_delay_min,
             LEAD(arr_delay_min) OVER w AS outbound_arr_delay_min,
             LEAD(late_aircraft_delay_min) OVER w AS late_aircraft_delay_outbound_min,
             crs_arr_local AS inbound_sched_arr_local, LEAD(crs_dep_local) OVER w AS outbound_sched_dep_local
      FROM usable_df WINDOW w AS (PARTITION BY segment_id ORDER BY leg_idx)
    )
    SELECT inbound_flight_id || '>' || outbound_flight_id AS turn_id, tail_number, operating_day, segment_id,
           station, station_tz, inbound_flight_id, outbound_flight_id, turn_index_in_segment,
           n_legs - turn_index_in_segment AS n_downstream_legs_after_turn,
           (epoch(out_dep_utc) - epoch(in_arr_utc)) / 60.0 AS sched_ground_min,
           (epoch(out_act_dep_utc) - epoch(in_act_arr_utc)) / 60.0 AS actual_ground_min,
           inbound_dep_delay_min, inbound_arr_delay_min, outbound_dep_delay_min, outbound_arr_delay_min,
           late_aircraft_delay_outbound_min, inbound_sched_arr_local, outbound_sched_dep_local,
           inbound_flight_date, outbound_flight_date, {_split_sql("outbound_flight_date")} AS split,
           {_storm_sql("outbound_flight_date")} AS storm_day_flag,
           COALESCE(h.high_cancel, FALSE) AS high_cancel_day
    FROM p LEFT JOIN daily_df h ON h.flight_date = p.outbound_flight_date
    WHERE outbound_flight_id IS NOT NULL
    ORDER BY tail_number, out_dep_utc, turn_id""").df()


def buffer_bucket(planned_buffer_min: pd.Series) -> pd.Series:
    b = pd.cut(planned_buffer_min, bins=[-np.inf, 0, 15, 30, 60, np.inf], labels=BUFFER_BUCKETS, right=True)
    return b.astype("string")


def score_turns(turns: pd.DataFrame, settings: MttSettings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add time-of-day band, station class, MTT and planned buffer. Returns (turns, mtt_estimates)."""
    t = turns.copy()
    t["tod_band"] = tod_band(pd.to_datetime(t["outbound_sched_dep_local"]).dt.hour)
    t["flag_actual_ground_nonpositive"] = t["actual_ground_min"] <= 0
    t = add_station_class(t, settings)
    est = estimate_mtt(t, settings)
    t = apply_mtt(t, est, settings)
    t["flag_below_mtt"] = t["sched_ground_min"] < t["mtt_min"]
    t["planned_buffer_min"] = (t["sched_ground_min"] - t["mtt_min"]).clip(lower=0)
    t["buffer_bucket"] = buffer_bucket(t["planned_buffer_min"])
    return t, est


def mtt_sensitivity(turns: pd.DataFrame, settings: MttSettings) -> pd.DataFrame:
    """Planned buffer under each sensitivity percentile (wide: one row per turn)."""
    out = turns[["turn_id", "split", "sched_ground_min"]].copy()
    for pct in SENSITIVITY_PERCENTILES:
        s = settings.with_percentile(pct)
        scored = apply_mtt(turns.drop(columns=["mtt_min", "mtt_source"]), estimate_mtt(turns, s), s)
        label = f"p{int(round(pct * 100)):02d}"
        out[f"mtt_{label}_min"] = scored["mtt_min"].to_numpy()
        out[f"planned_buffer_{label}_min"] = (out["sched_ground_min"] - out[f"mtt_{label}_min"]).clip(lower=0)
    return out


def add_turn_columns_to_legs(legs: pd.DataFrame, turns: pd.DataFrame) -> pd.DataFrame:
    """Copy the turn that follows each leg (ground time, MTT, buffer) onto the leg row; set position flags."""
    cols = ["inbound_flight_id", "sched_ground_min", "actual_ground_min", "mtt_min", "planned_buffer_min",
            "flag_below_mtt"]
    t = turns[cols].rename(columns={"inbound_flight_id": "flight_id"})
    out = legs.merge(t, on="flight_id", how="left")
    out["is_first_leg"] = out["leg_idx"] == 1
    out["is_last_leg"] = out["leg_idx"] == out["n_legs"]
    out["flag_overnight_arrival"] = out["arr_day_offset"] > 0
    return out


# 4. Reconciliation ---------------------------------------------------------------------------------


def reconciliation(legs: pd.DataFrame, base_total: int | None = None) -> pd.DataFrame:
    """Every flight in exactly one category. Raises if the counts do not add up."""
    if legs["flight_id"].duplicated().any():
        raise ValueError("flight_id is not unique")
    if legs["category"].isna().any() or not set(legs["category"]).issubset(CATEGORIES):
        raise ValueError("flights without a valid category")
    counts = legs["category"].value_counts().reindex(CATEGORIES, fill_value=0)
    total = int(counts.sum())
    if total != len(legs) or (base_total is not None and total != base_total):
        raise ValueError(f"category counts {total} do not match the base total {base_total or len(legs)}")
    out = counts.rename("flights").to_frame()
    out["pct"] = (100 * out["flights"] / max(total, 1)).round(3)
    out.index.name = "category"
    return out.reset_index()


def rule_comparison(con: duckdb.DuckDBPyConnection, legs: pd.DataFrame) -> pd.DataFrame:
    """Tail-days usable under the old contract v1.0 rule versus the new segment rule.

    Old (v1.0): tail-day (tail + FlightDate) with 2+ operated legs after cancelled legs are REMOVED, no diverted
    leg, and every consecutive pair continuous (previous Dest = next Origin).
    New (v1.1): cancelled/diverted legs and swaps break chains; usable = segment of 2+ legs.
    """
    old = con.execute("""
    WITH o AS (
      SELECT tail_number, flight_date, dest, diverted,
             LEAD(origin) OVER (PARTITION BY tail_number, flight_date
                                ORDER BY crs_dep_utc, flight_number, flight_id) AS next_origin
      FROM flights_t WHERE in_sequence AND cancelled = 0)
    SELECT COUNT(*) AS tail_days, SUM(n) AS legs FROM (
      SELECT tail_number, flight_date, COUNT(*) AS n
      FROM o GROUP BY 1, 2
      HAVING COUNT(*) >= 2 AND MAX(diverted) = 0
         AND BOOL_AND(CASE WHEN next_origin IS NULL THEN TRUE ELSE dest = next_origin END))""").fetchone()
    t = legs[legs["tail_number"].notna()]
    g = t.groupby(["tail_number", "flight_date"]).agg(
        any_usable=("usable", "any"), all_usable=("usable", "all"), n_seg=("segment_id", "nunique"),
        n_canc=("cancelled", "sum"), n_div=("diverted", "sum"), usable_legs=("usable", "sum"),
    )
    whole = g["all_usable"] & (g["n_seg"] == 1) & (g["n_canc"] == 0) & (g["n_div"] == 0)
    rows = [
        ("tail-days with a tail number (all flights)", len(g), len(t)),
        ("OLD v1.0 rule: usable tail-days (cancelled legs removed, all pairs continuous, no diversion)",
         old[0], int(old[1])),
        ("NEW v1.1 rule: tail-days with at least one usable segment", int(g["any_usable"].sum()),
         int(g.loc[g["any_usable"], "usable_legs"].sum())),
        ("NEW v1.1 rule: tail-days that are one single usable segment (comparable to the old rule)",
         int(whole.sum()), int(g.loc[whole, "usable_legs"].sum())),
        ("NEW v1.1 rule: usable segments", int(t.loc[t["usable"], "segment_id"].nunique()), int(t["usable"].sum())),
    ]
    return pd.DataFrame(rows, columns=["rule", "count", "legs"])


# 5. Orchestration ----------------------------------------------------------------------------------


@dataclass
class RotationTables:
    legs: pd.DataFrame
    turns: pd.DataFrame
    breaks: pd.DataFrame
    mtt_estimates: pd.DataFrame
    mtt_sens: pd.DataFrame
    reconciliation: pd.DataFrame
    rule_comparison: pd.DataFrame


def build_all(
    base_path: Path | None = None,
    airports_path: Path | None = None,
    settings: MttSettings | None = None,
    rest_gap_min: float = REST_GAP_MIN,
) -> RotationTables:
    settings = settings or MttSettings()
    flights = load_flights(base_path, airports_path)
    con = connect()
    legs, breaks = chain_legs(con, flights, rest_gap_min)
    rec = reconciliation(legs, base_total=len(flights))
    cmp_ = rule_comparison(con, legs)
    turns = build_turns(con, legs)
    turns, est = score_turns(turns, settings)
    sens = mtt_sensitivity(turns, settings)
    legs = add_turn_columns_to_legs(legs, turns)
    return RotationTables(legs, turns, breaks, est, sens, rec, cmp_)


def _write(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, path: Path) -> None:
    con.register("out_df", df)
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM out_df) TO '{_sql_path(path)}' (FORMAT parquet, COMPRESSION zstd)")
    con.unregister("out_df")


def run(out_dir: Path | None = None, **kwargs) -> RotationTables:
    out_dir = out_dir or processed_dir()
    tables = build_all(**kwargs)
    con = connect()
    _write(con, tables.legs, out_dir / LEGS_FILE)
    _write(con, tables.turns, out_dir / TURNS_FILE)
    _write(con, tables.breaks, out_dir / BREAKS_FILE)
    _write(con, tables.mtt_sens, out_dir / MTT_SENS_FILE)
    tables.mtt_estimates.to_csv(out_dir / "mtt_estimates.csv", index=False)
    tables.reconciliation.to_csv(out_dir / "rotation_reconciliation.csv", index=False)
    tables.rule_comparison.to_csv(out_dir / "rotation_rule_comparison.csv", index=False)
    for name, df in [("rotations", tables.legs), ("turns", tables.turns), ("breaks", tables.breaks)]:
        print(f"{name:10s} {len(df):>10,} rows")
    print(tables.reconciliation.to_string(index=False))
    print(tables.rule_comparison.to_string(index=False))
    return tables


if __name__ == "__main__":
    run()
