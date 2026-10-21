# CLAUDE.md — Buffer Delay Allocation Engine

University analytics capstone. Read this before making changes.

## Problem

An airline flies one aircraft on several flights a day in a chain (a **rotation**). If an early flight
lands late, the aircraft departs late on the next leg and the delay propagates ("dominoes"). Airlines add
planned ground-time **buffer** to absorb delays, but buffer costs aircraft utilisation, so the total is
limited (e.g. 500 extra minutes per day across the network).

**Question:** given delay forecasts and a fixed buffer budget, where should the minutes go to minimise
knock-on (propagated) delay? Spreading evenly is wasteful; early-in-day legs matter more because they
have more downstream legs.

This continues an earlier capstone (D1) that predicted whether American Airlines flights arrive 15+
minutes late using the public BTS dataset. This project rebuilds a simple version of that prediction
model, then adds propagation modelling, simulation and optimisation.

## Pipeline

1. **Data** — BTS "Reporting Carrier On-Time Performance (1987-present)", AA mainline only
   (`Reporting_Airline = AA`), 2025 (train) and Jan–Aug 2026 (test). Key fields: `Tail_Number`,
   `Flight_Number_Reporting_Airline`, `FlightDate`, `Origin`, `Dest`, `CRSDepTime`, `CRSArrTime`,
   `CRSElapsedTime`, `Distance`, `DepDelay`, `ArrDelay`, `ArrDel15`, `Cancelled`, `Diverted`,
   `LateAircraftDelay`. BTS times are local, so airport time zones are needed to compute ground time:
   the IANA zone is derived from OurAirports lat/lon with the free `timezonefinder` library (OpenFlights
   `airports.dat` as fallback), not taken from OurAirports directly. Real flights stay real; derived fields (planned ground time, minimum turnaround
   time (MTT), planned buffer) are computed; cost per buffer/delay minute are labelled adjustable
   assumptions; synthetic data is used only for stress scenarios.
2. **Prediction model** — gradient boosting (XGBoost or LightGBM) plus a logistic regression baseline,
   using schedule-only (pre-departure) features, no outcome leakage. Outputs per flight:
   P(arrival delay >= 15 min) and expected delay minutes with uncertainty.
   Split: train Jan–Sep 2025, validate Oct–Dec 2025, test Jan–Aug 2026.
   Metrics: precision, recall, PR-AUC, calibration, top-10% lift versus historical delay-rate baseline.
3. **Propagation model + Monte Carlo simulator** over aircraft rotations, validated against BTS
   `LateAircraftDelay`.
4. **Optimiser** — exact solver (open-source HiGHS or OR-Tools) and a greedy method versus baselines
   (no buffer, even spread, historical-rate-based), with total budget and per-turn max constraints.
5. **Evaluation** — 2026 backtest, budget sensitivity, forecast-quality sensitivity, storm stress
   scenarios.
6. **Export** — an `.xlsx` results file with sheets: `manifest`, `kpis`, `strategy_comparison`,
   `budget_curve`, `turn_plans`, `rotations`, `risk_heatmap`. The app ships with a default `.xlsx` and
   lets the user upload a new one in the browser (validated against a data contract, reset-to-default
   button). The app never computes the optimisation itself and never connects to Databricks.
7. **App** — one React + TypeScript + Vite app deployed to Netlify with two sections: `/console` (the
   operations manager product) and `/journey` (a guided walkthrough for the professor: problem, data and
   fit-gap, lineage, data quality, rendered notebooks, code links, model cards, optimisation formulation,
   results, and a reconciliation panel comparing notebook KPIs with app KPIs).

## Where things run

- **Databricks Free Edition** — heavy pipeline, notebooks, model training, simulation, optimisation.
  Raw data lives there, not in git. Unity Catalog: catalog `workspace`, schema `default`, Volume `bufferengine`; notebook path `/Volumes/workspace/default/bufferengine/` (CLI path `dbfs:/Volumes/workspace/default/bufferengine/`). Inside it, folders mirror the repo `data/` folder: `interim/`, `reference/`, `processed/`.
- **GitHub repo** — code, notebooks (exported `.ipynb` and HTML), small result files only.
- **Netlify free tier** — static frontend only. No Python backend. No paid services anywhere.

## Hard rules

- Free tools only. Do not add paid APIs, paid hosting or paid services.
- Never commit raw BTS data or any file over 25 MB. Keep raw data git-ignored.
- Never use outcome fields (`DepDelay`, `ArrDelay`, `TaxiOut`, etc.) as prediction features.
- Keep real data real. Anything synthetic or assumed must be labelled as such in code, files and the UI.
- Work in small, reviewable steps and report what was done.

## Repo layout

```
CLAUDE.md               this file
README.md               overview, how to run
netlify.toml            Netlify build config (base = app)
requirements.txt        planned Python packages
pyproject.toml          makes src/bufferengine importable
data/
  raw/                  BTS downloads (git-ignored)
  interim/              intermediate files (git-ignored)
  processed/            small processed outputs
  reference/            small reference tables (e.g. airport time zones)
notebooks/              01_data_audit ... 08_export (.ipynb + HTML exports)
src/bufferengine/
  pipeline/             ingestion, cleaning, rotation building
  features/             schedule-only feature engineering
  models/               prediction models
  simulation/           propagation model + Monte Carlo
  optimiser/            exact + greedy buffer allocation
  export/               results workbook writer
tests/                  pytest tests
docs/
  decisions.md          decision log
  data_contract.md      table and workbook schemas
app/                    React + TypeScript + Vite frontend
  public/data/          default results workbook
```

## Local environment

- Python venv at `.venv` (built with Python 3.12 for wheel compatibility with the ML/solver packages).
- Node 20+ for the app.

## Phases

| Phase | Scope |
|---|---|
| 0 | Setup and scaffolding |
| 1 | Data acquisition, audit, data contract |
| 2 | Rotation building and EDA |
| 3 | Prediction model |
| 4 | Propagation model and Monte Carlo simulator |
| 5 | Optimiser |
| 6 | Evaluation (backtest, sensitivity, stress) |
| 7 | Export workbook |
| 8 | App: console and journey, Netlify deploy |
