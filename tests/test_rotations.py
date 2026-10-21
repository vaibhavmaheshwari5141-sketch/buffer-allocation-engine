import duckdb
import numpy as np
import pandas as pd
import pytest

from bufferengine.config import base_table_path, processed_dir
from bufferengine.pipeline import build_rotations as B
from bufferengine.pipeline.mtt import MttSettings, apply_mtt, estimate_mtt, tod_band
from bufferengine.pipeline.timeconv import hhmm_to_minutes

TZ = {"DFW": "America/Chicago", "ORD": "America/Chicago", "MIA": "America/New_York", "ATL": "America/New_York",
      "LAX": "America/Los_Angeles", "JFK": "America/New_York", "BOS": "America/New_York"}


def r(tail, date, fn, o, d, dep, arr, **kw):
    """One scheduled flight; dep and arr are local hhmm. Optional: cancelled, diverted, dd, ad, lad."""
    return {"tail": tail, "date": date, "fn": fn, "o": o, "d": d, "dep": dep, "arr": arr, **kw}


def make_inputs(rows, tmp_path):
    recs = []
    for x in rows:
        recs.append({
            "FlightDate": pd.Timestamp(x["date"]), "Tail_Number": x["tail"],
            "Flight_Number_Reporting_Airline": x["fn"], "Origin": x["o"], "Dest": x["d"],
            "CRSDepTime": x["dep"], "CRSArrTime": x["arr"],
            "crs_dep_min": hhmm_to_minutes([x["dep"]]).iloc[0], "crs_arr_min": hhmm_to_minutes([x["arr"]]).iloc[0],
            "CRSElapsedTime": 120.0, "Distance": 800.0, "DepDelay": float(x.get("dd", 0)),
            "ArrDelay": float(x.get("ad", 0)), "ArrDel15": int(x.get("ad", 0) >= 15),
            "LateAircraftDelay": x.get("lad"), "Cancelled": x.get("cancelled", 0), "Diverted": x.get("diverted", 0),
        })
    base = tmp_path / "base.parquet"
    pd.DataFrame(recs).to_parquet(base)
    airports = tmp_path / "airports_tz.csv"
    pd.DataFrame({"iata": list(TZ), "timezone": list(TZ.values())}).to_csv(airports, index=False)
    return base, airports


def chain(rows, tmp_path):
    base, airports = make_inputs(rows, tmp_path)
    flights = B.load_flights(base, airports)
    legs, breaks = B.chain_legs(B.connect(), flights)
    return legs.set_index("flight_number"), breaks, flights


def turns_of(rows, tmp_path):
    base, airports = make_inputs(rows, tmp_path)
    con = B.connect()
    legs, _ = B.chain_legs(con, B.load_flights(base, airports))
    return B.build_turns(con, legs)


# Chains ----------------------------------------------------------------------------------------------


def test_clean_chain_is_one_usable_segment(tmp_path):
    legs, breaks, _ = chain([r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930),
                             r("N1", "2025-06-01", 2, "ORD", "DFW", 1030, 1200)], tmp_path)
    assert legs.loc[[1, 2], "category"].tolist() == ["usable_segment"] * 2
    assert legs.loc[[1, 2], "leg_idx"].tolist() == [1, 2]
    assert legs.loc[[1, 2], "n_downstream_legs"].tolist() == [1, 0]
    assert legs.loc[1, "segment_id"] == legs.loc[2, "segment_id"]
    assert legs.loc[1, "break_reason_after"] == "linked" and legs.loc[2, "break_reason_before"] == "linked"
    assert breaks.empty


def test_airport_mismatch_breaks_chain(tmp_path):
    legs, breaks, _ = chain([r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930),
                             r("N1", "2025-06-01", 2, "MIA", "ATL", 1200, 1330)], tmp_path)
    assert (legs["category"] == "single_leg_segment").all()
    assert breaks["reason"].tolist() == ["airport_mismatch"]


def test_swap_suspect_breaks_chain_and_is_counted(tmp_path):
    # The second flight is scheduled to leave ORD at 09:20, before the aircraft is scheduled to arrive (09:30).
    legs, breaks, _ = chain([r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930),
                             r("N1", "2025-06-01", 2, "ORD", "DFW", 920, 1100)], tmp_path)
    assert (legs["category"] == "single_leg_segment").all()
    assert breaks["reason"].tolist() == ["swap_suspect"]
    assert breaks["gap_min"].iloc[0] == -10 and bool(breaks["negative_gap"].iloc[0])


def test_cancelled_flight_breaks_chain_and_is_not_removed(tmp_path):
    legs, breaks, _ = chain([r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930),
                             r("N1", "2025-06-01", 2, "ORD", "DFW", 1030, 1200, cancelled=1),
                             r("N1", "2025-06-01", 3, "DFW", "ORD", 1300, 1430)], tmp_path)
    assert legs.loc[2, "category"] == "cancelled"
    # Removing the cancelled flight would link 1 and 3 (DFW -> ORD, then DFW -> ORD does not even connect, but
    # the point is that nothing is chained through it): both stay single-leg.
    assert legs.loc[1, "category"] == "single_leg_segment" and legs.loc[3, "category"] == "single_leg_segment"
    assert set(breaks["reason"]) == {"cancelled"} and len(breaks) == 2
    assert legs.loc[1, "break_reason_after"] == "cancelled" and legs.loc[3, "break_reason_before"] == "cancelled"
    assert legs.loc[1, "n_cancelled_in_tail_day"] == 1


def test_cancelled_flight_between_connecting_legs_is_not_chained_through(tmp_path):
    # 1: DFW->ORD, 2: ORD->DFW (cancelled), 3: ORD->DFW would connect to 1 if 2 were removed.
    legs, _, _ = chain([r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930),
                        r("N1", "2025-06-01", 2, "ORD", "DFW", 1030, 1200, cancelled=1),
                        r("N1", "2025-06-01", 3, "ORD", "DFW", 1100, 1230)], tmp_path)
    assert legs.loc[1, "category"] == "single_leg_segment" and legs.loc[3, "category"] == "single_leg_segment"


def test_diverted_flight_breaks_chain(tmp_path):
    legs, breaks, _ = chain([r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930, diverted=1),
                             r("N1", "2025-06-01", 2, "ORD", "DFW", 1030, 1200)], tmp_path)
    assert legs.loc[1, "category"] == "diverted" and legs.loc[2, "category"] == "single_leg_segment"
    assert breaks["reason"].tolist() == ["diverted"]


def test_missing_tail_category_and_cancelled_priority(tmp_path):
    legs, _, _ = chain([r(None, "2025-06-01", 1, "DFW", "ORD", 800, 930),
                        r(None, "2025-06-01", 2, "DFW", "ORD", 900, 1030, cancelled=1)], tmp_path)
    assert legs.loc[1, "category"] == "missing_tail" and legs.loc[2, "category"] == "cancelled"


def test_midnight_crossing_keeps_operating_day_and_long_rest_starts_new_day(tmp_path):
    # Red-eye LAX 22:00 PST -> JFK 06:30 EST next day; 60-minute turn to BOS; then more than 5 h of rest.
    rows = [r("N1", "2025-01-10", 1, "LAX", "JFK", 2200, 630),
            r("N1", "2025-01-11", 2, "JFK", "BOS", 730, 830),
            r("N1", "2025-01-11", 3, "BOS", "JFK", 2000, 2100)]
    legs, _, _ = chain(rows, tmp_path)
    assert legs.loc[[1, 2], "segment_id"].nunique() == 1 and legs.loc[2, "leg_idx"] == 2
    assert str(legs.loc[1, "operating_day"]) == str(legs.loc[2, "operating_day"]) == "2025-01-10"
    assert str(legs.loc[3, "operating_day"]) == "2025-01-11"
    assert legs.loc[1, "segment_id"] != legs.loc[3, "segment_id"]
    assert legs.loc[3, "break_reason_before"] == "long_rest" and legs.loc[2, "break_reason_after"] == "long_rest"


# Ground time -----------------------------------------------------------------------------------------


def test_ground_time_across_dst_spring_forward(tmp_path):
    # ORD on 2025-03-09: arrive 01:30 CST (07:30 UTC), depart 03:30 CDT (08:30 UTC): 60 minutes, not 120.
    turns = turns_of([r("N1", "2025-03-09", 1, "DFW", "ORD", 30, 130),
                      r("N1", "2025-03-09", 2, "ORD", "DFW", 330, 500)], tmp_path)
    assert turns["sched_ground_min"].tolist() == [60.0]


def test_ground_time_across_dst_fall_back(tmp_path):
    # 2025-11-02: arrive 00:30 CDT (05:30 UTC), depart 02:30 CST (08:30 UTC): 180 minutes, not 120.
    turns = turns_of([r("N1", "2025-11-02", 1, "DFW", "ORD", 0, 30),
                      r("N1", "2025-11-02", 2, "ORD", "DFW", 230, 400)], tmp_path)
    assert turns["sched_ground_min"].tolist() == [180.0]


def test_overnight_ground_time_in_utc(tmp_path):
    # Turn at JFK: arrive 06:30 (next day, red-eye) and depart 07:30 -> 60 minutes.
    turns = turns_of([r("N1", "2025-01-10", 1, "LAX", "JFK", 2200, 630),
                      r("N1", "2025-01-11", 2, "JFK", "BOS", 730, 830)], tmp_path)
    assert turns["sched_ground_min"].tolist() == [60.0]
    assert turns["n_downstream_legs_after_turn"].tolist() == [1]


def test_actual_ground_time_uses_delays(tmp_path):
    turns = turns_of([r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930, dd=10, ad=20),
                      r("N1", "2025-06-01", 2, "ORD", "DFW", 1030, 1200, dd=25)], tmp_path)
    # actual arrival 09:50, actual departure 10:55 -> 65 minutes (scheduled 60).
    assert turns["actual_ground_min"].tolist() == [65.0] and turns["sched_ground_min"].tolist() == [60.0]


# Reconciliation and idempotence ----------------------------------------------------------------------


def test_reconciliation_adds_up_and_detects_problems(tmp_path):
    rows = [r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930), r("N1", "2025-06-01", 2, "ORD", "DFW", 1030, 1200),
            r("N2", "2025-06-01", 3, "DFW", "ORD", 800, 930), r(None, "2025-06-01", 4, "DFW", "ORD", 800, 930),
            r("N3", "2025-06-01", 5, "DFW", "ORD", 800, 930, cancelled=1)]
    legs, _, flights = chain(rows, tmp_path)
    rec = B.reconciliation(legs.reset_index(), base_total=len(flights))
    assert rec["flights"].sum() == 5
    assert dict(zip(rec["category"], rec["flights"])) == {
        "usable_segment": 2, "single_leg_segment": 1, "cancelled": 1, "diverted": 0, "missing_tail": 1, "other": 0}
    with pytest.raises(ValueError):
        B.reconciliation(legs.reset_index(), base_total=len(flights) + 1)
    broken = legs.reset_index().copy()
    broken.loc[0, "category"] = None
    with pytest.raises(ValueError):
        B.reconciliation(broken)


def test_build_is_idempotent(tmp_path):
    rows = [r("N1", "2025-06-01", 1, "DFW", "ORD", 800, 930, dd=20, ad=25),
            r("N1", "2025-06-01", 2, "ORD", "DFW", 1030, 1200, dd=15, ad=10),
            r("N1", "2025-06-01", 3, "DFW", "ORD", 1300, 1430), r("N2", "2025-06-02", 4, "DFW", "MIA", 800, 1100)]
    base, airports = make_inputs(rows, tmp_path)
    a = B.build_all(base, airports)
    b = B.build_all(base, airports)
    for name in ["legs", "turns", "breaks", "mtt_estimates", "mtt_sens", "reconciliation", "rule_comparison"]:
        pd.testing.assert_frame_equal(getattr(a, name), getattr(b, name))


# MTT -------------------------------------------------------------------------------------------------


def turns_frame(n, station="AAA", band="midday", ground=45.0, split="train", cls="other"):
    return pd.DataFrame({
        "station": station, "tod_band": band, "station_class": cls, "split": split,
        "inbound_arr_delay_min": 10.0, "outbound_dep_delay_min": 10.0,
        "actual_ground_min": np.linspace(ground, ground + 20, n),
        "outbound_flight_date": pd.Timestamp("2025-03-01").date(),
    })


def test_mtt_fallback_station_then_class_then_global():
    s = MttSettings(percentile=0.10, min_n=50, floor_min=25, cap_min=60)
    t = pd.concat([turns_frame(200, "BIG", ground=40), turns_frame(10, "TINY", ground=30)], ignore_index=True)
    est = estimate_mtt(t, s)
    new = turns_frame(1, "NEW", band="night")
    out = apply_mtt(pd.concat([t, new], ignore_index=True), est, s)
    src = out.groupby("station")["mtt_source"].first()
    assert src["BIG"] == "station_band"   # 200 pressured turns >= min_n
    assert src["TINY"] == "class_band"    # only 10 turns: falls back to the class x band cell (210 turns)
    assert src["NEW"] == "global"         # unseen station and band
    assert len(out) == 211


def test_mtt_floor_cap_and_percentile():
    s = MttSettings(percentile=0.10, min_n=10, floor_min=25, cap_min=60)
    low = estimate_mtt(turns_frame(100, ground=10.0), s)      # raw p10 about 12 -> floored
    assert low.loc[low["level"] == "station_band", "mtt_min"].iloc[0] == 25
    high = estimate_mtt(turns_frame(100, ground=90.0), s)     # raw p10 about 92 -> capped
    assert high.loc[high["level"] == "station_band", "mtt_min"].iloc[0] == 60
    mid = estimate_mtt(turns_frame(101, ground=40.0), s)      # grounds 40..60 evenly: p10 = 42
    assert mid.loc[mid["level"] == "station_band", "raw_min"].iloc[0] == pytest.approx(42.0)


def test_mtt_uses_only_train_period_and_pressured_turns():
    s = MttSettings(percentile=0.10, min_n=10, floor_min=25, cap_min=60)
    train = turns_frame(100, ground=40.0)
    noise = pd.concat([
        turns_frame(100, ground=1.0, split="test"),
        turns_frame(100, ground=1.0, split="validate"),
        turns_frame(100, ground=1.0).assign(inbound_arr_delay_min=-5.0),    # inbound not late
        turns_frame(100, ground=1.0).assign(outbound_dep_delay_min=0.0),    # outbound not late
        turns_frame(100, ground=-50.0),                                     # impossible ground time
    ])
    base = estimate_mtt(train, s)
    both = estimate_mtt(pd.concat([train, noise], ignore_index=True), s)
    pd.testing.assert_frame_equal(base.reset_index(drop=True), both.reset_index(drop=True))


def test_tod_band_boundaries():
    assert tod_band(pd.Series([4, 5, 9, 10, 14, 15, 19, 20, 23, 0])).tolist() == [
        "night", "morning", "morning", "midday", "midday", "afternoon", "afternoon", "night", "night", "night"]


# Real outputs (skipped when the build has not been run on this machine) -------------------------------

LEGS_PATH = processed_dir() / B.LEGS_FILE


@pytest.mark.skipif(not (LEGS_PATH.exists() and base_table_path().exists()), reason="rotation outputs not built")
def test_real_reconciliation_adds_up_to_base_total():
    total = duckdb.sql(f"SELECT COUNT(*) FROM read_parquet('{base_table_path().as_posix()}')").fetchone()[0]
    legs = pd.read_parquet(LEGS_PATH, columns=["flight_id", "category", "usable", "segment_id", "n_legs"])
    rec = B.reconciliation(legs, base_total=total)
    assert rec["flights"].sum() == total == len(legs)
    assert legs["flight_id"].is_unique
    seg = legs[legs["segment_id"].notna()].drop_duplicates("segment_id")
    usable_segments = set(legs.loc[legs["usable"], "segment_id"])
    assert ((seg["n_legs"] >= 2) == seg["segment_id"].isin(usable_segments)).all()


@pytest.mark.skipif(not LEGS_PATH.exists(), reason="rotation outputs not built")
def test_real_train_mtt_estimates_do_not_depend_on_other_splits():
    t = pd.read_parquet(processed_dir() / B.TURNS_FILE)
    s = MttSettings()
    train = t[t["split"] == "train"]
    pd.testing.assert_frame_equal(estimate_mtt(t, s).reset_index(drop=True), estimate_mtt(train, s).reset_index(drop=True))


def test_segment_ids_are_unique_even_for_simultaneous_flights(tmp_path):
    # The same aircraft is scheduled on two flights at the same UTC minute in different cities (a BTS data quirk).
    legs, _, _ = chain([r("N1", "2025-06-01", 1, "LAX", "JFK", 700, 1530),
                        r("N1", "2025-06-01", 2, "DFW", "ORD", 900, 1030),
                        r("N1", "2025-06-01", 3, "ORD", "DFW", 1130, 1300)], tmp_path)
    assert legs.loc[1, "segment_id"] != legs.loc[2, "segment_id"]
    assert legs.loc[2, "segment_id"] == legs.loc[3, "segment_id"] and legs.loc[2, "n_legs"] == 2
