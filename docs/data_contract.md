# Data contract

Contract version: **1.1** (Phase 2, 2026-10-21; v1.0 was Phase 1). Any change to a column name, type, unit or null rule
bumps the version; the version is written into the workbook `manifest` so the app can reject files it
does not understand. Section (a) changed in v1.1 (see "Changes from v1.0" in that section); sections (b) and (c) are unchanged.

## Conventions

- **Provenance** (column `Kind` below):
  - **real** — copied from BTS without change (or only normalised, e.g. trimmed codes).
  - **derived** — computed deterministically from real data plus public reference data (e.g. UTC times
    from airport time zones).
  - **assumption** — an adjustable parameter or a value that depends on one (e.g. minimum turnaround,
    cost per minute). Every assumption is listed in the manifest with its value.
  - **synthetic** — generated, e.g. storm stress scenarios. Synthetic rows are always marked with
    `scenario != 'base'` and the app must label them as such.
  - **model** — output of a fitted model (prediction table).
- **Types:** `string`, `int`, `float` (64-bit), `bool`, `date` (YYYY-MM-DD), `timestamp_utc` (ISO 8601
  with `Z`), `timestamp_local` (ISO 8601 without offset, local clock of the named airport).
- **Units:** all durations are **minutes** (float unless stated). Distances are statute miles.
- **Nulls:** "No" means the column must never be null. "Yes" lists when it may be.
- **Names:** snake_case in every output table and sheet (the BTS base table keeps BTS names).

---

## a) Rotation tables (v1.1)

Built by `src/bufferengine/pipeline/build_rotations.py` (notebook `02_rotation_building`). Four parquet files in
`data/processed/` (git-ignored; large) plus small committed CSVs (`mtt_estimates.csv`,
`rotation_reconciliation.csv`, `rotation_rule_comparison.csv`).

**Chain rules (v1.1).**

1. Chains use **operated** flights (cancelled = 0 and diverted = 0). Cancelled and diverted flights stay in the
   aircraft's sequence at their scheduled position and **break** the chain; they are not removed and chained through.
2. Per aircraft, legs are ordered by scheduled UTC departure (ties: flight number, flight_id). Two consecutive
   legs are linked only if previous `dest` = next `origin` **and** the next scheduled departure is not before the
   previous scheduled arrival (UTC). A pair that fails the second test is a tail swap (`swap_suspect`): the chain
   breaks and the pair is written to `rotation_breaks`. There is no "kept with a flag" case.
3. A new **operating day** starts when the scheduled ground time to the next leg exceeds 300 minutes
   (`REST_GAP_MIN`). A leg that crosses midnight, and an early leg after a short turn behind it, stay in the same
   operating day. `operating_day` is the local FlightDate of the first flight of the run.
4. A **segment** is a maximal run of linked operated legs. **`usable` applies to a segment**: at least 2 consecutive
   legs. Single-leg segments are kept with `usable = false`. A tail-day (`rotation_id`) can hold several segments.
5. Every base flight is in exactly one `category` (priority in this order): `cancelled`, `diverted`, `missing_tail`,
   `other` (no valid UTC times), `single_leg_segment`, `usable_segment`.

### a1) `rotations.parquet`: one row per base flight (1,641,071 rows)

**Keys:** `flight_id` is unique. (`segment_id`, `leg_idx`) is unique among operated legs.

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| rotation_id | string | | Yes: missing tail | derived | `{tail_number}_{flight_date}` (tail-day key; a segment that crosses midnight spans two rotation_ids) |
| flight_id | string | | No | derived | `{YYYYMMDD}-{flight_number}-{origin}-{dest}-{crs_dep_time:04d}` |
| tail_number | string | | Yes: 11 cancelled flights | real | Normalised BTS Tail_Number |
| flight_date | date | | No | real | BTS FlightDate (local departure date) |
| operating_day | date | | Yes: not operated | derived | See rule 3 |
| segment_id | string | | Yes: not operated | derived | `{tail_number}_{flight_id of the segment's first leg}` |
| leg_idx | int | | Yes: not operated | derived | Position in the segment, 1-based. **Not a prediction feature** |
| n_legs | int | | Yes: not operated | derived | Legs in the segment. **Not a prediction feature** |
| n_segments | int | | Yes: not operated | derived | Segments (including single-leg) of this rotation_id |
| n_downstream_legs | int | | Yes: not operated | derived | `n_legs - leg_idx`, legs after this one in the realised chain. **Not a prediction feature** |
| n_cancelled_in_tail_day | int | | Yes: missing tail | derived | Informational: cancelled flights of this tail on this flight_date (was `n_cancelled_removed` in v1.0) |
| flight_number | int | | No | real | Flight_Number_Reporting_Airline |
| origin, dest | string | | No | real | IATA |
| origin_tz, dest_tz | string | | No | derived | IANA zone from `airports_tz.csv` |
| crs_dep_time, crs_arr_time | int | hhmm | No | real | Local scheduled times |
| crs_dep_local, crs_arr_local | timestamp_local | | No | derived | FlightDate (plus inferred arrival day) + time, at the airport's local clock |
| crs_dep_utc, crs_arr_utc | timestamp_utc | | No | derived | Local times converted with the airport zones |
| arr_day_offset | int | days | No | derived | 0 = arrives on FlightDate, 1 = next day |
| crs_block_min | float | min | No | real | CRSElapsedTime |
| distance_mi | float | mi | No | real | Distance |
| act_dep_utc, act_arr_utc | timestamp_utc | | Yes: not operated | derived | crs UTC + dep_delay_min / arr_delay_min |
| dep_delay_min, arr_delay_min | float | min | Yes: cancelled, diverted (arr) | real | BTS DepDelay, ArrDelay (outcomes) |
| arr_del15 | int | 0/1 | Yes: cancelled, diverted | real | BTS ArrDel15 (target) |
| late_aircraft_delay_min | float | min | Yes: arr_delay_min < 15 | real | BTS LateAircraftDelay |
| primary_delay_min | float | min | Yes: not operated | derived | `max(arr_delay_min, 0) - coalesce(late_aircraft_delay_min, 0)`: delay not inherited from the previous leg |
| cancelled, diverted | int | 0/1 | No | real | BTS flags |
| category | string | | No | derived | `usable_segment`, `single_leg_segment`, `cancelled`, `diverted`, `missing_tail`, `other` |
| usable | bool | | No | derived | Operated and in a segment of 2+ legs |
| split | string | | No | derived | `train` (to 2025-09-30), `validate` (2025-10 to 2025-12), `test` (2026-01 to 2026-08), by flight_date |
| storm_day_flag | bool | | No | derived | flight_date in 2026-01-24 to 2026-01-28 |
| high_cancel_day | bool | | No | derived | More than 10% of that day's flights cancelled |
| break_reason_before | string | | Yes: not operated | derived | `first_flight`, `long_rest`, `cancelled`, `diverted`, `airport_mismatch`, `swap_suspect`, or `linked` |
| break_reason_after | string | | Yes: not operated | derived | Same values, plus `last_flight` |
| sched_ground_min | float | min | Yes: last leg of a segment, not usable | derived | Scheduled ground time to the **next** leg of the segment (next crs_dep_utc minus crs_arr_utc) |
| actual_ground_min | float | min | Same | derived | Same using actual times |
| mtt_min | float | min | Same | assumption | Estimated minimum turnaround for that turn (a2) |
| planned_buffer_min | float | min | Same | derived from assumption | `max(0, sched_ground_min - mtt_min)` |
| flag_below_mtt | bool | | Same | derived | `sched_ground_min < mtt_min` |
| is_first_leg, is_last_leg | bool | | Yes: not operated | derived | Position in the segment |
| flag_overnight_arrival | bool | | No (null-safe) | derived | `arr_day_offset > 0` |

### a2) `rotation_turns.parquet`: one row per consecutive leg pair inside a usable segment (1,045,807 rows)

**Key:** `turn_id` = `{inbound_flight_id}>{outbound_flight_id}` (unique). The turn belongs to the outbound flight's
date for `split` and the flags.

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| turn_id | string | | No | derived | See above |
| tail_number, operating_day, segment_id | | | No | derived | As in a1 |
| station | string | | No | real | Airport of the turn (inbound `dest`) |
| station_tz | string | | No | derived | IANA zone of the station |
| inbound_flight_id, outbound_flight_id | string | | No | derived | |
| turn_index_in_segment | int | | No | derived | 1 = turn between legs 1 and 2. **Not a prediction feature** |
| n_downstream_legs_after_turn | int | | No | derived | Outbound and later legs: `n_legs - turn_index_in_segment`. **Not a prediction feature** |
| sched_ground_min | float | min | No | derived | Always >= 0 and <= 300 by construction |
| actual_ground_min | float | min | No | derived | May be <= 0 for 235 turns (see flag) |
| inbound_dep_delay_min, inbound_arr_delay_min | float | min | No | real | |
| outbound_dep_delay_min, outbound_arr_delay_min | float | min | No | real | |
| late_aircraft_delay_outbound_min | float | min | Yes: outbound arr_delay < 15 | real | BTS LateAircraftDelay of the outbound flight |
| inbound_sched_arr_local, outbound_sched_dep_local | timestamp_local | | No | derived | Station local clock |
| inbound_flight_date, outbound_flight_date | date | | No | real | |
| split | string | | No | derived | By `outbound_flight_date` |
| storm_day_flag, high_cancel_day | bool | | No | derived | By `outbound_flight_date` |
| tod_band | string | | No | derived | Local hour of the outbound scheduled departure: `morning` 05-09, `midday` 10-14, `afternoon` 15-19, `night` 20-04 |
| flag_actual_ground_nonpositive | bool | | No | derived | `actual_ground_min <= 0`: the aircraft cannot have flown both; excluded from MTT estimation and the premise check |
| station_class | string | | No | derived | `high_volume` (>= 20 train turns/day at the station; 13 stations) or `other` |
| mtt_source | string | | No | derived | `station_band`, `class_band` or `global` (fallback used) |
| mtt_min | float | min | No | **assumption** | Minimum turnaround estimate (see below) |
| flag_below_mtt | bool | | No | derived | `sched_ground_min < mtt_min` |
| planned_buffer_min | float | min | No | derived from assumption | `max(0, sched_ground_min - mtt_min)` |
| buffer_bucket | string | | No | derived | `0`, `1-15`, `16-30`, `31-60`, `60+` (minutes of planned buffer) |

**MTT estimate (assumption).** Per station and `tod_band`, the `mtt_percentile` (default **0.10**) of
`actual_ground_min` over **train** turns where `inbound_arr_delay_min > 0` and `outbound_dep_delay_min > 0` and
`actual_ground_min > 0`. A cell needs at least 50 such turns; otherwise the `station_class` x band cell (same
rule), then one global value. Result clipped to a **floor of 25 min** and **cap of 60 min**. No validate or test turn
is read. Estimates are written to `data/processed/mtt_estimates.csv` and logged in the export manifest.

### a3) `rotation_breaks.parquet`: consecutive aircraft flights that were not linked for a flight-level reason (93,458 rows)

Excludes the benign boundaries `first_flight` and `long_rest`. One row per rejected pair.

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| next_flight_id, prev_flight_id | string | | No | derived | The pair (next is the later flight) |
| tail_number | string | | No | real | |
| reason | string | | No | derived | `cancelled`, `diverted`, `airport_mismatch`, `swap_suspect` (precedence in this order after `long_rest`) |
| non_operated_side | string | | No | derived | For cancelled / diverted: `prev`, `next` or `both`; otherwise `neither` |
| prev_dest, next_origin, next_dest | string | | No | real | |
| gap_min | float | min | No | derived | Scheduled next departure minus previous arrival (negative for swaps) |
| airport_mismatch, negative_gap | bool | | No | derived | Both conditions, recorded independently |
| flight_date | date | | No | real | Date of the next flight |
| split, storm_day_flag, high_cancel_day | | | No | derived | By `flight_date` |

### a4) `mtt_sensitivity.parquet`: planned buffer under other MTT percentiles (1,045,807 rows)

`turn_id`, `split`, `sched_ground_min`, then for each percentile p05, p10, p20: `mtt_pXX_min` (assumption) and
`planned_buffer_pXX_min` (derived from assumption). The main table uses p10.

### Changes from v1.0

1. Column names are the v1.0 snake_case names (`tail_number`, `leg_idx`, `crs_dep_utc`, `act_dep_utc`, `dep_delay_min`,
   `mtt_min`, ...). Phase 2 prompt names were mapped: `tail`→`tail_number`, `leg_index_in_segment`→`leg_idx`,
   `n_legs_in_segment`→`n_legs`, `sched_dep_utc`→`crs_dep_utc`, `actual_dep_utc`→`act_dep_utc`, `dep_delay`→`dep_delay_min`,
   `mtt_est_min`→`mtt_min`. Turn delays carry the same `_min` suffix.
2. Files are in `data/processed/` and `rotations.parquet` is the legs table.
3. `split` values stay `train` / `validate` / `test`.
4. Cancelled and diverted legs **break** chains (v1.0 removed cancelled legs and chained through). Tail swaps break chains and are
   counted in `rotation_breaks` (v1.0 kept them with `flag_negative_ground`, now removed). `usable` applies to a **segment**
   of 2+ legs, not the tail-day.
5. Added `segment_id`, `n_segments`, `n_downstream_legs`, `storm_day_flag`, `high_cancel_day`, `operating_day`, `category`, `usable`,
   `break_reason_before/after`. `rotation_id` kept as `{tail_number}_{flight_date}`. `n_cancelled_removed` renamed
   `n_cancelled_in_tail_day` (informational).
6. The table now has **one row per base flight** (not only usable legs) so that every flight is accounted for; usable legs are `usable = true`.
7. `planned_buffer_min = max(0, sched_ground_min - mtt_min)` (v1.0 allowed negative values); the shortfall is `flag_below_mtt`.
8. Ground-time columns are on the leg row for the turn that follows it.
9. New tables: `rotation_turns`, `rotation_breaks`, `mtt_sensitivity`.

---

## b) Prediction table

One row per **scheduled** AA flight in the base table that has a valid scheduled time and known time
zones, including cancelled flights (they are scored but excluded from metrics). File:
`data/processed/predictions.parquet` (Phase 3, notebook `04_prediction_model`). Only schedule-known
inputs are used (see `docs/data_dictionary.md`, "Feature?" column).

**Primary key:** (`flight_date`, `flight_number`, `origin`, `dest`, `crs_dep_time`, `model_name`).

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| flight_date | date | | No | real | FlightDate |
| flight_number | int | | No | real | Flight_Number_Reporting_Airline |
| origin | string | | No | real | IATA |
| dest | string | | No | real | IATA |
| crs_dep_time | int | hhmm | No | real | CRSDepTime |
| tail_number | string | | Yes: missing in BTS | real | For joining to rotations only, not a feature |
| crs_dep_utc | timestamp_utc | | No | derived | As in the rotation table |
| model_name | string | | No | model | `logreg_baseline` or `gbm` |
| model_version | string | | No | model | `{model_name}-{yyyymmdd}-{git short sha}` |
| p_delay15 | float | 0–1 | No | model | Calibrated P(arrival delay >= 15 min) |
| expected_delay_min | float | min | No | model | E[max(ArrDelay, 0)] |
| expected_primary_delay_min | float | min | No | model | E[primary delay]: the input the simulator uses, so propagated delay is not double counted |
| q50_delay_min | float | min | No | model | Median of max(ArrDelay, 0) |
| q80_delay_min | float | min | No | model | 80th percentile |
| q95_delay_min | float | min | No | model | 95th percentile |
| hist_delay_rate | float | 0–1 | Yes: no training history | derived | Historical ArrDel15 rate for the same flight number + origin in the training window (baseline) |
| split | string | | No | derived | `train` / `validate` / `test` |
| arr_del15 | int | 0/1 | Yes: cancelled or diverted | real | Observed label, for evaluation only |
| scored_at_utc | timestamp_utc | | No | derived | When the model scored the row |

Rules: q50 <= q80 <= q95; p_delay15 is in [0, 1]; predictions on `train` rows are in-sample and are
never used for reported metrics.

---

## c) Export workbook (`.xlsx`)

File: `app/public/data/results.xlsx` (default; Phase 7, notebook `08_export`). One header row per sheet,
data from row 2, no merged cells, no formulas. The app validates sheet names, column names and types
against this section and rejects the file with a clear message if they do not match.

### manifest

Key–value sheet. Columns: `key` (string), `value` (string, parsed by the app per key), `kind` (real,
derived, assumption, synthetic, model, meta), `description` (string).

| key | Type of value | Kind | Description |
|---|---|---|---|
| contract_version | string | meta | `1.1` |
| run_id | string | meta | Unique ID of the export run |
| run_date_utc | timestamp_utc | meta | When the workbook was written |
| git_commit | string | meta | Repo commit of the code that produced it |
| notebooks | string | meta | Comma-separated notebook names that produced the results |
| data_source | string | meta | `BTS Reporting Carrier On-Time Performance, AA mainline` |
| data_version | string | meta | Base table file name, row count and SHA-256 prefix |
| data_period_train | string | meta | `2025-01-01..2025-09-30` |
| data_period_validate | string | meta | `2025-10-01..2025-12-31` |
| data_period_test | string | meta | `2026-01-01..2026-08-31` |
| rows_base_table | int | real | Rows in the base table |
| rows_rotations | int | derived | Rows (legs) in the rotation table |
| rows_turn_plans / rows_rotations_sheet / rows_risk_heatmap | int | meta | Row counts of those sheets |
| model_version | string | model | Model used for the results |
| solver | string | meta | e.g. `HiGHS 1.x` |
| budget_min_per_day | float | assumption | Default daily buffer budget (e.g. 500) |
| budget_grid | string | assumption | Comma-separated budgets in `budget_curve` |
| per_turn_max_min | float | assumption | Maximum added buffer per turn |
| mtt_method | string | assumption | How minimum turnaround was estimated |
| cost_per_buffer_min_usd | float | assumption | Cost of one planned buffer minute (adjustable) |
| cost_per_delay_min_usd | float | assumption | Cost of one propagated delay minute (adjustable) |
| n_sim_runs | int | assumption | Monte Carlo replications per day |
| random_seed | int | meta | Seed |
| headline_reduction_pct | float | derived | Propagated-delay reduction of the exact optimiser vs no buffer on the 2026 backtest |
| headline_vs_even_pct | float | derived | Same vs even spread |
| synthetic_scenarios | string | synthetic | Names of stress scenarios included (empty if none) |

### kpis

| Column | Type | Null? | Kind | Definition |
|---|---|---|---|---|
| kpi_id | string | No | meta | Stable ID, e.g. `prop_delay_reduction_pct` |
| label | string | No | meta | Display name |
| value | float | No | derived / model | Value |
| unit | string | No | meta | `min`, `pct`, `ratio`, `usd` |
| strategy | string | Yes: model KPIs | meta | Strategy the KPI refers to |
| scope | string | No | meta | `validate`, `test_backtest`, `stress` |
| scenario | string | No | meta / synthetic | `base` or a stress scenario name |
| source_notebook | string | No | meta | Notebook that computed it (used by the reconciliation panel) |
| kind | string | No | meta | Provenance of the value |

### strategy_comparison

One row per (strategy, budget_min, scenario).

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| strategy | string | | No | meta | `no_buffer`, `even_spread`, `historical_rate`, `greedy`, `exact` |
| scenario | string | | No | meta / synthetic | `base` or stress scenario |
| budget_min | float | min/day | No | assumption | Daily buffer budget |
| buffer_used_min | float | min/day | No | derived | Mean buffer allocated per day |
| propagated_delay_min_mean | float | min/day | No | derived | Mean total propagated delay per day across simulations |
| propagated_delay_min_p95 | float | min/day | No | derived | 95th percentile across simulations |
| delayed_legs_15 | float | legs/day | No | derived | Mean legs with arrival delay >= 15 |
| otp_pct | float | % | No | derived | Simulated on-time arrival rate |
| reduction_vs_no_buffer_pct | float | % | No | derived | Relative reduction vs `no_buffer` |
| total_cost_usd | float | USD/day | No | assumption | buffer cost + delay cost, using manifest costs |
| runtime_s | float | s | Yes: baselines | meta | Solver run time |
| n_days | int | | No | meta | Days evaluated |

### budget_curve

One row per (strategy, budget_min, scenario). Columns: `strategy` (string), `scenario` (string),
`budget_min` (float, assumption), `propagated_delay_min_mean` (float, derived), `propagated_delay_min_p05`
(float, derived), `propagated_delay_min_p95` (float, derived), `marginal_reduction_per_min` (float,
derived; reduction relative to the previous budget step divided by the extra minutes; null for the
first step).

### turn_plans

One row per (date, tail, turn, strategy): the buffer each strategy adds to each turn.
Key: (`flight_date`, `tail_number`, `leg_idx`, `strategy`, `budget_min`, `scenario`).

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| flight_date | date | | No | real | |
| tail_number | string | | No | real | |
| leg_idx | int | | No | derived | Index of the **inbound** leg; the turn is between leg_idx and leg_idx + 1 |
| station | string | | No | real | Airport where the turn happens (inbound dest) |
| inbound_flight | int | | No | real | Flight number arriving |
| outbound_flight | int | | No | real | Flight number departing |
| sched_arr_local | timestamp_local | | No | derived | Inbound scheduled arrival, station local time |
| sched_dep_local | timestamp_local | | No | derived | Outbound scheduled departure, station local time |
| sched_ground_min | float | min | No | derived | |
| mtt_min | float | min | No | assumption | |
| planned_buffer_min | float | min | No | derived from assumption | |
| p_delay15_inbound | float | 0–1 | No | model | |
| expected_delay_inbound_min | float | min | No | model | |
| downstream_legs | int | | No | derived | Legs after this turn in the chain |
| strategy | string | | No | meta | |
| budget_min | float | min/day | No | assumption | |
| scenario | string | | No | meta / synthetic | |
| added_buffer_min | float | min | No | derived | Allocation for this turn (0 ≤ value ≤ per_turn_max_min) |
| expected_propagated_saved_min | float | min | No | derived | Simulated propagated-delay reduction attributed to this turn |

### rotations

One row per leg, for the days shown in the app (a sample of backtest days to keep the file small).
Key: (`flight_date`, `tail_number`, `leg_idx`, `scenario`).

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| flight_date | date | | No | real | |
| tail_number | string | | No | real | |
| leg_idx | int | | No | derived | |
| n_legs | int | | No | derived | |
| flight_number | int | | No | real | |
| origin | string | | No | real | |
| dest | string | | No | real | |
| crs_dep_local | timestamp_local | | No | derived | |
| crs_arr_local | timestamp_local | | No | derived | |
| crs_dep_utc | timestamp_utc | | No | derived | |
| p_delay15 | float | 0–1 | No | model | |
| expected_delay_min | float | min | No | model | |
| actual_arr_delay_min | float | min | Yes: synthetic scenario | real | Observed BTS ArrDelay |
| sim_arr_delay_no_buffer_min | float | min | No | derived | Mean simulated arrival delay, no added buffer |
| sim_arr_delay_exact_min | float | min | No | derived | Mean simulated arrival delay with the exact plan |
| added_buffer_exact_min | float | min | Yes: last leg | derived | Buffer added after this leg by the exact plan |
| scenario | string | | No | meta / synthetic | |

### risk_heatmap

One row per (station, local departure hour, scenario).

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| station | string | | No | real | Origin airport |
| dep_hour_local | int | 0–23 | No | derived | Scheduled local departure hour |
| scenario | string | | No | meta / synthetic | |
| flights | int | | No | real | Scheduled flights in the backtest window |
| mean_p_delay15 | float | 0–1 | No | model | |
| mean_expected_delay_min | float | min | No | model | |
| observed_del15_rate | float | 0–1 | Yes: synthetic scenario | real | BTS ArrDel15 rate |
| mean_propagated_delay_min | float | min | No | derived | Simulated, no added buffer |
| added_buffer_exact_min | float | min | No | derived | Total buffer the exact plan adds at turns departing in this cell |
