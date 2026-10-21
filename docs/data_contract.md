# Data contract

Contract version: **1.0** (Phase 1, 2026-10-21). Any change to a column name, type, unit or null rule
bumps the version; the version is written into the workbook `manifest` so the app can reject files it
does not understand.

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

## a) Rotation table

One row per leg in a **usable** aircraft chain (tail-day). File: `data/processed/rotations.parquet`
(built in Phase 2, notebook `02_rotation_building`).

**Usable chain:** non-null tail number; at least 2 operated legs on the local FlightDate; no diverted
legs; every consecutive pair continuous (previous `dest` = next `origin`) after cancelled legs are
removed. Legs are ordered by scheduled UTC departure. Cancelled legs are excluded from the chain and
counted in `n_cancelled_removed`.

**Primary key:** (`tail_number`, `flight_date`, `leg_idx`). **Also unique:** (`flight_date`,
`flight_number`, `origin`, `dest`, `crs_dep_time`) — the flight key shared with the prediction table.

| Column | Type | Unit | Null? | Kind | Definition |
|---|---|---|---|---|---|
| rotation_id | string | | No | derived | `{tail_number}_{flight_date}` |
| tail_number | string | | No | real | Normalised BTS Tail_Number |
| flight_date | date | | No | real | BTS FlightDate (local departure date of the leg) |
| leg_idx | int | | No | derived | 1..n_legs in UTC departure order |
| n_legs | int | | No | derived | Legs in the chain |
| n_cancelled_removed | int | | No | derived | Cancelled legs of this tail-day removed before chaining |
| flight_number | int | | No | real | Flight_Number_Reporting_Airline |
| origin | string | | No | real | IATA |
| dest | string | | No | real | IATA |
| origin_tz | string | | No | derived | IANA zone from `airports_tz.csv` |
| dest_tz | string | | No | derived | IANA zone from `airports_tz.csv` |
| crs_dep_time | int | hhmm | No | real | CRSDepTime |
| crs_arr_time | int | hhmm | No | real | CRSArrTime |
| crs_dep_local | timestamp_local | | No | derived | FlightDate + CRSDepTime at origin |
| crs_arr_local | timestamp_local | | No | derived | Inferred arrival date + CRSArrTime at dest |
| crs_dep_utc | timestamp_utc | | No | derived | crs_dep_local converted with origin_tz |
| crs_arr_utc | timestamp_utc | | No | derived | crs_arr_local converted with dest_tz |
| arr_day_offset | int | days | No | derived | 0 = arrives on FlightDate, 1 = next day |
| crs_block_min | float | min | No | real | CRSElapsedTime |
| distance_mi | float | mi | No | real | Distance |
| act_dep_utc | timestamp_utc | | No | derived | crs_dep_utc + dep_delay_min |
| act_arr_utc | timestamp_utc | | No | derived | crs_arr_utc + arr_delay_min |
| dep_delay_min | float | min | No | real | DepDelay (outcome: validation only, never a feature) |
| arr_delay_min | float | min | No | real | ArrDelay (outcome) |
| arr_del15 | int | 0/1 | No | real | ArrDel15 (outcome / target) |
| late_aircraft_delay_min | float | min | Yes: arr_delay_min < 15 | real | LateAircraftDelay |
| primary_delay_min | float | min | No | derived | max(arr_delay_min, 0) − coalesce(late_aircraft_delay_min, 0): delay not inherited from the previous leg |
| sched_ground_min | float | min | Yes: last leg | derived | Next leg crs_dep_utc − this leg crs_arr_utc |
| actual_ground_min | float | min | Yes: last leg | derived | Next leg act_dep_utc − this leg act_arr_utc |
| mtt_min | float | min | Yes: last leg | assumption | Estimated minimum turnaround at `dest` for the next departure (method fixed in Phase 2; value logged in manifest) |
| planned_buffer_min | float | min | Yes: last leg | derived from assumption | sched_ground_min − mtt_min (may be negative) |
| is_first_leg | bool | | No | derived | leg_idx = 1 |
| is_last_leg | bool | | No | derived | leg_idx = n_legs |
| flag_negative_ground | bool | | No | derived | sched_ground_min < 0 (as-flown sequence differs from the schedule: tail swap) |
| flag_below_mtt | bool | | No | derived | planned_buffer_min < 0 |
| flag_overnight_arrival | bool | | No | derived | arr_day_offset > 0 |
| split | string | | No | derived | `train` (Jan–Sep 2025), `validate` (Oct–Dec 2025), `test` (Jan–Aug 2026) |

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
| contract_version | string | meta | `1.0` |
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
