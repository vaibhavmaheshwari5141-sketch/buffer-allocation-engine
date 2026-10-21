# Dataset release: `data-v1.0`

The cleaned dataset is **committed to the repository** at `data/processed/aa_clean_v1.0.parquet` (68.4 MB,
1,641,071 rows, 59 columns) and tagged `data-v1.0`.

We originally planned to distribute it as a GitHub Release asset, because our own working rule capped committed
files at 50 MB. That rule was ours, not GitHub's: the platform accepts files up to 100 MB per file and only warns
above 50 MB. Since the D2 brief requires the cleaned dataset to be *in the repository*, the file is committed
directly. The decision is recorded in `docs/decisions.md`.

## What ships with it

| Artefact | Path |
|---|---|
| Cleaned dataset | `data/processed/aa_clean_v1.0.parquet` |
| 5,000-row stratified sample (250 per month, seed 42) | `data/sample/aa_clean_sample.csv` |
| SHA-256 checksums | `data/CHECKSUMS.sha256` |
| Manifest (row/column counts, schema hash, source dates, parameters, environment) | `data/data_manifest.json` |
| Data dictionary | `docs/data_dictionary_clean.md`, `.csv` |
| Cleaning code | `src/bufferengine/pipeline/` |

The other parquet tables (base table, rotations, turns, breaks, MTT sensitivity) remain git-ignored. They are
rebuilt by the pipeline and their checksums are recorded in `data/CHECKSUMS.sha256`.

## Verify your copy

```bash
python -m bufferengine.pipeline.manifest --verify
```

`--verify` reports any file in `CHECKSUMS.sha256` that is missing or different. Files you have not rebuilt yet
(the git-ignored tables) are reported as missing, which is expected until you run the pipeline.

On Windows PowerShell, to check the one file:

```powershell
Get-FileHash data\processed\aa_clean_v1.0.parquet -Algorithm SHA256
```

Compare against the `processed/aa_clean_v1.0.parquet` line in `data/CHECKSUMS.sha256`.

## Rebuild from raw

```bash
python -m bufferengine.pipeline.run_all
```

This downloads the 20 monthly BTS files (skipping any already present), builds the base table, reference tables
and rotations, cleans, regenerates the audit evidence and rewrites the checksums. Output is byte-identical across
runs, so the file SHA-256 is authoritative.

## Versioning

`data-v1.0` is immutable. Any change to a column name, type, unit or null rule bumps the data contract version
(`docs/data_contract.md`) and produces a new tag. Earlier tags stay retrievable and verifiable.
