# Notebooks

Run in Databricks Free Edition. Commit the exported `.ipynb` and HTML copies here (small files only, no
raw data in outputs).

| Notebook | Purpose |
|---|---|
| 01_data_audit (done, Phase 1; `.ipynb` + `.html`) | Load BTS AA data, check coverage, nulls, cancellations, diversions, tail-number quality |
| 02_rotation_building (done, Phase 2; `.ipynb` + `.html`) | Chain flights into aircraft rotations; time zones; planned ground time, MTT, buffer |
| 03_eda | Delay patterns by hour, airport, leg position in rotation; propagation evidence |
| 04_prediction_model | Logistic regression baseline and gradient boosting on schedule-only features |
| 05_propagation_simulator | Propagation model and Monte Carlo simulator; validation against LateAircraftDelay |
| 06_optimiser | Exact (HiGHS / OR-Tools) and greedy buffer allocation versus baselines |
| 07_evaluation | 2026 backtest, budget sensitivity, forecast-quality sensitivity, storm stress scenarios |
| 08_export | Write the results workbook for the app |
