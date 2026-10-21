# Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-21 | Scope: AA mainline only (`Reporting_Airline = AA`) from BTS Reporting Carrier On-Time Performance | Continues D1; one carrier keeps rotations coherent |
| 2026-10-21 | Data periods: 2025 for training/validation, Jan–Aug 2026 for test | Out-of-time backtest |
| 2026-10-21 | Split: train Jan–Sep 2025, validate Oct–Dec 2025, test Jan–Aug 2026 | Time-based split avoids leakage |
| 2026-10-21 | Airport time zones derived from OurAirports lat/lon with `timezonefinder`; OpenFlights `airports.dat` as fallback (superseded the earlier "from OurAirports" wording) | OurAirports has no IANA zone column; BTS times are local and ground time needs UTC |
| 2026-10-21 | Real flights stay real; derived fields (planned ground time, MTT, planned buffer) are computed; costs per buffer/delay minute are labelled adjustable assumptions; synthetic data only for stress scenarios | Keep real data real and label assumptions |
| 2026-10-21 | Models: gradient boosting (XGBoost or LightGBM) plus logistic regression baseline, schedule-only features | Comparable to D1; no outcome leakage |
| 2026-10-21 | Never use outcome fields (`DepDelay`, `ArrDelay`, `TaxiOut`, etc.) as features | Leakage |
| 2026-10-21 | Metrics: precision, recall, PR-AUC, calibration, top-10% lift vs historical delay-rate baseline | Imbalanced target; ops relevance |
| 2026-10-21 | Propagation via Monte Carlo over rotations, validated against `LateAircraftDelay` | Ground truth for knock-on delay |
| 2026-10-21 | Optimiser: exact solver (HiGHS or OR-Tools) and greedy, vs no-buffer, even-spread, historical-rate baselines; total budget + per-turn max constraints | Open-source solvers; transparent baselines |
| 2026-10-21 | Heavy compute in Databricks Free Edition; raw data in Volume `bufferengine`, not in git | Free tier; repo stays small |
| 2026-10-21 | Results exchanged via an `.xlsx` workbook (manifest, kpis, strategy_comparison, budget_curve, turn_plans, rotations, risk_heatmap) | Simple contract between pipeline and app |
| 2026-10-21 | App: React + TypeScript + Vite + Tailwind on Netlify free tier, static only; `/console` and `/journey`; upload workbook in browser with contract validation and reset-to-default | No backend; app never optimises or connects to Databricks |
| 2026-10-21 | Free tools only; no files over 25 MB in git | Course constraint |
| 2026-10-21 | Local Python venv built with Python 3.12 (3.14 also installed) | Wheel availability for xgboost, lightgbm, shap, ortools, highspy; closer to Databricks runtime |

## Phase 1 — data acquisition, audit, data contract

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-21 | Download BTS monthly PREZIP files (`transtats.bts.gov/PREZIP/..._{YEAR}_{MONTH}.zip`), Jan 2025 – Aug 2026; URL pattern verified (Sep 2026 returns 404) | Reproducible, scriptable, no manual form clicks |
| 2026-10-21 | All paths resolve under `DATA_ROOT` (default: repo `data/`; Databricks: `/Volumes/workspace/default/bufferengine`) | Same code locally and in Databricks |
| 2026-10-21 | Base table built with DuckDB: read every column as text, filter `Reporting_Airline = 'AA'`, cast with `TRY_CAST` and log cast failures per column | Explicit dtypes; silent coercions become visible |
| 2026-10-21 | No rows dropped in the base table except non-AA carriers; cancelled, diverted and missing-tail rows kept with flags | Filtering decisions belong to later phases and stay auditable |
| 2026-10-21 | `Tail_Number` normalised: trim, upper-case, empty → null | Consistent rotation key |
| 2026-10-21 | hhmm `2400` kept as 1440 minutes (end of day), not 0 | Preserves ordering within the local day |
| 2026-10-21 | Scheduled arrival date inferred as the first local date that places arrival after departure in UTC; DST gaps shift forward, ambiguous fall-back times take daylight time | BTS gives no arrival date; validated against `CRSElapsedTime` |
| 2026-10-21 | Actual UTC times = scheduled UTC + DepDelay / ArrDelay (not from DepTime/ArrTime) | Avoids guessing the actual date for flights delayed past midnight |
| 2026-10-21 | Legs within a tail-day ordered by scheduled UTC departure, not local time | Local clock order differs from true order for some tail-days across zones |
| 2026-10-21 | PBI resolved through the OpenFlights fallback: OurAirports now lists Palm Beach as `DJT`, BTS still uses `PBI` | Shows why the fallback is needed; kept code-based, no manual edits |
| 2026-10-21 | DuckDB sessions in the audit use `TimeZone = 'UTC'` | Output identical on any machine (the dev machine is UTC+05:30) |
| 2026-10-21 | Simulator input will be **primary delay** (arrival delay minus LateAircraftDelay); the prediction contract carries `expected_primary_delay_min` | Avoids double counting propagated delay when the simulator re-propagates it |
| 2026-10-21 | Data contract v1.0 written; provenance kinds: real, derived, assumption, synthetic, model | Every number in the app traceable to its source |
| 2026-10-21 | Databricks location is catalog `workspace`, schema `default`, Volume `bufferengine` (`/Volumes/workspace/default/bufferengine/`); the Volume mirrors `data/` (`interim/`, `reference/`, `processed/`) so `DATA_ROOT` is the Volume root. Corrects the earlier path guess | Matches the user's actual workspace |


## Phase 2: rotation building, MTT and planned buffer

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-21 | **Contract bumped to v1.1.** Keep the v1.0 snake_case column names (`tail_number`, `leg_idx`, `crs_dep_utc`, `act_dep_utc`, `dep_delay_min`, `mtt_min`, ...); the Phase 2 prompt names are mapped to them (`tail`, `leg_index_in_segment`, `n_legs_in_segment`, `sched_dep_utc`, `actual_dep_utc`, `dep_delay`, `mtt_est_min`) | One naming scheme across docs, tables and the export workbook |
| 2026-10-21 | Outputs go to `data/processed/` (not interim): `rotations.parquet` (legs), `rotation_turns.parquet`, `rotation_breaks.parquet`, `mtt_sensitivity.parquet`; uploaded to `dbfs:/Volumes/workspace/default/bufferengine/processed/`. Parquet files there are git-ignored (86 MB, 50 MB, 19 MB); small CSVs are committed. The `.gitignore` safety net now ignores every `*.parquet` | The 25 MB file rule |
| 2026-10-21 | `split` values stay `train` / `validate` / `test` (train to 2025-09-30, validate 2025-10 to 2025-12, test 2026-01 to 2026-08) | Matches contract v1.0 |
| 2026-10-21 | Chains use **operated flights only**; cancelled and diverted flights stay in the aircraft sequence at their scheduled position and **break** the chain (v1.0 removed cancelled legs and chained through) | A cancelled flight means the aircraft did not fly that leg; chaining through would invent a turn |
| 2026-10-21 | Link rule: previous `dest` = next `origin` and next scheduled departure (UTC) >= previous scheduled arrival (UTC). Failing the time test is a `swap_suspect`: the chain breaks and the pair is counted in `rotation_breaks`, not kept with a flag (v1.0 `flag_negative_ground` removed). Result: 33,461 swap-suspect pairs | BTS reports the aircraft that flew; a negative scheduled ground time means the plan and the operation differ |
| 2026-10-21 | **`usable` applies to a segment** of 2+ consecutive operated legs, not to the tail-day. Single-leg segments are kept with `usable = false`. Old v1.0 rule: 405,613 usable tail-days; new rule: 384,708 tail-days with a usable segment (306,913 that are one clean segment) | The unit the optimiser needs is a chain, and one disrupted leg should not discard a whole day |
| 2026-10-21 | **Operating day rule:** a new operating day starts when the scheduled ground time to the next leg exceeds **300 minutes**; `operating_day` is the local FlightDate of the first flight of the run. A leg crossing midnight stays with its tail sequence. Tested with a LAX-JFK red-eye, a short JFK turn and a long rest | The data has a valley at 240-300 min between daytime turns and overnight rests (Phase 1 gap distribution); delay does not realistically carry over a 5-hour rest. A local-clock cut would break across time zones |
| 2026-10-21 | `segment_id` = `{tail_number}_{first leg flight_id}` | An earlier version built from the first departure minute collided 104 times: an aircraft can be scheduled on two flights at the same UTC minute in different cities. Found by a real-data test; regression test added |
| 2026-10-21 | `rotations.parquet` has one row per base flight with a `category` (usable_segment, single_leg_segment, cancelled, diverted, missing_tail, other) so that every flight is accounted for; the counts must equal 1,641,071 and a pytest enforces it. The 11 flights with no tail are all cancelled | "Never silently drop rows" |
| 2026-10-21 | `rotation_id` kept as `{tail_number}_{flight_date}` (legacy tail-day key); added `segment_id`, `n_segments`, `n_downstream_legs`, `storm_day_flag`, `high_cancel_day`; `n_cancelled_removed` renamed `n_cancelled_in_tail_day` (informational) | Contract reconciliation instruction |
| 2026-10-21 | `planned_buffer_min = max(0, sched_ground_min - mtt_min)`; the shortfall below MTT is `flag_below_mtt` (v1.0 allowed negative buffer) | Phase 2 prompt definition; negative buffer is not actionable |
| 2026-10-21 | Turn `split` and flags follow the **outbound** flight date; leg `split` follows the leg's own FlightDate | A turn is evaluated when the outbound departs; MTT uses only turns whose outbound is in the train window |
| 2026-10-21 | `storm_day_flag` = 2026-01-24 to 2026-01-28. `high_cancel_day` = more than 10% of that day's flights cancelled (27 days, 30,866 turns) | A real storm is in the test window; backtests are reported with and without disrupted days |
| 2026-10-21 | **MTT is an assumption** estimated from TRAIN turns only: per station and time-of-day band (morning 05-09, midday 10-14, afternoon 15-19, night 20-04, local time of the outbound departure), the **10th percentile** of actual ground time on turns where the inbound arrived late, the outbound departed late and ground time > 0. A cell needs at least 50 turns; fallback is station class x band, then global | Under time pressure the aircraft is turned as fast as it can be; a low percentile approximates the minimum |
| 2026-10-21 | MTT **floor 25 min, cap 60 min**. The floor never binds (lowest raw estimate 27; first percentile of pressured turns about 32). The cap binds for 3,736 turns (0.36%): HNL (raw 71-87) and ANC night (60) | Floor: no realistic turn is shorter. Cap: stops noisy or long-haul cells from absorbing all buffer; its effect is reported |
| 2026-10-21 | Default percentile **p10**; p05 and p20 sensitivity in `mtt_sensitivity.parquet` (zero-buffer share 5.7% / 7.7% / 13.9%) | p10 is mid-range and robust to a few unusually fast turns |
| 2026-10-21 | Station class is data-driven: `high_volume` = at least 20 train turns per day at the station (13 stations: DFW, CLT, PHX, ORD, PHL, MIA, LAX, DCA, MCO, LGA, LAS, BOS, TPA; JFK at 19.6 just misses). Named `high_volume` / `other`, not "hub" | Verifiable from the data; an official hub list would not match the volume split |
| 2026-10-21 | Turns with `actual_ground_min <= 0` (235) are kept and flagged, and excluded from MTT estimation and the premise check | The aircraft cannot have flown both legs as recorded |
| 2026-10-21 | The premise check uses cluster-robust (by segment) confidence intervals for slopes, plus an absorption table. Result: slope about 1.1 up to 30 min of buffer, 0.95 at 31-60, 0.44 at 60+; at fixed inbound delay the outbound delay falls steeply with buffer | A straight-line slope understates absorption because absorption is piecewise |
| 2026-10-21 | Chain fields (`leg_idx`, `n_legs`, `n_downstream_legs`, `segment_id`, `break_reason_*`, turn index) are **not prediction features** until a leakage review; they are allowed for the optimiser under the assumption that the planned rotation equals the operated rotation (documented limitation) | They depend on same-day cancellations and swaps |


## D2: cleaned dataset v1.0 and audit evidence

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-21 | The clean step keeps **all 1,641,071 rows** and fails if the count differs; original BTS columns are never modified. Structural nulls (cancelled and diverted flights, delay causes on non-late arrivals) are **not imputed**; they are flagged (`struct_null_*`) | A null here means "did not happen", not "missing"; imputing would invent delays |
| 2026-10-21 | Data-quality flags instead of deletions: `dq_missing_tail`, `dq_extreme_delay_24h`, `dq_time_conflict`, `dq_duplicate_key`, `dq_outlier_other`. Each rule and count is in `data/processed/dq_treatment_log.csv` | Filtering decisions belong to the analysis and stay auditable |
| 2026-10-21 | `arr_delay_min_capped` = ArrDelay clipped to the **train-period** 0.5% and 99.5% quantiles of operated flights ([-42, 454] min); `ArrDelay` is unchanged | A cap chosen on validate or test data would leak; a separate column keeps the original usable |
| 2026-10-21 | Cleaned file is zstd level 19 with rows in a fixed order; repeated runs give byte-identical output. `CHECKSUMS.sha256` file hashes are authoritative; the manifest adds a logical `content_sha256` for parquet tables as a fallback | Reproducibility |
| 2026-10-21 | The cleaned parquet is 68.4 MB (over the 50 MB limit), so it stays git-ignored, a 5,000-row sample (250 per month, seed 42) is committed, and the file is distributed as a GitHub Release asset under `data-v1.0` | Size rule from the D2 brief |
| 2026-10-21 | `data/processed/*.parquet` is ignored by the `*.parquet` rule; small CSVs, `CHECKSUMS.sha256` and `data_manifest.json` are committed | Keep the repo small and still verifiable |
| 2026-10-21 | Representativeness evidence is generated by script; the summary text (`docs/d2/representativeness_summary.md`) is built from the tables so each number traces to a file. The AA share of all reporting carriers is computed from the raw monthly files (all carriers) | "Every number must trace to a file" |
| 2026-10-21 | The domestic-service check uses OurAirports country codes matched on IATA code. Result: no foreign airports, only US territories (PR, VI); PBI is unmatched because OurAirports renamed it | Verified, not assumed; the D1 charter describes AA as domestic and international, and BTS data does not contain the international flying |
| 2026-10-21 | Source log records licence wording **verbatim** from official pages and repositories. BTS: "not found on page" (no licence statement on pages we could open; bts.gov blocked automated access). OurAirports: Unlicense file in the repository; documentation page returned HTTP 500. OpenFlights: ODbL wording quoted. timezonefinder: MIT, data ODbL | Do not invent licences |
| 2026-10-21 | Project code is MIT-licensed; `DATA_NOTICE.md` is generated from the source log so the wording matches; academic use; no ownership claimed over source data | Public-repo preparation |
| 2026-10-21 | Public-repo safety: a scan script (`bufferengine.pipeline.safety_scan`) covers the working tree and git history. Local Windows user paths in notebook outputs were replaced by `<repo>`; history is not rewritten and its findings are reported | The brief forbids rewriting history without approval |
| 2026-10-21 | One entry point `python -m bufferengine.pipeline.run_all` runs download, base, reference, rotations, clean, evidence, features, checksums | Reproducibility |
| 2026-10-21 | Feature log records leakage status for every built and planned feature: realised-chain fields (`leg_idx`, `n_downstream_legs`) need review; historical delay rates use the training window only | Agreed leakage rules |
