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
