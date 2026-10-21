"""Minimum turnaround time (MTT): an ASSUMPTION estimated from TRAIN-period data only.

BTS has no turnaround standard. The idea: when the inbound flight arrived late AND the outbound flight left
late, the aircraft was probably turned as fast as it could be, so the low tail of the *actual* ground time
on such turns approximates the minimum. The estimate is a low percentile of that distribution:

  1. per station and time-of-day band, if the cell has at least ``min_n`` pressured train turns;
  2. otherwise per station class (high_volume / other) and band, if that cell has at least ``min_n``;
  3. otherwise one global value.

Every estimate is clipped to [floor_min, cap_min]. Only rows with split == 'train' are ever read.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

DEFAULT_MTT_PERCENTILE = 0.10
SENSITIVITY_PERCENTILES = (0.05, 0.10, 0.20)
TOD_BANDS = ["morning", "midday", "afternoon", "night"]


@dataclass(frozen=True)
class MttSettings:
    percentile: float = DEFAULT_MTT_PERCENTILE
    min_n: int = 50  # minimum pressured train turns in a cell
    floor_min: float = 25.0  # no MTT below this
    cap_min: float = 60.0  # no MTT above this
    high_volume_min_daily_turns: float = 20.0  # train turns per day for the high_volume class

    def with_percentile(self, percentile: float) -> "MttSettings":
        return replace(self, percentile=percentile)


def tod_band(hour: pd.Series) -> pd.Series:
    """Local hour of the outbound scheduled departure -> band. 05-09 morning, 10-14 midday, 15-19 afternoon,
    20-04 night."""
    h = pd.Series(hour).astype("int64")
    out = np.select([(h >= 5) & (h < 10), (h >= 10) & (h < 15), (h >= 15) & (h < 20)],
                    ["morning", "midday", "afternoon"], default="night")
    return pd.Series(out, index=h.index, dtype="string")


def add_station_class(turns: pd.DataFrame, settings: MttSettings) -> pd.DataFrame:
    """station_class = 'high_volume' when the station averages >= high_volume_min_daily_turns TRAIN turns per day
    (13 stations at 20/day: the AA hubs DFW, CLT, PHX, ORD, PHL, MIA, LAX, DCA plus MCO, LGA, LAS, BOS, TPA;
    JFK is just under at 19.6). Data-driven, so it is a volume class, not an official hub list."""
    t = turns.copy()
    train = t[t["split"] == "train"]
    days = max(train["outbound_flight_date"].nunique(), 1)
    daily = train.groupby("station").size() / days
    big = set(daily[daily >= settings.high_volume_min_daily_turns].index)
    t["station_class"] = np.where(t["station"].isin(big), "high_volume", "other")
    return t


def pressured_train_turns(turns: pd.DataFrame) -> pd.DataFrame:
    """Train turns where the aircraft was under time pressure and the ground time is physically valid."""
    m = (
        (turns["split"] == "train")
        & (turns["inbound_arr_delay_min"] > 0)
        & (turns["outbound_dep_delay_min"] > 0)
        & (turns["actual_ground_min"] > 0)
    )
    return turns.loc[m]


def _cell(df: pd.DataFrame, keys: list[str], pct: float) -> pd.DataFrame:
    g = df.groupby(keys, observed=True)["actual_ground_min"]
    return g.quantile(pct).rename("raw_min").to_frame().join(g.size().rename("n")).reset_index()


def estimate_mtt(turns: pd.DataFrame, settings: MttSettings) -> pd.DataFrame:
    """Estimate table: one row per cell and level, with n, raw percentile and the clipped MTT."""
    p = pressured_train_turns(turns)
    pct = settings.percentile
    st = _cell(p, ["station", "tod_band"], pct).assign(level="station_band")
    cl = _cell(p, ["station_class", "tod_band"], pct).assign(level="class_band")
    gl = pd.DataFrame({"raw_min": [p["actual_ground_min"].quantile(pct) if len(p) else np.nan], "n": [len(p)],
                       "level": "global"})
    est = pd.concat([st, cl, gl], ignore_index=True)
    est["mtt_min"] = est["raw_min"].clip(settings.floor_min, settings.cap_min).fillna(settings.floor_min)
    est["used"] = np.where(est["level"] == "global", True, est["n"] >= settings.min_n)
    est["percentile"] = pct
    cols = ["level", "station", "station_class", "tod_band", "n", "raw_min", "mtt_min", "used", "percentile"]
    for c in cols:
        if c not in est:
            est[c] = pd.NA
    return est[cols]


def apply_mtt(turns: pd.DataFrame, est: pd.DataFrame, settings: MttSettings) -> pd.DataFrame:
    """Attach mtt_min and mtt_source to every turn using the fallback chain (row order is preserved)."""
    t = turns.copy()
    used = est[est["used"].astype(bool)]
    sb = used[used["level"] == "station_band"][["station", "tod_band", "mtt_min"]].rename(columns={"mtt_min": "m_sb"})
    cb = used[used["level"] == "class_band"][["station_class", "tod_band", "mtt_min"]].rename(columns={"mtt_min": "m_cb"})
    g = used[used["level"] == "global"]["mtt_min"]
    m_global = float(g.iloc[0]) if len(g) else settings.floor_min
    t = t.merge(sb, on=["station", "tod_band"], how="left").merge(cb, on=["station_class", "tod_band"], how="left")
    t["mtt_source"] = np.where(t["m_sb"].notna(), "station_band", np.where(t["m_cb"].notna(), "class_band", "global"))
    t["mtt_min"] = t["m_sb"].fillna(t["m_cb"]).fillna(m_global).astype("float64")
    return t.drop(columns=["m_sb", "m_cb"])
