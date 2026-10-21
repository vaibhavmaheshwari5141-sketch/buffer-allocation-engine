# Buffer Delay Allocation Engine

Given flight-delay forecasts and a fixed daily budget of ground-time buffer minutes, decide where to place
those minutes across American Airlines aircraft rotations to minimise knock-on (propagated) delay.
Built on public BTS on-time performance data. University analytics capstone.

> Simulation on public BTS data. Not connected to airline systems.

## Architecture

```
BTS on-time data ──► Databricks Free Edition ─────────────────► results .xlsx ──► React app (Netlify)
(AA, 2025–2026)      pipeline · prediction model ·              (small file,       /console  ops view
                     propagation simulator · optimiser          in git)            /journey  walkthrough
```

- **Databricks Free Edition** runs the heavy work: data pipeline, model training, simulation and
  optimisation. Raw data stays there.
- **GitHub** holds code, exported notebooks and small result files.
- **Netlify** serves a static React app that reads the results workbook. There is no backend, and the
  app never runs the optimisation itself.

## Run the app locally

Requires Node 20+.

```bash
cd app
npm install
npm run dev        # http://localhost:5173
npm run build      # production build into app/dist
```

## Python package and tests

Requires Python 3.11+ (3.12 recommended).

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install pytest pandas
pip install -e .
pytest
```

## Data pipeline (Phase 1)

```bash
python -m bufferengine.pipeline.download_bts   # 20 monthly BTS zips into data/raw/ (about 570 MB, resumable)
python -m bufferengine.pipeline.build_base     # data/interim/aa_base_2025_2026.parquet (AA only, 1.64M rows)
python -m bufferengine.pipeline.reference      # data/reference/airports_tz.csv
```

Raw and interim data are git-ignored. See [docs/data_dictionary.md](docs/data_dictionary.md),
[docs/fit_gap.md](docs/fit_gap.md) and [docs/data_contract.md](docs/data_contract.md).

## Notebooks

Notebooks run in Databricks Free Edition against data in the `bufferengine` Volume. Exported `.ipynb`
and HTML copies are committed to [notebooks/](notebooks/). See [notebooks/README.md](notebooks/README.md)
for the planned list.

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Setup and scaffolding | Done |
| 1 | Data acquisition, audit, data contract | Done |
| 2 | Rotation building and EDA | Not started |
| 3 | Prediction model | Not started |
| 4 | Propagation model and Monte Carlo simulator | Not started |
| 5 | Optimiser | Not started |
| 6 | Evaluation (backtest, sensitivity, stress) | Not started |
| 7 | Export workbook | Not started |
| 8 | App: console and journey, Netlify deploy | Not started |
