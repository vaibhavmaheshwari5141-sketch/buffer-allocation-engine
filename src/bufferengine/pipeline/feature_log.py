"""Feature log: every derived feature built so far, plus candidate schedule-only features for the prediction model.

Writes docs/feature_log.csv and docs/feature_log.md. Columns: name, status (built / planned), logic, source_columns, kind
(real / derived / assumption / model), leakage_risk (none / low / review / outcome), allowed_as_prediction_feature
(yes / no / review), reason.

Agreed leakage notes: leg position and downstream legs come from the realised chain and need review; historical delay
rates must be computed from the training window only.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

COLS = ["name", "status", "logic", "source_columns", "kind", "leakage_risk", "allowed_as_prediction_feature", "reason"]

B, P = "built", "planned"
FEATURES: list[tuple] = [
    # ---- Phase 1: base and clean table
    ("crs_dep_min", B, "CRSDepTime hhmm to minutes after local midnight (2400 = 1440)", "CRSDepTime", "derived", "none", "yes", "Schedule only; known before departure"),
    ("crs_arr_min", B, "CRSArrTime hhmm to minutes after local midnight", "CRSArrTime", "derived", "none", "yes", "Schedule only"),
    ("crs_dep_utc", B, "FlightDate + CRSDepTime at the origin, converted to UTC with the airport IANA zone (DST handled)", "FlightDate, CRSDepTime, Origin, airports_tz", "derived", "none", "yes", "Schedule only; zone from public reference data"),
    ("crs_arr_utc", B, "Arrival date inferred (first date after departure in UTC), CRSArrTime at the destination, to UTC", "FlightDate, CRSArrTime, Dest, airports_tz", "derived", "none", "yes", "Schedule only"),
    ("flight_id", B, "YYYYMMDD-flightnumber-origin-dest-CRSDepTime", "FlightDate, Flight_Number_Reporting_Airline, Origin, Dest, CRSDepTime", "derived", "none", "no", "Unique key, not a predictor"),
    ("Tail_Number (normalised)", B, "Trim, upper-case, empty to null", "Tail_Number", "real", "review", "no", "As-flown aircraft; can reflect delay-driven swaps. Used as the rotation key only"),
    ("is_operated", B, "Cancelled = 0 and Diverted = 0", "Cancelled, Diverted", "derived", "outcome", "no", "Known only after the day"),
    ("struct_null_cancelled / struct_null_diverted / struct_null_delay_cause", B, "Rows whose columns are structurally null by BTS rules", "Cancelled, Diverted, ArrDelay", "derived", "outcome", "no", "Defined from outcomes"),
    ("dq_missing_tail", B, "Tail_Number is null", "Tail_Number", "derived", "outcome", "no", "Data-quality flag"),
    ("dq_extreme_delay_24h", B, "DepDelay or ArrDelay above 1440 min", "DepDelay, ArrDelay", "derived", "outcome", "no", "Defined from outcomes"),
    ("dq_time_conflict", B, "UTC block differs from CRSElapsedTime, AirTime > ActualElapsedTime, ArrDel15 inconsistent, etc.", "several", "derived", "outcome", "no", "Data-quality flag using actuals"),
    ("dq_duplicate_key", B, "Another row has the same FlightDate, flight number, Origin, Dest, CRSDepTime", "key columns", "derived", "none", "no", "Data-quality flag"),
    ("dq_outlier_other", B, "Taxi over 180 min, departure over 60 min early, elapsed vs schedule over 180 min, invalid distance", "TaxiOut, TaxiIn, DepDelay, ActualElapsedTime, Distance", "derived", "outcome", "no", "Defined from actuals"),
    ("split", B, "train to 2025-09-30, validate 2025-10 to 2025-12, test 2026-01 to 2026-08, by FlightDate", "FlightDate", "derived", "none", "no", "Partition label, never an input"),
    ("arr_delay_min_capped", B, "ArrDelay clipped to train-period quantiles 0.5% / 99.5%", "ArrDelay, split", "derived", "outcome", "no", "Outcome (target-side); thresholds from train only"),
    # ---- Phase 2: rotations, turns, MTT, buffer
    ("arr_day_offset", B, "Days between departure date and inferred arrival date (0, 1)", "crs_dep_utc, crs_arr_utc", "derived", "none", "yes", "Schedule only"),
    ("flag_overnight_arrival", B, "arr_day_offset > 0", "arr_day_offset", "derived", "none", "yes", "Schedule only"),
    ("crs_dep_local / crs_arr_local", B, "Scheduled times as local clock timestamps", "FlightDate, CRS times", "derived", "none", "yes", "Schedule only"),
    ("rotation_id", B, "{tail_number}_{flight_date}", "Tail_Number, FlightDate", "derived", "review", "no", "Built from as-flown tail; key only"),
    ("operating_day", B, "FlightDate of the first flight of a run with no rest over 300 min", "Tail_Number, crs_dep_utc, crs_arr_utc", "derived", "review", "no", "Depends on the realised tail sequence"),
    ("segment_id", B, "{tail}_{first leg flight_id} of a run of linked operated legs", "chain", "derived", "review", "no", "Realised chain; depends on cancellations and swaps"),
    ("leg_idx", B, "Position of the leg in its segment, 1-based", "chain", "derived", "review", "review", "Realised chain: depends on same-day cancellations and swaps. Needs a leakage review; allowed for the optimiser (planned = operated assumption)"),
    ("n_legs", B, "Legs in the segment", "chain", "derived", "review", "review", "Realised chain: needs a leakage review"),
    ("n_segments", B, "Segments in the tail-day", "chain", "derived", "review", "review", "Realised chain"),
    ("n_downstream_legs", B, "n_legs - leg_idx", "chain", "derived", "review", "review", "Realised chain: a lower bound on planned exposure; needs a leakage review. Allowed for the optimiser"),
    ("n_cancelled_in_tail_day", B, "Cancelled flights of the tail on the date", "Cancelled, Tail_Number", "derived", "outcome", "no", "Defined from outcomes"),
    ("is_first_leg / is_last_leg", B, "leg_idx = 1 / leg_idx = n_legs", "chain", "derived", "review", "review", "Realised chain"),
    ("break_reason_before / break_reason_after", B, "Why a chain starts or ends: first_flight, long_rest, cancelled, diverted, airport_mismatch, swap_suspect", "chain", "derived", "outcome", "no", "Uses same-day cancellations and diversions"),
    ("category", B, "usable_segment, single_leg_segment, cancelled, diverted, missing_tail, other", "chain, Cancelled, Diverted", "derived", "outcome", "no", "Reconciliation label"),
    ("usable", B, "Operated and in a segment of 2+ legs", "chain", "derived", "outcome", "no", "Depends on same-day outcomes"),
    ("act_dep_utc / act_arr_utc", B, "Scheduled UTC + DepDelay / ArrDelay", "crs_*_utc, DepDelay, ArrDelay", "derived", "outcome", "no", "Actual times"),
    ("primary_delay_min", B, "max(ArrDelay, 0) - coalesce(LateAircraftDelay, 0)", "ArrDelay, LateAircraftDelay", "derived", "outcome", "no", "Outcome; the simulator input"),
    ("sched_ground_min", B, "Next scheduled departure minus scheduled arrival (UTC) for linked legs", "crs_dep_utc, crs_arr_utc, chain", "derived", "review", "review", "Known from the schedule, but defined through the realised tail sequence"),
    ("actual_ground_min", B, "Next actual departure minus actual arrival", "act_*_utc", "derived", "outcome", "no", "Actuals"),
    ("mtt_min", B, "10th percentile of actual ground time on time-pressured TRAIN turns by station and time-of-day band (fallbacks, floor 25, cap 60)", "turns, split", "assumption", "review", "review", "Fitted on train outcomes; an assumption, not an observation. Train data only"),
    ("mtt_source", B, "station_band, class_band or global fallback used", "mtt estimate", "derived", "none", "no", "Provenance label"),
    ("planned_buffer_min", B, "max(0, sched_ground_min - mtt_min)", "sched_ground_min, mtt_min", "assumption", "review", "review", "Inherits the review needs of both inputs"),
    ("flag_below_mtt", B, "sched_ground_min < mtt_min", "sched_ground_min, mtt_min", "assumption", "review", "review", "As above"),
    ("buffer_bucket", B, "planned buffer bucket: 0, 1-15, 16-30, 31-60, 60+", "planned_buffer_min", "assumption", "review", "review", "As above"),
    ("tod_band", B, "Local hour of outbound departure: morning 05-09, midday 10-14, afternoon 15-19, night 20-04", "crs_dep_local", "derived", "none", "yes", "Schedule only"),
    ("station_class", B, "high_volume if a station has at least 20 train turns per day, else other", "turns, split", "derived", "low", "review", "Estimated on train data; fine if refit on train only"),
    ("turn_index_in_segment / n_downstream_legs_after_turn", B, "Position of the turn and legs after it", "chain", "derived", "review", "review", "Realised chain"),
    ("flag_actual_ground_nonpositive", B, "actual_ground_min <= 0", "actual_ground_min", "derived", "outcome", "no", "Actuals"),
    ("storm_day_flag", B, "FlightDate in 2026-01-24 to 2026-01-28", "FlightDate", "derived", "outcome", "no", "Defined in hindsight from a known disruption; use to include or exclude days in evaluation"),
    ("high_cancel_day", B, "More than 10% of the day's flights cancelled", "Cancelled", "derived", "outcome", "no", "Defined from same-day outcomes"),
    # ---- Planned: candidate schedule-only features for the Phase 3 prediction model
    ("dep_hour_local", P, "Hour of crs_dep_local", "crs_dep_local", "derived", "none", "yes", "Schedule only; late-arrival rate rises from 10% at 05:00 to 37% at 19:00"),
    ("dep_minute_of_day", P, "crs_dep_min", "crs_dep_min", "derived", "none", "yes", "Schedule only"),
    ("arr_hour_local", P, "Hour of crs_arr_local", "crs_arr_local", "derived", "none", "yes", "Schedule only"),
    ("day_of_week", P, "DayOfWeek", "DayOfWeek", "real", "none", "yes", "Calendar"),
    ("month_of_year", P, "Month", "Month", "real", "none", "yes", "Calendar; limited seasonal coverage (see docs/d2)"),
    ("is_weekend", P, "DayOfWeek in (6, 7)", "DayOfWeek", "derived", "none", "yes", "Calendar"),
    ("is_us_federal_holiday / days_to_holiday", P, "US federal holiday calendar (public reference) and distance in days", "FlightDate", "derived", "none", "yes", "Calendar; known long in advance; reference data to be added"),
    ("origin / dest / route", P, "Airport codes and the origin-dest pair as categorical features", "Origin, Dest", "real", "none", "yes", "Schedule only; high cardinality, encode on train"),
    ("distance_mi / distance_bin", P, "Distance and binned distance", "Distance", "real", "none", "yes", "Schedule only"),
    ("sched_block_min", P, "CRSElapsedTime", "CRSElapsedTime", "real", "none", "yes", "Schedule only"),
    ("block_vs_distance_ratio", P, "CRSElapsedTime / Distance: schedule padding", "CRSElapsedTime, Distance", "derived", "none", "yes", "Schedule only"),
    ("tz_offset_diff", P, "Difference in UTC offsets between origin and destination on the day", "airports_tz, crs_*_utc", "derived", "none", "yes", "Schedule only"),
    ("flight_number", P, "Flight_Number_Reporting_Airline", "Flight_Number_Reporting_Airline", "real", "none", "yes", "Schedule only; high cardinality"),
    ("origin_dep_density", P, "Number of AA flights scheduled to depart the origin in the same local hour", "Origin, crs_dep_local", "derived", "none", "yes", "Congestion proxy from the published schedule"),
    ("dest_arr_density", P, "Number of AA flights scheduled to arrive at the destination in the same local hour", "Dest, crs_arr_local", "derived", "none", "yes", "Congestion proxy from the published schedule"),
    ("origin_is_high_volume", P, "Origin class (high_volume / other)", "station_class", "derived", "low", "yes", "Class fixed from train data"),
    ("hist_route_delay_rate", P, "ArrDel15 rate for the route (origin-dest) in the TRAINING window only, smoothed", "ArrDel15, Origin, Dest, split", "derived", "review", "review", "Historical delay rates must use the training window only; for training rows use out-of-fold estimates"),
    ("hist_flight_number_delay_rate", P, "ArrDel15 rate by flight number in the training window only", "ArrDel15, flight number, split", "derived", "review", "review", "Same rule: training window only"),
    ("hist_origin_hour_delay_rate", P, "ArrDel15 rate by origin and local departure hour in the training window only", "ArrDel15, Origin, crs_dep_min, split", "derived", "review", "review", "Same rule: training window only"),
    ("hist_origin_dow_delay_rate", P, "ArrDel15 rate by origin and day of week in the training window only", "ArrDel15, Origin, DayOfWeek, split", "derived", "review", "review", "Same rule: training window only"),
    ("planned_leg_position", P, "Position of the flight in the aircraft's planned day (needs the planned rotation)", "schedule, chain", "derived", "review", "review", "Leg position comes from the realised chain; use only if a planned rotation known before departure can be built"),
    ("planned_inbound_ground_min", P, "Scheduled ground time before the flight (planned turn)", "sched_ground_min", "derived", "review", "review", "Realised-chain definition; needs a leakage review"),
    ("planned_buffer_before_min", P, "Planned buffer before the flight", "planned_buffer_min", "assumption", "review", "review", "Inherits MTT assumption and chain review"),
]


def feature_frame() -> pd.DataFrame:
    return pd.DataFrame(FEATURES, columns=COLS)


def write(docs_dir: Path | None = None) -> pd.DataFrame:
    docs_dir = docs_dir or Path(__file__).resolve().parents[3] / "docs"
    df = feature_frame()
    df.to_csv(docs_dir / "feature_log.csv", index=False)
    n_b, n_p = int((df["status"] == "built").sum()), int((df["status"] == "planned").sum())
    allowed = df["allowed_as_prediction_feature"].value_counts().to_dict()
    lines = [
        "# Feature log", "",
        f"Generated by `bufferengine.pipeline.feature_log`. {len(df)} entries: **{n_b} built** (Phase 1 and Phase 2) and **{n_p} planned** "
        "(candidate schedule-only features for the Phase 3 prediction model). The same table is in `docs/feature_log.csv`.", "",
        "Columns: `status` built or planned; `kind` real, derived, assumption or model; `leakage_risk` none, low, review or outcome; "
        "`allowed_as_prediction_feature` yes, no or review.", "",
        f"Allowed as prediction feature: yes = {allowed.get('yes', 0)}, review = {allowed.get('review', 0)}, no = {allowed.get('no', 0)}.", "",
        "## Agreed leakage rules", "",
        "1. **Never** use outcome or actual fields (DepDelay, ArrDelay, TaxiOut, TaxiIn, WheelsOff, times of day of actual events, delay causes, `LateAircraftDelay`, `primary_delay_min`, actual ground time, cancellation or diversion flags) as inputs. The target is `ArrDel15`.",
        "2. **Leg position and downstream legs** (`leg_idx`, `n_legs`, `n_downstream_legs`, `turn_index_in_segment`, `segment_id`, `break_reason_*`) come from the *realised* chain, which depends on same-day cancellations, diversions and tail swaps. They need a leakage review and are not prediction features until it is done. They may be used by the optimiser under the stated assumption that the planned rotation equals the operated rotation.",
        "3. **Historical delay rates** (`hist_*`) must be computed from the **training window only** (Jan-Sep 2025). For training rows use out-of-fold estimates so a row never contributes to its own feature. Validate and test rows use the training-window values.",
        "4. **MTT and planned buffer** are assumptions estimated from train outcomes. Treat them as inputs to the simulator and optimiser; to use them in a prediction model, run the same review.",
        "5. **Partition labels** (`split`, `storm_day_flag`, `high_cancel_day`) select rows; they are never inputs.", "",
        "## Features", "", "| name | status | logic | source columns | kind | leakage risk | allowed | reason |", "|---|---|---|---|---|---|---|---|",
    ]
    for r in df.itertuples(index=False):
        lines.append(f"| {r.name} | {r.status} | {r.logic} | {r.source_columns} | {r.kind} | {r.leakage_risk} | {r.allowed_as_prediction_feature} | {r.reason} |")
    (docs_dir / "feature_log.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return df


if __name__ == "__main__":
    write()
