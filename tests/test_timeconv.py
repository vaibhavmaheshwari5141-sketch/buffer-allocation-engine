import pandas as pd
import pytest

from bufferengine.pipeline.timeconv import hhmm_to_minutes, scheduled_times_utc


@pytest.mark.parametrize(
    "hhmm, expected",
    [(0, 0), (5, 5), (705, 425), (1200, 720), (2359, 1439), (2400, 1440)],
)
def test_hhmm_to_minutes_valid(hhmm, expected):
    assert hhmm_to_minutes([hhmm]).iloc[0] == expected


@pytest.mark.parametrize("bad", [2460, 2401, 160, -1, None])
def test_hhmm_to_minutes_invalid_is_na(bad):
    assert pd.isna(hhmm_to_minutes([bad]).iloc[0])


def _utc(date, dep, arr, origin_tz, dest_tz):
    df = scheduled_times_utc(
        pd.Series([date]), hhmm_to_minutes([dep]), hhmm_to_minutes([arr]), pd.Series([origin_tz]), pd.Series([dest_tz])
    )
    return df.iloc[0]


def test_same_day_flight_utc():
    # DFW 08:00 CDT (UTC-5) -> ORD 10:15 CDT, summer.
    r = _utc("2025-07-01", 800, 1015, "America/Chicago", "America/Chicago")
    assert r.crs_dep_utc == pd.Timestamp("2025-07-01 13:00", tz="UTC")
    assert r.crs_arr_utc == pd.Timestamp("2025-07-01 15:15", tz="UTC")
    assert r.arr_day_offset == 0


def test_overnight_red_eye_arrives_next_day():
    # LAX 22:00 PST -> JFK 06:30 EST next morning; 330 min block.
    r = _utc("2025-01-01", 2200, 630, "America/Los_Angeles", "America/New_York")
    assert r.arr_day_offset == 1
    assert r.crs_arr_utc == pd.Timestamp("2025-01-02 11:30", tz="UTC")
    assert r.crs_block_min_utc == 330


def test_departure_after_midnight_local_date_is_flight_date():
    # FlightDate is the local departure date, so 00:30 belongs to that date, not the previous one.
    r = _utc("2025-01-02", 30, 600, "America/Los_Angeles", "America/Chicago")
    assert r.crs_dep_utc == pd.Timestamp("2025-01-02 08:30", tz="UTC")
    assert r.arr_day_offset == 0


def test_dst_spring_forward_overnight():
    # Night of 2025-03-08/09 (US DST starts 02:00 on 9 Mar). LAX 23:30 PST -> JFK 07:55 EDT.
    r = _utc("2025-03-08", 2330, 755, "America/Los_Angeles", "America/New_York")
    assert r.crs_dep_utc == pd.Timestamp("2025-03-09 07:30", tz="UTC")
    assert r.crs_arr_utc == pd.Timestamp("2025-03-09 11:55", tz="UTC")
    assert r.crs_block_min_utc == 265


def test_dst_spring_forward_morning():
    # 2025-03-09: ORD 01:30 CST (before the change) -> MIA 06:00 EDT (after it) = 150 min.
    r = _utc("2025-03-09", 130, 600, "America/Chicago", "America/New_York")
    assert r.crs_block_min_utc == 150


def test_dst_nonexistent_local_time_shifts_forward():
    # 02:30 does not exist in New York on 2025-03-09; it is treated as 03:00 EDT = 07:00 UTC.
    r = _utc("2025-03-09", 230, 400, "America/New_York", "America/New_York")
    assert r.crs_dep_utc == pd.Timestamp("2025-03-09 07:00", tz="UTC")


def test_dst_fall_back_ambiguous_takes_daylight_time():
    # 2025-11-02 01:20 occurs twice in Chicago; the first (CDT, UTC-5) is used.
    r = _utc("2025-11-02", 120, 400, "America/Chicago", "America/Chicago")
    assert r.crs_dep_utc == pd.Timestamp("2025-11-02 06:20", tz="UTC")


def test_arizona_has_no_dst():
    # PHX stays UTC-7 all year; LAX is UTC-8 in winter and UTC-7 in summer.
    winter = _utc("2025-01-15", 820, 855, "America/Phoenix", "America/Los_Angeles")
    summer = _utc("2025-07-15", 820, 855, "America/Phoenix", "America/Los_Angeles")
    assert winter.crs_dep_utc.hour == 15 and summer.crs_dep_utc.hour == 15
    assert winter.crs_block_min_utc == 95
    assert summer.crs_block_min_utc == 35


def test_hawaii_overnight_to_dallas():
    r = _utc("2025-01-01", 1846, 600, "Pacific/Honolulu", "America/Chicago")
    assert r.arr_day_offset == 1
    assert r.crs_block_min_utc == 434


def test_missing_timezone_gives_nat():
    r = _utc("2025-01-01", 800, 1000, None, "America/Chicago")
    assert pd.isna(r.crs_dep_utc)
