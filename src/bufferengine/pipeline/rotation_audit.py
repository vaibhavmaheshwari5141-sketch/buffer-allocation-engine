"""Diagnostics for the rotation tables (used by notebooks/02_rotation_building).

All functions take the pandas tables written by ``build_rotations`` (legs = rotations.parquet,
turns = rotation_turns.parquet, breaks = rotation_breaks.parquet) and return DataFrames.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bufferengine.config import processed_dir
from bufferengine.pipeline.build_rotations import (
    BREAKS_FILE,
    BUFFER_BUCKETS,
    LEGS_FILE,
    MTT_SENS_FILE,
    TURNS_FILE,
)

Z95 = 1.96


def load_tables(root=None) -> dict[str, pd.DataFrame]:
    root = root or processed_dir()
    return {
        "legs": pd.read_parquet(root / LEGS_FILE),
        "turns": pd.read_parquet(root / TURNS_FILE),
        "breaks": pd.read_parquet(root / BREAKS_FILE),
        "sens": pd.read_parquet(root / MTT_SENS_FILE),
    }


# 1. Categories and chain length --------------------------------------------------------------------


def segment_length_distribution(legs: pd.DataFrame) -> pd.DataFrame:
    seg = legs[legs["segment_id"].notna()].drop_duplicates("segment_id")
    d = seg["n_legs"].clip(upper=8).value_counts().sort_index().rename("segments").to_frame()
    d["legs"] = d.index * d["segments"]
    d["pct_of_segments"] = (100 * d["segments"] / d["segments"].sum()).round(2)
    d.index = [str(i) if i < 8 else "8+" for i in d.index]
    d.index.name = "legs_in_segment"
    return d.reset_index()


def break_reason_counts(legs: pd.DataFrame) -> pd.DataFrame:
    """Why each operated leg is the first of its segment (reason before) and the last (reason after)."""
    op = legs[legs["segment_id"].notna()]
    before = op["break_reason_before"].value_counts().rename("legs_starting_segment")
    after = op["break_reason_after"].value_counts().rename("legs_ending_segment")
    out = pd.concat([before, after], axis=1).fillna(0).astype(int)
    out.index.name = "reason"
    return out.drop(index="linked", errors="ignore").reset_index()


def usable_share_by_month(legs: pd.DataFrame) -> pd.DataFrame:
    m = legs.assign(month=pd.to_datetime(legs["flight_date"]).dt.to_period("M").astype(str))
    return m.groupby("month").agg(flights=("flight_id", "size"), usable_pct=("usable", lambda s: 100 * s.mean())).round(2).reset_index()


# 2. Swaps ------------------------------------------------------------------------------------------


def swaps_by_month(breaks: pd.DataFrame, turns: pd.DataFrame) -> pd.DataFrame:
    sw = breaks[breaks["reason"] == "swap_suspect"].assign(
        month=lambda d: pd.to_datetime(d["flight_date"]).dt.to_period("M").astype(str))
    tn = turns.assign(month=pd.to_datetime(turns["outbound_flight_date"]).dt.to_period("M").astype(str))
    a = sw.groupby("month").size().rename("swap_suspect_pairs")
    b = tn.groupby("month").size().rename("linked_turns")
    out = pd.concat([a, b], axis=1).fillna(0).astype(int)
    out["swap_per_100_candidate_pairs"] = (100 * out["swap_suspect_pairs"] / (out["swap_suspect_pairs"] + out["linked_turns"])).round(2)
    return out.reset_index(names="month")


def swaps_by_station(breaks: pd.DataFrame, turns: pd.DataFrame, top: int = 15, min_pairs: int = 300) -> pd.DataFrame:
    sw = breaks[breaks["reason"] == "swap_suspect"].groupby("prev_dest").size().rename("swap_suspect_pairs")
    tn = turns.groupby("station").size().rename("linked_turns")
    out = pd.concat([sw, tn], axis=1).fillna(0).astype(int)
    out["swap_per_100_candidate_pairs"] = (100 * out["swap_suspect_pairs"] / (out["swap_suspect_pairs"] + out["linked_turns"])).round(2)
    out = out[(out["swap_suspect_pairs"] + out["linked_turns"]) >= min_pairs]
    out.index.name = "station"
    return out.sort_values("swap_per_100_candidate_pairs", ascending=False).head(top).reset_index()


def swap_size_distribution(breaks: pd.DataFrame) -> pd.DataFrame:
    sw = breaks[breaks["reason"] == "swap_suspect"]
    b = pd.cut(-sw["gap_min"], bins=[0, 15, 30, 60, 120, np.inf], labels=["<=15", "15-30", "30-60", "60-120", ">120"])
    out = b.value_counts().sort_index().rename("pairs").to_frame()
    out["pct"] = (100 * out["pairs"] / out["pairs"].sum()).round(1)
    out.index.name = "next departure scheduled before arrival by (min)"
    return out.reset_index()


# 3. Ground time, MTT, buffer -----------------------------------------------------------------------


def buffer_summary(turns: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    g = turns.groupby(by, observed=True)
    out = g.agg(
        turns=("turn_id", "size"),
        sched_ground_p50=("sched_ground_min", "median"),
        mtt_p50=("mtt_min", "median"),
        buffer_p50=("planned_buffer_min", "median"),
        buffer_mean=("planned_buffer_min", "mean"),
        zero_buffer_pct=("planned_buffer_min", lambda s: 100 * (s == 0).mean()),
        buffer_under_15_pct=("planned_buffer_min", lambda s: 100 * (s < 15).mean()),
    ).round(1)
    return out.reset_index()


def mtt_source_share(turns: pd.DataFrame) -> pd.DataFrame:
    out = turns["mtt_source"].value_counts(normalize=True).mul(100).round(2).rename("pct_of_turns")
    return out.rename_axis("mtt_source").reset_index()


def sensitivity_summary(sens: pd.DataFrame, split: str | None = None) -> pd.DataFrame:
    s = sens if split is None else sens[sens["split"] == split]
    rows = []
    for c in [c for c in s.columns if c.startswith("planned_buffer_")]:
        label = c.split("_")[2]
        rows.append({
            "mtt_percentile": label, "mtt_median_min": s[f"mtt_{label}_min"].median(),
            "zero_buffer_pct": 100 * (s[c] == 0).mean(), "buffer_under_15_pct": 100 * (s[c] < 15).mean(),
            "buffer_median_min": s[c].median(), "buffer_mean_min": s[c].mean(),
        })
    return pd.DataFrame(rows).round(2)


# 4. Premise check: pass-through of delay -----------------------------------------------------------


def cluster_slope(x: np.ndarray, y: np.ndarray, cluster: np.ndarray) -> dict:
    """OLS slope of y on x with cluster-robust (by segment) standard error and 95% CI."""
    n = len(x)
    if n < 30:
        return {"n": n, "slope": np.nan, "se": np.nan, "ci_low": np.nan, "ci_high": np.nan, "intercept": np.nan}
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    slope = ((x - xm) * (y - ym)).sum() / sxx
    intercept = ym - slope * xm
    resid = y - (intercept + slope * x)
    score = pd.Series((x - xm) * resid).groupby(cluster).sum()
    g = len(score)
    var = (g / (g - 1)) * (score ** 2).sum() / sxx**2
    se = float(np.sqrt(var))
    return {"n": n, "slope": slope, "se": se, "ci_low": slope - Z95 * se, "ci_high": slope + Z95 * se, "intercept": intercept}


def passthrough_by(turns: pd.DataFrame, by: str, order: list | None = None, split: str = "train") -> pd.DataFrame:
    """Slope of outbound departure delay on inbound arrival delay (inbound late only), by a grouping column."""
    t = turns[(turns["split"] == split) & (turns["inbound_arr_delay_min"] > 0) & (~turns["flag_actual_ground_nonpositive"])]
    rows = []
    keys = order if order is not None else sorted(t[by].dropna().unique())
    for k in keys:
        s = t[t[by] == k]
        r = cluster_slope(s["inbound_arr_delay_min"].to_numpy(float), s["outbound_dep_delay_min"].to_numpy(float),
                          s["segment_id"].to_numpy())
        r.update({by: k, "mean_inbound_delay": s["inbound_arr_delay_min"].mean(),
                  "mean_outbound_dep_delay": s["outbound_dep_delay_min"].mean(),
                  "share_outbound_late15_pct": 100 * (s["outbound_dep_delay_min"] >= 15).mean()})
        rows.append(r)
    out = pd.DataFrame(rows)
    return out[[by, "n", "slope", "ci_low", "ci_high", "intercept", "mean_inbound_delay", "mean_outbound_dep_delay",
                "share_outbound_late15_pct"]].round(3)


def passthrough_by_buffer(turns: pd.DataFrame, split: str = "train") -> pd.DataFrame:
    return passthrough_by(turns, "buffer_bucket", BUFFER_BUCKETS, split)


def passthrough_by_turn_index(turns: pd.DataFrame, split: str = "train") -> pd.DataFrame:
    t = turns.assign(turn_index=turns["turn_index_in_segment"].clip(upper=5).astype(int))
    return passthrough_by(t, "turn_index", [1, 2, 3, 4, 5], split)


def passthrough_by_hour(turns: pd.DataFrame, split: str = "train") -> pd.DataFrame:
    t = turns.assign(hour=pd.to_datetime(turns["outbound_sched_dep_local"]).dt.hour)
    return passthrough_by(t, "hour", list(range(6, 23)), split)


def downstream_by_hour(turns: pd.DataFrame, split: str = "train") -> pd.DataFrame:
    """Mean number of legs after the turn, by outbound departure hour (more early = more to protect)."""
    t = turns[turns["split"] == split].assign(hour=lambda d: pd.to_datetime(d["outbound_sched_dep_local"]).dt.hour)
    return t.groupby("hour").agg(turns=("turn_id", "size"), mean_downstream_legs=("n_downstream_legs_after_turn", "mean")).round(2).reset_index()


def controlled_passthrough(turns: pd.DataFrame, split: str = "train") -> pd.DataFrame:
    """Buffer effect within station class and band (guards against composition effects)."""
    out = []
    for cls in ["high_volume", "other"]:
        r = passthrough_by(turns[turns["station_class"] == cls], "buffer_bucket", BUFFER_BUCKETS, split)
        r.insert(0, "station_class", cls)
        out.append(r)
    return pd.concat(out, ignore_index=True)


# 5. BTS LateAircraftDelay comparison ----------------------------------------------------------------


def late_aircraft_comparison(turns: pd.DataFrame, split: str = "train") -> pd.DataFrame:
    """Aggregate sanity check by inbound delay bin: our observed pass-through vs BTS LateAircraftDelay.

    BTS reports LateAircraftDelay only on arrivals of 15+ minutes, so the comparison is on averages over all
    outbound flights (missing = 0), not flight by flight.
    """
    t = turns[(turns["split"] == split) & (turns["inbound_arr_delay_min"] > 0)].copy()
    t["inbound_delay_bin"] = pd.cut(t["inbound_arr_delay_min"], bins=[0, 15, 30, 60, 120, np.inf],
                                    labels=["1-15", "16-30", "31-60", "61-120", ">120"])
    t["lad"] = t["late_aircraft_delay_outbound_min"].fillna(0)
    t["dep_pass"] = t["outbound_dep_delay_min"].clip(lower=0)
    g = t.groupby("inbound_delay_bin", observed=True)
    return g.agg(
        turns=("turn_id", "size"),
        mean_inbound_arr_delay=("inbound_arr_delay_min", "mean"),
        mean_outbound_dep_delay_floor0=("dep_pass", "mean"),
        mean_bts_late_aircraft_delay_outbound=("lad", "mean"),
        outbound_arr_late15_pct=("outbound_arr_delay_min", lambda s: 100 * (s >= 15).mean()),
        outbound_has_late_aircraft_pct=("lad", lambda s: 100 * (s > 0).mean()),
    ).round(2).reset_index()


# 6. Storm days -------------------------------------------------------------------------------------


def absorption_table(turns: pd.DataFrame, split: str = "train", min_turns: int = 200) -> pd.DataFrame:
    """Outbound departure delay by inbound delay band and planned-buffer bucket (train, inbound late).

    A straight-line slope is a blunt summary because absorption is piecewise (delay up to the buffer is
    absorbed, the rest passes through); this table shows the level directly.
    """
    t = turns[(turns["split"] == split) & (turns["inbound_arr_delay_min"] > 0)
              & (~turns["flag_actual_ground_nonpositive"])].copy()
    t["inbound_delay_band"] = pd.cut(t["inbound_arr_delay_min"], bins=[0, 15, 30, 60, np.inf],
                                     labels=["1-15", "16-30", "31-60", ">60"])
    g = t.groupby(["inbound_delay_band", "buffer_bucket"], observed=True).agg(
        turns=("turn_id", "size"), mean_inbound_delay=("inbound_arr_delay_min", "mean"),
        mean_outbound_dep_delay=("outbound_dep_delay_min", "mean"),
        outbound_dep_late15_pct=("outbound_dep_delay_min", lambda s: 100 * (s >= 15).mean()))
    g = g[g["turns"] >= min_turns].round(1).reset_index()
    g["buffer_bucket"] = pd.Categorical(g["buffer_bucket"], BUFFER_BUCKETS, ordered=True)
    return g.sort_values(["inbound_delay_band", "buffer_bucket"]).reset_index(drop=True)


def high_cancel_days(legs: pd.DataFrame) -> pd.DataFrame:
    d = legs.groupby("flight_date").agg(
        flights=("flight_id", "size"), cancelled=("cancelled", "sum"),
        usable_pct=("usable", lambda s: 100 * s.mean()), storm_day_flag=("storm_day_flag", "first"),
        high_cancel_day=("high_cancel_day", "first"))
    d["cancelled_pct"] = (100 * d["cancelled"] / d["flights"]).round(1)
    d["usable_pct"] = d["usable_pct"].round(1)
    return d[d["high_cancel_day"]].reset_index()


def storm_window(legs: pd.DataFrame, turns: pd.DataFrame, start="2026-01-20", end="2026-02-02") -> pd.DataFrame:
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    ld = pd.to_datetime(legs["flight_date"])
    d = legs[(ld >= lo) & (ld <= hi)]
    a = d.groupby("flight_date").agg(flights=("flight_id", "size"), cancelled_pct=("cancelled", lambda s: 100 * s.mean()),
                                     usable_pct=("usable", lambda s: 100 * s.mean()),
                                     storm_day_flag=("storm_day_flag", "first"), high_cancel_day=("high_cancel_day", "first"))
    td = pd.to_datetime(turns["outbound_flight_date"])
    t = turns[(td >= lo) & (td <= hi)]
    b = t.groupby("outbound_flight_date").agg(turns=("turn_id", "size"), mean_outbound_dep_delay=("outbound_dep_delay_min", "mean"),
                                             storm_flag_turns=("storm_day_flag", "sum"))
    out = a.join(b, how="left").fillna({"turns": 0, "storm_flag_turns": 0})
    return out.round(1).reset_index(names="flight_date")
