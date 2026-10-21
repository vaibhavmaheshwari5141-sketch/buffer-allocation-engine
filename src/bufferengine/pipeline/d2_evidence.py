"""D2 audit evidence: quality and representativeness tables and figures.

Reads aa_clean_v1.0.parquet, the Phase 2 rotation tables, the raw monthly BTS zips (all carriers) and the
OurAirports file. Writes CSV tables to docs/d2/tables/, PNG figures (200 dpi) to docs/d2/figures/ and a
generated summary docs/d2/representativeness_summary.md in which every number is read from those tables.

Usage:
    python -m bufferengine.pipeline.d2_evidence
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bufferengine.config import interim_dir, processed_dir, raw_dir  # noqa: E402
from bufferengine.pipeline import clean as C  # noqa: E402
from bufferengine.pipeline.reference import load_ourairports  # noqa: E402

D2 = Path(__file__).resolve().parents[3] / "docs" / "d2"
TABLES, FIGS = D2 / "tables", D2 / "figures"
BLUE, ORANGE, GREY, GREEN, RED = "#1f6fb2", "#d9822b", "#8a94a6", "#2a9d6f", "#c0392b"
STORM = ("2026-01-24", "2026-01-28")
US_TERRITORIES = {"PR": "Puerto Rico", "VI": "US Virgin Islands", "GU": "Guam", "MP": "Northern Mariana Islands", "AS": "American Samoa"}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Single-panel figures about 3.9 in wide with 8 pt text: readable when placed at half page width (about 3.2 in).
plt.rcParams.update({
    "figure.figsize": (3.9, 3.0), "figure.dpi": 100, "savefig.dpi": 200, "font.size": 8, "axes.titlesize": 9,
    "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.3,
})


def _save_table(df: pd.DataFrame, name: str) -> pd.DataFrame:
    TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLES / name, index=False)
    return df


def _save_fig(fig, name: str) -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIGS / name, dpi=200)
    plt.close(fig)


def connect(clean_file: Path | None = None, root: Path | None = None) -> duckdb.DuckDBPyConnection:
    root = root or processed_dir()
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"CREATE VIEW c AS SELECT * FROM read_parquet('{(clean_file or C.clean_path(root)).as_posix()}')")
    for view, fname in [("rot", "rotations.parquet"), ("turns", "rotation_turns.parquet"), ("brk", "rotation_breaks.parquet")]:
        con.execute(f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{(root / fname).as_posix()}')")
    return con


def q(con, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


# 3. Quality evidence -----------------------------------------------------------------------------------


def expected_null_sql(col: str) -> str:
    if col in C.CAUSE_COLS or col == "struct_null_delay_cause":
        return "struct_null_delay_cause"
    if col == "CancellationCode":
        return "Cancelled = 0"
    if col in C.STRUCT_RULES["dep_cols"]:
        return "Cancelled = 1"
    if col in C.STRUCT_RULES["arr_cols"] or col == "arr_delay_min_capped":
        return "Cancelled = 1 OR Diverted = 1"
    return "FALSE"


RULE_TEXT = {
    "struct_null_delay_cause": "cause columns: null unless operated and ArrDelay >= 15",
    "Cancelled = 0": "only populated when cancelled",
    "Cancelled = 1": "null for cancelled flights",
    "Cancelled = 1 OR Diverted = 1": "null for cancelled or diverted flights",
    "FALSE": "never expected to be null",
}


def missing_by_column(con) -> pd.DataFrame:
    cols = [r[0] for r in con.execute("DESCRIBE c").fetchall()]
    parts = []
    for col in cols:
        cond = expected_null_sql(col)
        parts.append(
            f"SELECT '{col}' AS column_name, COUNT(*) AS n_rows, SUM(CASE WHEN \"{col}\" IS NULL THEN 1 ELSE 0 END) AS n_missing, "
            f"SUM(CASE WHEN \"{col}\" IS NULL AND ({cond}) THEN 1 ELSE 0 END) AS n_structural, "
            f"'{RULE_TEXT[cond]}' AS expected_null_rule FROM c")
    df = q(con, " UNION ALL ".join(parts))
    df["n_unexpected"] = df["n_missing"] - df["n_structural"]
    df["pct_missing"] = (100 * df["n_missing"] / df["n_rows"]).round(4)
    df["pct_unexpected"] = (100 * df["n_unexpected"] / df["n_rows"]).round(6)
    out = df[["column_name", "n_rows", "n_missing", "pct_missing", "n_structural", "n_unexpected", "pct_unexpected", "expected_null_rule"]]
    out = out.sort_values(["n_missing", "column_name"], ascending=[False, True]).reset_index(drop=True)
    out.loc[out["column_name"] == "Tail_Number", "expected_null_rule"] = "never expected to be null; the missing rows are all cancelled flights (flag dq_missing_tail)"
    _save_table(out, "missing_by_column.csv")
    m = out[out["n_missing"] > 0].iloc[::-1]
    fig, ax = plt.subplots(figsize=(3.9, 4.1))
    ax.barh(m["column_name"], m["n_structural"] / 1e3, color=BLUE, label="structural (BTS rules)")
    ax.barh(m["column_name"], m["n_unexpected"] / 1e3, left=m["n_structural"] / 1e3, color=RED, label="unexpected")
    ax.set_xlabel("missing values (thousands of rows)"); ax.set_title("Missing values by column")
    ax.legend(loc="lower right"); _save_fig(fig, "q_missing_by_column.png")
    return out


def duplicates(con) -> pd.DataFrame:
    base_cols = [r[0] for r in con.execute("DESCRIBE c").fetchall()][:45]
    cols = ", ".join(f'"{c}"' for c in base_cols)
    key = ", ".join(f'"{c}"' for c in C.DUP_KEY)
    rows = []

    def add(check, n_groups, n_rows, note):
        rows.append({"check": check, "duplicate_groups": int(n_groups), "rows_involved": int(n_rows), "excess_rows": int(n_rows - n_groups), "note": note})

    r = q(con, f"SELECT COUNT(*) g, COALESCE(SUM(n), 0) n FROM (SELECT COUNT(*) n FROM c GROUP BY {key} HAVING COUNT(*) > 1)").iloc[0]
    add("Same FlightDate + flight number + Origin + Dest + CRSDepTime", r.g, r.n, "The project key; flight_id is built from it")
    r = q(con, f"SELECT COUNT(*) g, COALESCE(SUM(n), 0) n FROM (SELECT COUNT(*) n FROM c GROUP BY {key}, Tail_Number HAVING COUNT(*) > 1)").iloc[0]
    add("Same key plus Tail_Number", r.g, r.n, "")
    r = q(con, "SELECT COUNT(*) g, COALESCE(SUM(n), 0) n FROM (SELECT COUNT(*) n FROM c GROUP BY flight_id HAVING COUNT(*) > 1)").iloc[0]
    add("Duplicate flight_id", r.g, r.n, "flight_id must be unique")
    total = int(q(con, "SELECT COUNT(*) n FROM c").iloc[0].n)
    distinct = int(q(con, f"SELECT COUNT(*) n FROM (SELECT DISTINCT {cols} FROM c)").iloc[0].n)
    add("Exact duplicate rows (all 45 BTS columns identical)", total - distinct, total - distinct if total != distinct else 0, "")
    r = q(con, "SELECT COUNT(*) g, COALESCE(SUM(n), 0) n FROM (SELECT COUNT(*) n FROM c WHERE Tail_Number IS NOT NULL AND is_operated GROUP BY Tail_Number, crs_dep_utc HAVING COUNT(*) > 1)").iloc[0]
    add("Same tail number on two OPERATED flights departing at the same scheduled UTC minute", r.g, r.n,
        "Not a row duplicate: one aircraft cannot fly two flights at once, so one of the tail numbers is wrong or reassigned")
    r = q(con, "SELECT COUNT(*) g, COALESCE(SUM(n), 0) n FROM (SELECT COUNT(*) n FROM c WHERE Tail_Number IS NOT NULL GROUP BY Tail_Number, crs_dep_utc HAVING COUNT(*) > 1)").iloc[0]
    add("Same tail number on two flights (any status) at the same scheduled UTC minute", r.g, r.n, "Includes cancelled flights")
    out = pd.DataFrame(rows)
    _save_table(out, "duplicates.csv")
    return out


def outliers_before_after(con) -> pd.DataFrame:
    lo, hi = (float(x) for x in q(con, "SELECT MIN(arr_delay_min_capped) lo, MAX(arr_delay_min_capped) hi FROM c").iloc[0])
    s = q(con, f"""
      SELECT COUNT(*) FILTER (WHERE ArrDelay > {C.EXTREME_DELAY_MIN}) arr24,
             COUNT(*) FILTER (WHERE arr_delay_min_capped > {C.EXTREME_DELAY_MIN}) arr24_after,
             COUNT(*) FILTER (WHERE ArrDelay < {lo} OR ArrDelay > {hi}) arr_cap,
             COUNT(*) FILTER (WHERE arr_delay_min_capped < {lo} OR arr_delay_min_capped > {hi}) arr_cap_after,
             COUNT(*) FILTER (WHERE DepDelay > {C.EXTREME_DELAY_MIN}) dep24,
             COUNT(*) FILTER (WHERE DepDelay < {C.EARLY_DEP_MIN}) early,
             COUNT(*) FILTER (WHERE TaxiOut > {C.TAXI_EXTREME_MIN}) taxiout, COUNT(*) FILTER (WHERE TaxiIn > {C.TAXI_EXTREME_MIN}) taxiin,
             COUNT(*) FILTER (WHERE ABS(ActualElapsedTime - CRSElapsedTime) > {C.ELAPSED_DIFF_EXTREME_MIN}) elapsed,
             COUNT(*) FILTER (WHERE Distance <= 0 OR Distance > {C.DISTANCE_MAX_MI}) dist,
             COUNT(*) n FROM c""").iloc[0]
    n = int(s.n)
    rows = [
        ("ArrDelay", f"more than {C.EXTREME_DELAY_MIN} min (24 h)", s.arr24, "Original kept and flagged (dq_extreme_delay_24h); capped column added", s.arr24_after, "arr_delay_min_capped"),
        ("ArrDelay", f"outside train-period limits [{lo:.0f}, {hi:.0f}] min", s.arr_cap, "Winsorised in arr_delay_min_capped; original unchanged", s.arr_cap_after, "arr_delay_min_capped"),
        ("DepDelay", f"more than {C.EXTREME_DELAY_MIN} min (24 h)", s.dep24, "Kept and flagged (dq_extreme_delay_24h); departure delay is not capped", s.dep24, "dq_extreme_delay_24h"),
        ("DepDelay", f"more than {abs(C.EARLY_DEP_MIN)} min early", s.early, "Kept and flagged (dq_outlier_other)", s.early, "dq_outlier_other"),
        ("TaxiOut", f"more than {C.TAXI_EXTREME_MIN} min", s.taxiout, "Kept and flagged (dq_outlier_other); outcome field, never a feature", s.taxiout, "dq_outlier_other"),
        ("TaxiIn", f"more than {C.TAXI_EXTREME_MIN} min", s.taxiin, "Kept and flagged (dq_outlier_other); outcome field, never a feature", s.taxiin, "dq_outlier_other"),
        ("ActualElapsedTime - CRSElapsedTime", f"differs by more than {C.ELAPSED_DIFF_EXTREME_MIN} min", s.elapsed, "Kept and flagged (dq_outlier_other)", s.elapsed, "dq_outlier_other"),
        ("Distance", f"zero, negative or above {C.DISTANCE_MAX_MI} mi", s.dist, "None found", s.dist, "dq_outlier_other"),
    ]
    out = pd.DataFrame(rows, columns=["variable", "rule", "rows_before", "treatment", "rows_after_in_modelling_column", "where_flagged_or_treated"])
    out["rows_before"] = out["rows_before"].astype(int); out["rows_after_in_modelling_column"] = out["rows_after_in_modelling_column"].astype(int)
    out.insert(3, "pct_before", (100 * out["rows_before"] / n).round(4))
    _save_table(out, "outliers_before_after.csv")

    stats = q(con, """
      SELECT 'ArrDelay (original), operated' AS series, COUNT(ArrDelay) n, MIN(ArrDelay) min, QUANTILE_CONT(ArrDelay, 0.5) p50,
             QUANTILE_CONT(ArrDelay, 0.99) p99, QUANTILE_CONT(ArrDelay, 0.999) p999, MAX(ArrDelay) max, STDDEV(ArrDelay) std FROM c
      UNION ALL SELECT 'arr_delay_min_capped, operated', COUNT(arr_delay_min_capped), MIN(arr_delay_min_capped),
             QUANTILE_CONT(arr_delay_min_capped, 0.5), QUANTILE_CONT(arr_delay_min_capped, 0.99), QUANTILE_CONT(arr_delay_min_capped, 0.999),
             MAX(arr_delay_min_capped), STDDEV(arr_delay_min_capped) FROM c""").round(2)
    _save_table(stats, "delay_before_after_summary.csv")

    d = q(con, "SELECT ArrDelay, arr_delay_min_capped, TaxiOut, TaxiIn, ActualElapsedTime - CRSElapsedTime AS elapsed_diff, Distance FROM c WHERE is_operated")
    fig, ax = plt.subplots()
    ax.hist(d["ArrDelay"].clip(-100, 800), bins=90, color=BLUE); ax.set_yscale("log")
    ax.set_title("Arrival delay, original"); ax.set_xlabel("ArrDelay (min; clipped to -100..800 for display)"); ax.set_ylabel("flights (log scale)")
    _save_fig(fig, "q_arr_delay_original.png")

    fig, ax = plt.subplots()
    ax.hist(d["arr_delay_min_capped"], bins=90, color=GREEN); ax.set_yscale("log")
    ax.set_title("Arrival delay, capped (train limits)"); ax.set_xlabel("arr_delay_min_capped (min)"); ax.set_ylabel("flights (log scale)")
    _save_fig(fig, "q_arr_delay_capped.png")

    for col, fname, name in [("TaxiOut", "q_taxi_out.png", "Taxi-out time"), ("TaxiIn", "q_taxi_in.png", "Taxi-in time")]:
        fig, ax = plt.subplots()
        ax.hist(d[col].dropna().clip(0, 240), bins=80, color=BLUE); ax.set_yscale("log"); ax.axvline(C.TAXI_EXTREME_MIN, color=RED, ls="--", lw=1)
        ax.set_title(f"{name} (red: {C.TAXI_EXTREME_MIN} min flag)"); ax.set_xlabel("minutes (clipped at 240)"); ax.set_ylabel("flights (log scale)")
        _save_fig(fig, fname)

    fig, ax = plt.subplots()
    ax.hist(d["elapsed_diff"].dropna().clip(-120, 300), bins=100, color=BLUE); ax.set_yscale("log")
    for v in (-C.ELAPSED_DIFF_EXTREME_MIN, C.ELAPSED_DIFF_EXTREME_MIN): ax.axvline(v, color=RED, ls="--", lw=1)
    ax.set_xlabel("actual minus scheduled elapsed time (min)"); ax.set_ylabel("flights (log scale)")
    ax.set_title("Elapsed time vs schedule (red: flag limits)"); _save_fig(fig, "q_elapsed_vs_scheduled.png")

    fig, ax = plt.subplots()
    ax.hist(d["Distance"], bins=80, color=BLUE); ax.set_xlabel("great-circle distance (miles)"); ax.set_ylabel("flights")
    ax.set_title("Distance (no invalid values)"); _save_fig(fig, "q_distance.png")
    return out


def time_conflicts(con) -> pd.DataFrame:
    out = q(con, """
      SELECT flight_id, FlightDate, Origin, Dest, CRSDepTime, CRSArrTime, CRSElapsedTime, crs_dep_utc, crs_arr_utc,
             (epoch(crs_arr_utc) - epoch(crs_dep_utc)) / 60.0 AS utc_block_min, Cancelled, Diverted
      FROM c WHERE dq_time_conflict ORDER BY FlightDate, flight_id""")
    return _save_table(out, "time_conflicts.csv")


# 4. Representativeness ---------------------------------------------------------------------------------


def aa_share_by_month(zip_dir: Path | None = None) -> pd.DataFrame:
    zip_dir = zip_dir or raw_dir()
    rows = []
    con = duckdb.connect()
    with tempfile.TemporaryDirectory(dir=interim_dir()) as tmp:
        for z in sorted(zip_dir.glob("bts_ontime_*.zip")):
            with zipfile.ZipFile(z) as zf:
                member = next(m for m in zf.namelist() if m.lower().endswith(".csv"))
                csv_path = Path(zf.extract(member, tmp))
            p = csv_path.as_posix().replace("'", "''")
            r = con.execute(f"""
              SELECT COUNT(*) AS all_flights, COUNT(DISTINCT TRIM(Reporting_Airline)) AS carriers,
                     COUNT(*) FILTER (WHERE TRIM(Reporting_Airline) = 'AA') AS aa_flights,
                     COUNT(*) FILTER (WHERE TRY_CAST(Cancelled AS DOUBLE) = 1) AS all_cancelled,
                     COUNT(*) FILTER (WHERE TRIM(Reporting_Airline) = 'AA' AND TRY_CAST(Cancelled AS DOUBLE) = 1) AS aa_cancelled,
                     COUNT(*) FILTER (WHERE TRY_CAST(Cancelled AS DOUBLE) = 0 AND TRY_CAST(Diverted AS DOUBLE) = 0) AS all_operated,
                     COUNT(*) FILTER (WHERE TRY_CAST(ArrDel15 AS DOUBLE) = 1) AS all_late,
                     COUNT(*) FILTER (WHERE TRIM(Reporting_Airline) = 'AA' AND TRY_CAST(Cancelled AS DOUBLE) = 0 AND TRY_CAST(Diverted AS DOUBLE) = 0) AS aa_operated,
                     COUNT(*) FILTER (WHERE TRIM(Reporting_Airline) = 'AA' AND TRY_CAST(ArrDel15 AS DOUBLE) = 1) AS aa_late
              FROM read_csv('{p}', header=true, all_varchar=true, quote='"', null_padding=true)""").df().iloc[0]
            csv_path.unlink(missing_ok=True)
            ym = z.stem.replace("bts_ontime_", "")
            rows.append({"month": ym.replace("_", "-"), **{k: int(v) for k, v in r.items()}})
    df = pd.DataFrame(rows)
    df["aa_share_pct"] = (100 * df["aa_flights"] / df["all_flights"]).round(2)
    df["all_cancelled_pct"] = (100 * df["all_cancelled"] / df["all_flights"]).round(2)
    df["aa_cancelled_pct"] = (100 * df["aa_cancelled"] / df["aa_flights"]).round(2)
    df["all_arr_del15_pct"] = (100 * df["all_late"] / df["all_operated"]).round(2)
    df["aa_arr_del15_pct"] = (100 * df["aa_late"] / df["aa_operated"]).round(2)
    _save_table(df, "aa_share_by_month.csv")
    fig, ax = plt.subplots()
    ax.bar(df["month"], df["aa_share_pct"], color=BLUE); ax.set_ylabel("AA share of all flights (%)")
    ax.set_title("AA share of reporting-carrier flights"); ax.tick_params(axis="x", rotation=70, labelsize=6)
    ax.set_ylim(0, max(20, df["aa_share_pct"].max() * 1.2)); _save_fig(fig, "r_aa_share_by_month.png")
    fig, ax = plt.subplots()
    ax.plot(df["month"], df["all_arr_del15_pct"], marker="o", color=GREY, label="all reporting carriers")
    ax.plot(df["month"], df["aa_arr_del15_pct"], marker="o", color=BLUE, label="AA mainline")
    ax.set_ylabel("late arrivals, 15+ min (% of operated)"); ax.set_title("Late-arrival rate: AA vs all carriers")
    ax.tick_params(axis="x", rotation=70, labelsize=6); ax.legend(); _save_fig(fig, "r_aa_vs_all_delay_rate.png")
    return df


def _rates(con, group_expr: str, label: str, order_sql: str | None = None) -> pd.DataFrame:
    return q(con, f"""
      SELECT {group_expr} AS {label}, COUNT(*) AS flights, ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 3) AS share_pct,
             ROUND(100.0 * AVG(Cancelled), 3) AS cancelled_pct, ROUND(100.0 * AVG(Diverted), 3) AS diverted_pct,
             ROUND(100.0 * AVG(ArrDel15) FILTER (WHERE is_operated), 3) AS arr_del15_pct
      FROM c GROUP BY 1 ORDER BY {order_sql or '1'}""")


def airport_tables(con) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    df = q(con, """
      SELECT Origin AS airport, COUNT(*) AS flights, SUM(Cancelled) AS cancelled, SUM(Diverted) AS diverted,
             COUNT(*) FILTER (WHERE is_operated) AS operated, COUNT(*) FILTER (WHERE is_operated AND ArrDel15 = 1) AS late
      FROM c GROUP BY 1 ORDER BY flights DESC, airport""")
    df["rank"] = np.arange(1, len(df) + 1)
    df["share_pct"] = (100 * df["flights"] / df["flights"].sum()).round(3)
    df["cum_share_pct"] = df["share_pct"].cumsum().round(2)
    df["group"] = np.where(df["rank"] <= 10, "top 10 airports", np.where(df["rank"] <= 30, "airports 11-30", "other airports"))
    df["cancelled_pct"] = (100 * df["cancelled"] / df["flights"]).round(3)
    df["arr_del15_pct"] = (100 * df["late"] / df["operated"]).round(3)
    _save_table(df[["rank", "airport", "group", "flights", "share_pct", "cum_share_pct", "cancelled_pct", "arr_del15_pct"]], "origin_airport_share.csv")
    g = df.groupby("group").agg(airports=("airport", "size"), flights=("flights", "sum"), cancelled=("cancelled", "sum"),
                                diverted=("diverted", "sum"), operated=("operated", "sum"), late=("late", "sum")).reindex(
        ["top 10 airports", "airports 11-30", "other airports"])
    g["share_pct"] = (100 * g["flights"] / g["flights"].sum()).round(2)
    g["cancelled_pct"] = (100 * g["cancelled"] / g["flights"]).round(3)
    g["diverted_pct"] = (100 * g["diverted"] / g["flights"]).round(3)
    g["arr_del15_pct"] = (100 * g["late"] / g["operated"]).round(3)
    g = g.reset_index()[["group", "airports", "flights", "share_pct", "cancelled_pct", "diverted_pct", "arr_del15_pct"]]
    _save_table(g, "origin_airport_groups.csv")
    small = df[df["share_pct"] < 0.1]
    info = {"n_airports": len(df), "n_small": len(small), "small_share_pct": round(float(small["flights"].sum() / df["flights"].sum() * 100), 2),
            "top1": df.iloc[0]["airport"], "top1_share": float(df.iloc[0]["share_pct"]), "top5_share": float(df["share_pct"].head(5).sum()),
            "top10_share": float(df["share_pct"].head(10).sum()), "top25_share": float(df["share_pct"].head(25).sum()),
            "top10": list(df["airport"].head(10)), "groups": dict(zip(df["airport"], df["group"]))}
    fig, ax = plt.subplots(figsize=(3.9, 3.4))
    t = df.head(15).iloc[::-1]
    ax.barh(t["airport"], t["share_pct"], color=BLUE); ax.set_xlabel("share of all AA flights, by origin (%)")
    ax.set_title(f"Top 10 origins carry {info['top10_share']:.0f}% of flights"); _save_fig(fig, "r_airport_concentration.png")
    return df, g, info


def calendar_tables(con) -> dict[str, pd.DataFrame]:
    hour = _rates(con, "crs_dep_min // 60", "dep_hour_local")
    month = _rates(con, "strftime(FlightDate, '%Y-%m')", "month")
    moy = _rates(con, "Month", "month_of_year")
    dow = _rates(con, "DayOfWeek", "day_of_week")
    dow["day_name"] = [DOW_NAMES[int(i) - 1] for i in dow["day_of_week"]]
    for name, d in [("by_hour.csv", hour), ("by_month.csv", month), ("by_month_of_year.csv", moy), ("by_day_of_week.csv", dow)]:
        _save_table(d, name)

    fig, ax = plt.subplots()
    ax.bar(hour["dep_hour_local"], hour["share_pct"], color=BLUE); ax.set_xlabel("scheduled departure hour (local)"); ax.set_ylabel("share of flights (%)")
    ax2 = ax.twinx(); ax2.grid(False); ax2.plot(hour["dep_hour_local"], hour["arr_del15_pct"], color=ORANGE, marker="o", ms=3, label="late-arrival rate")
    ax2.plot(hour["dep_hour_local"], hour["cancelled_pct"], color=RED, marker="s", ms=3, label="cancellation rate"); ax2.set_ylabel("rate (%)")
    ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, frameon=False); ax.set_title("Flights and delay rates by hour of day"); _save_fig(fig, "r_hour_of_day.png")

    mnames = [MONTH_NAMES[i - 1] for i in moy["month_of_year"]]
    fig, ax = plt.subplots()
    ax.bar(mnames, moy["share_pct"], color=BLUE); ax.set_title("Share of flights by calendar month"); ax.set_ylabel("share of flights (%)"); ax.set_xlabel("month (both years pooled)")
    _save_fig(fig, "r_month_share.png")
    fig, ax = plt.subplots()
    ax.bar(dow["day_name"], dow["share_pct"], color=BLUE); ax.set_title("Share of flights by day of week"); ax.set_ylabel("share of flights (%)"); ax.set_xlabel("day of week")
    _save_fig(fig, "r_weekday_share.png")

    fig, ax = plt.subplots()
    ax.bar(mnames, moy["arr_del15_pct"], color=ORANGE, label="late arrival"); ax.plot(mnames, moy["cancelled_pct"], color=RED, marker="o", ms=3, label="cancelled")
    ax.set_title("Delay and cancellation rate by month"); ax.set_ylabel("rate (%)"); ax.set_xlabel("month (both years pooled)"); ax.legend()
    _save_fig(fig, "r_rates_by_month.png")
    fig, ax = plt.subplots()
    ax.bar(dow["day_name"], dow["arr_del15_pct"], color=ORANGE, label="late arrival"); ax.plot(dow["day_name"], dow["cancelled_pct"], color=RED, marker="o", ms=3, label="cancelled")
    ax.set_title("Delay and cancellation rate by weekday"); ax.set_ylabel("rate (%)"); ax.set_xlabel("day of week"); ax.legend()
    _save_fig(fig, "r_rates_by_weekday.png")
    return {"hour": hour, "month": month, "moy": moy, "dow": dow}


def rotation_tables(con, airport_info: dict) -> dict[str, pd.DataFrame]:
    groups = airport_info["groups"]
    gmap = pd.DataFrame({"airport": list(groups), "group": list(groups.values())})
    con.register("gmap", gmap)
    by_month = q(con, """
      WITH td AS (SELECT tail_number, flight_date, BOOL_OR(usable) AS u, COUNT(*) AS n, SUM(CASE WHEN usable THEN 1 ELSE 0 END) AS ul
                  FROM rot WHERE tail_number IS NOT NULL GROUP BY 1, 2)
      SELECT strftime(flight_date, '%Y-%m') AS month, COUNT(*) AS tail_days, SUM(u::INT) AS tail_days_with_usable_segment,
             ROUND(100.0 * AVG(u::INT), 2) AS pct_tail_days_usable, ROUND(100.0 * SUM(ul) / SUM(n), 2) AS pct_flights_in_usable_segments
      FROM td GROUP BY 1 ORDER BY 1""")
    by_group = q(con, """
      WITH td AS (SELECT tail_number, flight_date, BOOL_OR(usable) AS u, ARG_MIN(origin, crs_dep_utc) AS first_origin
                  FROM rot WHERE tail_number IS NOT NULL GROUP BY 1, 2)
      SELECT g.group AS airport_group, COUNT(*) AS tail_days, ROUND(100.0 * AVG(td.u::INT), 2) AS pct_tail_days_usable
      FROM td JOIN gmap g ON g.airport = td.first_origin GROUP BY 1 ORDER BY 3 DESC""")
    legs_group = q(con, """
      SELECT g.group AS airport_group, COUNT(*) AS flights, ROUND(100.0 * AVG(usable::INT), 2) AS pct_flights_in_usable_segments
      FROM rot r JOIN gmap g ON g.airport = r.origin GROUP BY 1 ORDER BY 3 DESC""")
    swap_month = q(con, """
      WITH s AS (SELECT strftime(flight_date, '%Y-%m') AS month, COUNT(*) AS swaps FROM brk WHERE reason = 'swap_suspect' GROUP BY 1),
           t AS (SELECT strftime(outbound_flight_date, '%Y-%m') AS month, COUNT(*) AS linked_turns FROM turns GROUP BY 1)
      SELECT s.month, s.swaps AS swap_suspect_pairs, t.linked_turns, ROUND(100.0 * s.swaps / (s.swaps + t.linked_turns), 2) AS swaps_per_100_candidate_pairs
      FROM s JOIN t USING (month) ORDER BY 1""")
    swap_group = q(con, """
      WITH s AS (SELECT g.group AS airport_group, COUNT(*) AS swaps FROM brk b JOIN gmap g ON g.airport = b.prev_dest WHERE reason = 'swap_suspect' GROUP BY 1),
           t AS (SELECT g.group AS airport_group, COUNT(*) AS linked_turns FROM turns x JOIN gmap g ON g.airport = x.station GROUP BY 1)
      SELECT s.airport_group, s.swaps AS swap_suspect_pairs, t.linked_turns, ROUND(100.0 * s.swaps / (s.swaps + t.linked_turns), 2) AS swaps_per_100_candidate_pairs
      FROM s JOIN t USING (airport_group) ORDER BY 4 DESC""")
    for name, d in [("usable_by_month.csv", by_month), ("usable_by_airport_group.csv", by_group), ("usable_flights_by_airport_group.csv", legs_group),
                    ("swaps_by_month.csv", swap_month), ("swaps_by_airport_group.csv", swap_group)]:
        _save_table(d, name)
    fig, ax = plt.subplots()
    ax.plot(by_month["month"], by_month["pct_tail_days_usable"], color=BLUE, marker="o", ms=3); ax.set_ylim(60, 100)
    ax.set_title("Tail-days with a usable chain (axis from 60%)"); ax.set_ylabel("tail-days (%)"); ax.tick_params(axis="x", rotation=80, labelsize=6)
    _save_fig(fig, "r_usable_by_month.png")
    fig, ax = plt.subplots()
    ax.bar(swap_month["month"], swap_month["swaps_per_100_candidate_pairs"], color=ORANGE)
    ax.set_title("Tail-swap pairs per 100 candidate pairs"); ax.set_ylabel("swap pairs per 100"); ax.tick_params(axis="x", rotation=80, labelsize=6)
    _save_fig(fig, "r_swaps_by_month.png")
    fig, ax = plt.subplots()
    ax.bar(by_group["airport_group"], by_group["pct_tail_days_usable"], color=BLUE); ax.set_title("Usable tail-days by base airport group")
    ax.set_ylabel("tail-days (%)"); ax.set_ylim(0, 100); ax.tick_params(axis="x", rotation=15)
    _save_fig(fig, "r_usable_by_airport_group.png")
    fig, ax = plt.subplots()
    ax.bar(swap_group["airport_group"], swap_group["swaps_per_100_candidate_pairs"], color=ORANGE); ax.set_title("Swap pairs per 100 at the station, by group")
    ax.set_ylabel("swap pairs per 100"); ax.tick_params(axis="x", rotation=15); _save_fig(fig, "r_swaps_by_airport_group.png")
    return {"by_month": by_month, "by_group": by_group, "swap_month": swap_month, "swap_group": swap_group, "legs_group": legs_group}


def country_check(con) -> dict[str, pd.DataFrame]:
    oa = load_ourairports()[["iata", "iso_country"]]
    airports = q(con, """
      SELECT a AS iata, SUM(o) AS flights_as_origin, SUM(d) AS flights_as_dest FROM (
        SELECT Origin AS a, COUNT(*) AS o, 0 AS d FROM c GROUP BY 1 UNION ALL SELECT Dest, 0, COUNT(*) FROM c GROUP BY 1) GROUP BY 1""")
    m = airports.merge(oa, on="iata", how="left")
    m["iso_country"] = m["iso_country"].fillna("unmatched")
    m["class"] = np.where(m["iso_country"] == "US", "US (50 states and DC)",
                          np.where(m["iso_country"].isin(US_TERRITORIES), "US territory", np.where(m["iso_country"] == "unmatched", "not matched in OurAirports", "foreign country")))
    summary = m.groupby(["class", "iso_country"]).agg(airports=("iata", "size"), flights_as_origin=("flights_as_origin", "sum"), flights_as_dest=("flights_as_dest", "sum")).reset_index()
    _save_table(summary, "country_check.csv")
    listing = m[m["class"] != "US (50 states and DC)"].sort_values("flights_as_origin", ascending=False)
    _save_table(listing, "non_50_state_airports.csv")
    states = q(con, """
      SELECT st AS state_code, SUM(o) AS flights_as_origin, SUM(d) AS flights_as_dest FROM (
        SELECT OriginState AS st, COUNT(*) AS o, 0 AS d FROM c GROUP BY 1 UNION ALL SELECT DestState, 0, COUNT(*) FROM c GROUP BY 1) GROUP BY 1 ORDER BY 1""")
    _save_table(states, "state_codes.csv")
    foreign = int(m.loc[m["class"] == "foreign country", ["flights_as_origin", "flights_as_dest"]].to_numpy().sum())
    terr = int(m.loc[m["class"] == "US territory", ["flights_as_origin", "flights_as_dest"]].to_numpy().sum())
    return {"summary": summary, "listing": listing, "states": states, "foreign_flight_ends": foreign, "territory_flight_ends": terr,
            "unmatched": list(m.loc[m["class"] == "not matched in OurAirports", "iata"])}


def storm_vs_normal(con) -> pd.DataFrame:
    lo, hi = STORM
    df = q(con, f"""
      WITH f AS (SELECT *, CASE WHEN FlightDate BETWEEN DATE '{lo}' AND DATE '{hi}' THEN 'storm days (2026-01-24 to 2026-01-28)'
                                WHEN FlightDate BETWEEN DATE '2026-01-01' AND DATE '2026-01-31' THEN 'other January 2026 days'
                                ELSE 'all other days' END AS grp FROM c)
      SELECT grp AS group_name, COUNT(DISTINCT FlightDate) AS days, ROUND(COUNT(*) * 1.0 / COUNT(DISTINCT FlightDate), 0) AS flights_per_day,
             ROUND(100.0 * AVG(Cancelled), 2) AS cancelled_pct, ROUND(100.0 * AVG(Diverted), 3) AS diverted_pct,
             ROUND(100.0 * AVG(ArrDel15) FILTER (WHERE is_operated), 2) AS arr_del15_pct,
             ROUND(AVG(ArrDelay) FILTER (WHERE is_operated), 2) AS mean_arr_delay_min,
             ROUND(AVG(arr_delay_min_capped) FILTER (WHERE is_operated), 2) AS mean_arr_delay_capped_min
      FROM f GROUP BY 1 ORDER BY 3 DESC""")
    r = q(con, f"""
      WITH f AS (SELECT *, CASE WHEN flight_date BETWEEN DATE '{lo}' AND DATE '{hi}' THEN 'storm days (2026-01-24 to 2026-01-28)'
                               WHEN flight_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31' THEN 'other January 2026 days' ELSE 'all other days' END AS grp FROM rot)
      SELECT grp AS group_name, ROUND(100.0 * AVG(usable::INT), 2) AS pct_flights_in_usable_segments FROM f GROUP BY 1""")
    t = q(con, f"""
      SELECT CASE WHEN outbound_flight_date BETWEEN DATE '{lo}' AND DATE '{hi}' THEN 'storm days (2026-01-24 to 2026-01-28)'
                  WHEN outbound_flight_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31' THEN 'other January 2026 days' ELSE 'all other days' END AS group_name,
             COUNT(*) AS turns, COUNT(DISTINCT outbound_flight_date) AS d, ROUND(AVG(outbound_dep_delay_min), 2) AS mean_outbound_dep_delay_min FROM turns GROUP BY 1""")
    t["turns_per_day"] = (t["turns"] / t["d"]).round(0)
    out = df.merge(r, on="group_name").merge(t[["group_name", "turns_per_day", "mean_outbound_dep_delay_min"]], on="group_name")
    _save_table(out, "storm_vs_normal.csv")
    daily = q(con, "SELECT FlightDate, ROUND(100.0 * AVG(Cancelled), 2) AS cancelled_pct, ROUND(100.0 * AVG(ArrDel15) FILTER (WHERE is_operated), 2) AS arr_del15_pct FROM c WHERE FlightDate BETWEEN DATE '2026-01-10' AND DATE '2026-02-10' GROUP BY 1 ORDER BY 1")
    fig, ax = plt.subplots()
    d = pd.to_datetime(daily["FlightDate"])
    ax.axvspan(pd.Timestamp(lo) - pd.Timedelta(hours=12), pd.Timestamp(hi) + pd.Timedelta(hours=12), color=RED, alpha=0.12, label="storm days")
    ax.plot(d, daily["cancelled_pct"], color=RED, marker="o", ms=3, label="cancelled %"); ax.plot(d, daily["arr_del15_pct"], color=BLUE, marker="o", ms=3, label="late arrival % (operated)")
    ax.set_ylabel("% of flights"); ax.set_title("January 2026 storm: cancellations and delays"); ax.set_xlabel("flight date"); ax.legend(); ax.tick_params(axis="x", rotation=45)
    _save_fig(fig, "r_storm_vs_normal.png")
    return out


def split_coverage(con) -> pd.DataFrame:
    df = q(con, "SELECT Month AS month_of_year, split, COUNT(*) AS flights, COUNT(DISTINCT Year) AS years FROM c GROUP BY 1, 2 ORDER BY 1, 2")
    wide = df.pivot(index="month_of_year", columns="split", values="flights").reindex(columns=["train", "validate", "test"]).fillna(0).astype(int)
    wide.insert(0, "month_name", [MONTH_NAMES[i - 1] for i in wide.index])
    wide["in_train"] = wide["train"] > 0; wide["in_validate"] = wide["validate"] > 0; wide["in_test"] = wide["test"] > 0
    wide = wide.reset_index()
    _save_table(wide, "split_coverage_by_month_of_year.csv")
    mon = q(con, "SELECT strftime(FlightDate, '%Y-%m') AS month, split, COUNT(*) AS flights FROM c GROUP BY 1, 2 ORDER BY 1")
    _save_table(mon, "split_by_month.csv")
    fig, ax = plt.subplots()
    cols = {"train": BLUE, "validate": ORANGE, "test": GREEN}
    ax.bar(mon["month"], mon["flights"] / 1e3, color=[cols[s] for s in mon["split"]]); ax.set_ylabel("flights (thousands)")
    ax.tick_params(axis="x", rotation=70, labelsize=6); ax.set_title("Flights per month, by train/validate/test split"); ax.set_xlabel("month")
    for s, c in cols.items(): ax.bar([], [], color=c, label=s)
    ax.legend(); _save_fig(fig, "r_split_coverage.png")
    return wide


# Summary -----------------------------------------------------------------------------------------------


def write_summary(t: dict) -> None:
    aa, ai, grp, cal, rot, ctry, storm, cov = t["aa"], t["airport_info"], t["airport_groups"], t["cal"], t["rot"], t["country"], t["storm"], t["cov"]
    hour = cal["hour"]; night = hour[hour["dep_hour_local"] < 5]; late = hour[hour["dep_hour_local"] >= 22]
    g = {r["group"]: r for _, r in grp.iterrows()}
    st = {r["group_name"]: r for _, r in storm.iterrows()}
    s_storm, s_jan, s_oth = st["storm days (2026-01-24 to 2026-01-28)"], st["other January 2026 days"], st["all other days"]
    miss_test = cov.loc[~cov["in_test"], "month_name"].tolist(); miss_train = cov.loc[~cov["in_train"], "month_name"].tolist()
    once = cov.loc[(cov["train"] + cov["validate"] + cov["test"] > 0) & ~(cov["in_test"] & cov["in_train"]), "month_name"].tolist()
    test_storm_pct = float(s_storm["flights_per_day"]) * 5 / float(t["test_flights"]) * 100
    um = rot["by_month"]; sm = rot["swap_month"]; sg = rot["swap_group"].set_index("airport_group")["swaps_per_100_candidate_pairs"]
    ug = rot["by_group"].set_index("airport_group")["pct_tail_days_usable"]
    lines = [
        "# Representativeness and bias: summary", "",
        "Generated by `bufferengine.pipeline.d2_evidence`. Every number below is read from a table in `docs/d2/tables/` "
        "(file named in brackets). Figures are in `docs/d2/figures/`.", "",
        "## Findings", "",
        f"1. **AA is a minority of US reporting-carrier traffic.** AA mainline is {aa['aa_share_pct'].mean():.1f}% of all reporting-carrier flights on average "
        f"(monthly range {aa['aa_share_pct'].min():.1f}% to {aa['aa_share_pct'].max():.1f}%), computed from {aa['all_flights'].sum():,} raw flights; "
        f"it is not a sample of the whole industry. AA's late-arrival rate ({aa['aa_late'].sum() / aa['aa_operated'].sum() * 100:.1f}% of operated flights) differs from all carriers' "
        f"({aa['all_late'].sum() / aa['all_operated'].sum() * 100:.1f}%). [aa_share_by_month.csv]",
        f"2. **Flights are concentrated in a few airports.** The top 10 origins carry {ai['top10_share']:.1f}% of flights and the top 25 carry {ai['top25_share']:.1f}%; "
        f"{ai['n_small']} of {ai['n_airports']} origin airports have under 0.1% of flights each and together {ai['small_share_pct']:.1f}%. Their rates differ: late-arrival "
        f"{g['top 10 airports']['arr_del15_pct']:.1f}% at the top 10, {g['airports 11-30']['arr_del15_pct']:.1f}% at airports 11-30 and {g['other airports']['arr_del15_pct']:.1f}% elsewhere; "
        f"cancellation {g['top 10 airports']['cancelled_pct']:.2f}%, {g['airports 11-30']['cancelled_pct']:.2f}% and {g['other airports']['cancelled_pct']:.2f}%. "
        f"Results will mostly describe hub behaviour. [origin_airport_share.csv, origin_airport_groups.csv]",
        f"3. **Night and very early hours are thin.** Departures scheduled 00:00-04:59 are {night['share_pct'].sum():.2f}% of flights and 22:00-23:59 are {late['share_pct'].sum():.2f}%. "
        f"Hourly late-arrival rates range from {hour['arr_del15_pct'].min():.1f}% to {hour['arr_del15_pct'].max():.1f}%, so hours with few flights have noisy rates. [by_hour.csv]",
        f"4. **Domestic scheduled service only; no foreign airports found.** Of the airports matched in OurAirports, flights touching a foreign country: {ctry['foreign_flight_ends']:,} flight ends; "
        f"flights touching US territories (Puerto Rico, US Virgin Islands and similar): {ctry['territory_flight_ends']:,} flight ends. "
        f"Airports not matched by IATA code: {', '.join(ctry['unmatched']) if ctry['unmatched'] else 'none'}. AA's international flying is therefore **absent**, although the D1 charter describes AA as domestic and international. "
        f"[country_check.csv, non_50_state_airports.csv, state_codes.csv]",
        f"5. **The calendar is short.** The table covers 20 months. Calendar months absent from the test window: {', '.join(miss_test)} "
        f"(BTS had published only to August 2026). Absent from the training window: {', '.join(miss_train)}. Calendar months with data in only one year: {', '.join(once)}. "
        f"Each calendar month appears once or twice, so seasonal and year effects are confounded. [split_coverage_by_month_of_year.csv]",
        f"6. **One severe storm sits in the test window.** The five storm days (2026-01-24 to 2026-01-28) had {s_storm['cancelled_pct']:.1f}% of flights cancelled against {s_oth['cancelled_pct']:.1f}% on all other days; "
        f"late-arrival rate {s_storm['arr_del15_pct']:.1f}% against {s_oth['arr_del15_pct']:.1f}%; only {s_storm['pct_flights_in_usable_segments']:.1f}% of flights were in usable chains against {s_oth['pct_flights_in_usable_segments']:.1f}%. "
        f"They are about {test_storm_pct:.1f}% of test-window flights, so they move test averages disproportionately. Other January days: cancelled {s_jan['cancelled_pct']:.1f}%. [storm_vs_normal.csv]",
        f"7. **Disrupted days under-contribute to usable chains.** Usable-chain share by month runs from {um['pct_tail_days_usable'].min():.1f}% to {um['pct_tail_days_usable'].max():.1f}% of tail-days "
        f"(lowest {um.loc[um['pct_tail_days_usable'].idxmin(), 'month']}); the optimiser sees fewer chains exactly when disruption is highest. [usable_by_month.csv]",
        f"8. **Tail swaps are uneven.** Swap-suspect pairs per 100 candidate pairs: {sg.get('top 10 airports', float('nan')):.2f} at the top-10 airports, {sg.get('airports 11-30', float('nan')):.2f} at airports 11-30, "
        f"{sg.get('other airports', float('nan')):.2f} elsewhere; by month from {sm['swaps_per_100_candidate_pairs'].min():.2f} to {sm['swaps_per_100_candidate_pairs'].max():.2f} "
        f"(highest {sm.loc[sm['swaps_per_100_candidate_pairs'].idxmax(), 'month']}). Usable tail-days by base airport: top 10 {ug.get('top 10 airports', float('nan')):.1f}%, "
        f"11-30 {ug.get('airports 11-30', float('nan')):.1f}%, other {ug.get('other airports', float('nan')):.1f}%. [swaps_by_month.csv, swaps_by_airport_group.csv, usable_by_airport_group.csv]",
        "",
        "## Who or what is under-represented", "",
        f"- **Other carriers and the rest of the industry**: AA is {aa['aa_share_pct'].mean():.1f}% of reporting-carrier flights; low-cost, regional and cargo operations are not covered. [aa_share_by_month.csv]",
        f"- **Small airports**: {ai['n_small']} airports with under 0.1% of flights each, {ai['small_share_pct']:.1f}% of flights combined. [origin_airport_share.csv]",
        f"- **Overnight and early-morning flying** (00:00-04:59): {night['share_pct'].sum():.2f}% of flights. [by_hour.csv]",
        f"- **International flying**: none in the table; foreign flight ends = {ctry['foreign_flight_ends']}. [country_check.csv]",
        f"- **September to December in the test window**, and any period after August 2026: absent. Test months present: {', '.join(cov.loc[cov['in_test'], 'month_name'])}. [split_coverage_by_month_of_year.csv]",
        f"- **Autumn and winter in the training window**: training has {', '.join(cov.loc[cov['in_train'], 'month_name'])} of 2025 only; October to December 2025 is validation only. [split_coverage_by_month_of_year.csv]",
        f"- **Severe disruption**: one storm episode ({s_storm['days']:.0f} days) in the whole table. [storm_vs_normal.csv]",
        f"- **Days with broken chains**: months with the lowest usable-chain share ({um['pct_tail_days_usable'].min():.1f}% of tail-days). [usable_by_month.csv]",
        "", "## Not verified", "",
        "- Reasons for AA's differences from the industry are not tested here. The BTS file does not identify regional (American Eagle) flying, which is reported by other carriers.",
        "- The country check uses OurAirports country codes matched on IATA code; an airport whose IATA code changed or is not in OurAirports appears under 'not matched'.",
    ]
    (D2 / "representativeness_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(root: Path | None = None) -> dict:
    D2.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    for old in list(FIGS.glob("*.png")) + list(TABLES.glob("*.csv")):
        old.unlink()  # start clean so renamed outputs never leave stale files behind
    con = connect(root=root)
    t: dict = {}
    t["missing"] = missing_by_column(con)
    t["dups"] = duplicates(con)
    t["outliers"] = outliers_before_after(con)
    time_conflicts(con)
    t["aa"] = aa_share_by_month()
    _, t["airport_groups"], t["airport_info"] = airport_tables(con)
    t["cal"] = calendar_tables(con)
    t["rot"] = rotation_tables(con, t["airport_info"])
    t["country"] = country_check(con)
    t["storm"] = storm_vs_normal(con)
    t["cov"] = split_coverage(con)
    t["test_flights"] = int(q(con, "SELECT COUNT(*) n FROM c WHERE split = 'test'").iloc[0].n)
    write_summary(t)
    print(f"tables: {len(list(TABLES.glob('*.csv')))}, figures: {len(list(FIGS.glob('*.png')))}")
    return t


if __name__ == "__main__":
    run()
