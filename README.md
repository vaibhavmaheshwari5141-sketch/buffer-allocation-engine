# Buffer Delay Allocation Engine

**University analytics capstone: Deliverable 2 (D2), data audit and cleaned dataset.**

| Team member | ID |
|---|---|
| Vaibhav Maheshwari | BA05057 |
| Prashansa Naomi Konduru | BA05042 |
| Anil Kumar | BA05008 |

> Simulation on public BTS data. Not connected to airline systems.

## For the professor: what D2 contains

D2 is a data audit with a cleaned dataset. It is built from the public BTS *Reporting Carrier On-Time Performance* files for
American Airlines mainline flights, January 2025 to August 2026 (**1,641,071 flights, 59 columns after cleaning, no rows deleted**).
Every number in the audit is produced by a script in this repository, and the tables and figures that carry it are committed.

| D2 artefact | Where it lives |
|---|---|
| Audit evidence: quality (missing values, duplicates, outliers, before and after) | [docs/d2/tables/](docs/d2/tables/) (CSV) and [docs/d2/figures/](docs/d2/figures/) (PNG, 200 dpi) |
| Representativeness and bias, ending with who or what is under-represented | [docs/d2/representativeness_summary.md](docs/d2/representativeness_summary.md) |
| Data-quality treatment log (issue, count, percent, treatment, reason) | [data/processed/dq_treatment_log.csv](data/processed/dq_treatment_log.csv) |
| Source log with licence wording, and the data notice | [data/reference/source_log.csv](data/reference/source_log.csv), [DATA_NOTICE.md](DATA_NOTICE.md) |
| **Cleaned dataset** `aa_clean_v1.0.parquet` (68.4 MB) | Committed at [data/processed/aa_clean_v1.0.parquet](data/processed/aa_clean_v1.0.parquet), tagged **`data-v1.0`**. A 5,000-row sample is in [data/sample/aa_clean_sample.csv](data/sample/aa_clean_sample.csv) |
| Checksums and data manifest | [data/CHECKSUMS.sha256](data/CHECKSUMS.sha256), [data/data_manifest.json](data/data_manifest.json) |
| Data dictionary for the cleaned dataset | [docs/data_dictionary_clean.md](docs/data_dictionary_clean.md) (and `.csv`) |
| Cleaning script | [src/bufferengine/pipeline/clean.py](src/bufferengine/pipeline/clean.py) |
| Evidence script (tables and figures) | [src/bufferengine/pipeline/d2_evidence.py](src/bufferengine/pipeline/d2_evidence.py) |
| Feature log (built and planned features, leakage review) | [docs/feature_log.md](docs/feature_log.md) (and `.csv`) |
| Earlier audit notebooks | [notebooks/01_data_audit.ipynb](notebooks/01_data_audit.ipynb), [notebooks/02_rotation_building.ipynb](notebooks/02_rotation_building.ipynb) |
| Public-repo safety scan | [docs/d2/safety_scan.md](docs/d2/safety_scan.md) |
| Release tag | `data-v1.0` |

**Reproduce everything with one command:** `python -m bufferengine.pipeline.run_all` (see [Reproduce data-v1.0](#reproduce-data-v10)).

## Project overview

Given flight-delay forecasts and a fixed daily budget of ground-time buffer minutes, decide where to place
those minutes across American Airlines aircraft rotations to minimise knock-on (propagated) delay.
Built on public BTS on-time performance data.

### Architecture

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

## Reproduce data-v1.0

Requires Python 3.11+ (3.12 recommended), about 3 GB of free disk and an internet connection for the BTS download.

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install pandas pyarrow duckdb requests timezonefinder matplotlib pytest jupyter
pip install -e .

python -m bufferengine.pipeline.run_all                 # download, base, reference, rotations, clean, evidence, checksums
python -m bufferengine.pipeline.manifest --verify       # compare every file with data/CHECKSUMS.sha256
pytest                                                  # all tests
```

`run_all` runs, in order: `download_bts` (existing valid files are skipped), `build_base`, `reference`, `build_rotations`, `clean`
(plus the data dictionary and sample), `d2_evidence`, `feature_log`, and the checksums and manifest. Useful options:
`--skip-download` (raw files already in `data/raw/`), `--skip-evidence`, `--only clean,checksums`. The full run takes about 10 to 15 minutes.

The cleaned file is written byte-for-byte identically on repeated runs; `data/CHECKSUMS.sha256` is the authoritative checksum. The manifest also records a
content hash for the parquet tables as a fallback. Raw BTS files, interim files and the large Phase 1/2 parquet tables are git-ignored; the cleaned dataset `aa_clean_v1.0.parquet` is committed.

## Run the app locally

Requires Node 20+.

```bash
cd app
npm install
npm run dev        # http://localhost:5173
npm run build      # production build into app/dist
```

## Data pipeline pieces

```bash
python -m bufferengine.pipeline.download_bts      # 20 monthly BTS zips into data/raw/ (about 570 MB, resumable)
python -m bufferengine.pipeline.build_base        # data/interim/aa_base_2025_2026.parquet (AA only, 1.64M rows)
python -m bufferengine.pipeline.reference         # data/reference/airports_tz.csv
python -m bufferengine.pipeline.build_rotations   # data/processed/ rotations, rotation_turns, rotation_breaks, mtt_sensitivity
python -m bufferengine.pipeline.clean             # data/processed/aa_clean_v1.0.parquet and dq_treatment_log.csv
python -m bufferengine.pipeline.d2_evidence       # docs/d2/tables and docs/d2/figures
```

See [docs/data_dictionary.md](docs/data_dictionary.md), [docs/fit_gap.md](docs/fit_gap.md), [docs/data_contract.md](docs/data_contract.md) (v1.1)
and [docs/rotation_reconciliation.md](docs/rotation_reconciliation.md).

## Notebooks

Notebooks run in Databricks Free Edition against data in the `bufferengine` Volume. Exported `.ipynb`
and HTML copies are committed to [notebooks/](notebooks/). See [notebooks/README.md](notebooks/README.md)
for the planned list.

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Setup and scaffolding | Done |
| 1 | Data acquisition, audit, data contract | Done |
| 2 | Rotation building, MTT and planned buffer | Done |
| D2 | Cleaned dataset v1.0 and audit evidence | Done |
| 3 | Prediction model | Not started |
| 4 | Propagation model and Monte Carlo simulator | Not started |
| 5 | Optimiser | Not started |
| 6 | Evaluation (backtest, sensitivity, stress) | Not started |
| 7 | Export workbook | Not started |
| 8 | App: console and journey, Netlify deploy | Not started |

## Licence

Code and documentation: MIT ([LICENSE](LICENSE)). Data: see [DATA_NOTICE.md](DATA_NOTICE.md); we claim no ownership of the source data.
