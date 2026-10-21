# Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-10-21 | Scope: AA mainline only (`Reporting_Airline = AA`) from BTS Reporting Carrier On-Time Performance | Continues D1; one carrier keeps rotations coherent |
| 2026-10-21 | Data periods: 2025 for training/validation, Jan–Aug 2026 for test | Out-of-time backtest |
| 2026-10-21 | Split: train Jan–Sep 2025, validate Oct–Dec 2025, test Jan–Aug 2026 | Time-based split avoids leakage |
| 2026-10-21 | Airport time zones from OurAirports (free) | BTS times are local; needed for ground time |
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
