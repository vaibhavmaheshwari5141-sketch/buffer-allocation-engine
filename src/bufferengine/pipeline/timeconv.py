"""Time handling: BTS hhmm integers, minutes after midnight, and local-to-UTC conversion.

BTS conventions (from the BTS field definitions):
- CRSDepTime / CRSArrTime / DepTime / ArrTime are local clock times as hhmm integers (e.g. 705 = 07:05).
- 2400 means midnight at the end of the day; it is kept as 1440 minutes so it sorts after 2359.
- FlightDate is the local scheduled departure date at the origin.
- The scheduled arrival is on FlightDate or a later day; the day is not given, so it is inferred as the
  first local arrival date that puts the arrival after the departure in UTC.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def hhmm_to_minutes(hhmm) -> pd.Series:
    """hhmm integer(s) -> minutes after midnight (Int64). 2400 -> 1440. Invalid or missing -> <NA>."""
    s = pd.to_numeric(pd.Series(hhmm), errors="coerce")
    h, m = s // 100, s % 100
    ok = s.notna() & (s >= 0) & (m < 60) & ((h < 24) | (s == 2400))
    out = (h * 60 + m).where(ok)
    return out.round().astype("Int64")


def _localize(naive: pd.Series, tz: pd.Series) -> pd.Series:
    """Localise naive local datetimes, each with its own IANA zone, and return UTC.

    DST: a nonexistent local time (spring-forward gap) is shifted forward to the first valid instant;
    an ambiguous local time (fall-back hour) takes the first occurrence (daylight time).
    """
    out = pd.Series(pd.NaT, index=naive.index, dtype="datetime64[ns, UTC]")
    valid = naive.notna() & tz.notna()
    for zone, idx in naive[valid].groupby(tz[valid]).groups.items():
        local = naive.loc[idx].dt.tz_localize(
            zone, nonexistent="shift_forward", ambiguous=np.ones(len(idx), dtype=bool)
        )
        out.loc[idx] = local.dt.tz_convert("UTC")
    return out


def scheduled_times_utc(
    flight_date: pd.Series,
    crs_dep_min: pd.Series,
    crs_arr_min: pd.Series,
    origin_tz: pd.Series,
    dest_tz: pd.Series,
) -> pd.DataFrame:
    """Scheduled departure and arrival in UTC, plus the inferred arrival day offset (0, 1, ...).

    Inputs are aligned Series: FlightDate (date), CRS minutes after midnight, and IANA zone per row.
    """
    day = pd.to_datetime(flight_date).dt.normalize()
    dep_local = day + pd.to_timedelta(crs_dep_min.astype("float64"), unit="min")
    dep_utc = _localize(dep_local, origin_tz)

    offset = pd.Series(0, index=day.index, dtype="int64")
    arr_utc = _localize(day + pd.to_timedelta(crs_arr_min.astype("float64"), unit="min"), dest_tz)
    # Roll the arrival date forward while it lands before departure (overnight / long eastbound legs).
    for _ in range(2):
        behind = arr_utc.notna() & dep_utc.notna() & (arr_utc < dep_utc)
        if not behind.any():
            break
        offset[behind] += 1
        arr_local = day[behind] + pd.to_timedelta(offset[behind], unit="D") + pd.to_timedelta(
            crs_arr_min[behind].astype("float64"), unit="min"
        )
        arr_utc.loc[behind] = _localize(arr_local, dest_tz[behind])

    return pd.DataFrame(
        {
            "crs_dep_local": dep_local,
            "crs_dep_utc": dep_utc,
            "crs_arr_local": day + pd.to_timedelta(offset, unit="D")
            + pd.to_timedelta(crs_arr_min.astype("float64"), unit="min"),
            "crs_arr_utc": arr_utc,
            "arr_day_offset": offset,
            "crs_block_min_utc": (arr_utc - dep_utc).dt.total_seconds() / 60,
        }
    )
