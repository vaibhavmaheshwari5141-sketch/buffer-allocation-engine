"""Checksums and data manifest for data-v1.0.

Writes (relative to the repo's data/ folder):
  CHECKSUMS.sha256      "<sha256>  <path relative to data/>" lines, verifiable with `sha256sum -c`
  data_manifest.json    row and column counts, schema hash, source dates, code commit, parameters, content hashes

Which checksum is authoritative: the file SHA-256 in CHECKSUMS.sha256. The clean step writes identical bytes on every run
(verified twice before release). For tables written by DuckDB, the manifest also records ``content_sha256``, a hash of
the logical content (Arrow batches), which stays the same even if a different parquet writer version changes the bytes.
If a file checksum ever differs after a rebuild, compare ``content_sha256``: a match means identical data.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bufferengine.pipeline import clean as C

DATA_VERSION = "data-v1.0"
REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data"

# (path relative to data/, description, committed to git?)
TRACKED_FILES = [
    ("processed/aa_clean_v1.0.parquet", "Cleaned AA flights, all 1,641,071 rows, 59 columns", False),
    ("interim/aa_base_2025_2026.parquet", "Phase 1 base table (AA only, selected columns)", False),
    ("processed/rotations.parquet", "Phase 2 legs table, one row per flight", False),
    ("processed/rotation_turns.parquet", "Phase 2 turns table", False),
    ("processed/rotation_breaks.parquet", "Phase 2 rejected pairs", False),
    ("processed/mtt_sensitivity.parquet", "Phase 2 MTT sensitivity", False),
    ("processed/dq_treatment_log.csv", "Data-quality treatment log", True),
    ("sample/aa_clean_sample.csv", "5,000-row stratified sample of the clean table", True),
]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_sha256(path: Path, batch_rows: int = 200_000) -> str:
    """Hash of the logical content of a parquet file (schema text plus serialised Arrow batches)."""
    pf = pq.ParquetFile(path)
    h = hashlib.sha256(str(pf.schema_arrow).encode())
    for batch in pf.iter_batches(batch_size=batch_rows):
        h.update(batch.serialize())
    return h.hexdigest()


def schema_hash(schema: pa.Schema) -> str:
    return hashlib.sha256("\n".join(f"{f.name}:{f.type}" for f in schema).encode()).hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unavailable"


def source_dates() -> dict:
    log_path = DATA / "interim" / "download_log.csv"
    out: dict = {"bts_months": "2025-01 to 2026-08"}
    if log_path.exists():
        log = pd.read_csv(log_path)
        ts = pd.to_datetime(log["logged_at_utc"])
        out.update({
            "bts_files": int(len(log)), "bts_rows_all_carriers": int(log["rows"].sum()),
            "bts_access_first_utc": ts.min().strftime("%Y-%m-%d"), "bts_access_last_utc": ts.max().strftime("%Y-%m-%d"),
            "bts_url_pattern": log["url"].iloc[0].replace("_2025_1.zip", "_{YEAR}_{MONTH}.zip"),
        })
    return out


def write_checksums_and_manifest(include_content_hash: bool = True) -> dict:
    lines, tables = [], {}
    for rel, desc, committed in TRACKED_FILES:
        p = DATA / rel
        if not p.exists():
            raise FileNotFoundError(f"{p} is missing; run the pipeline first")
        digest = file_sha256(p)
        lines.append(f"{digest}  {rel}")
        entry = {"description": desc, "bytes": p.stat().st_size, "file_sha256": digest, "committed_to_git": committed}
        if p.suffix == ".parquet":
            pf = pq.ParquetFile(p)
            entry.update({"rows": pf.metadata.num_rows, "columns": pf.metadata.num_columns, "schema_sha256": schema_hash(pf.schema_arrow)})
            if include_content_hash:
                entry["content_sha256"] = content_sha256(p)
        else:
            df = pd.read_csv(p, low_memory=False)
            entry.update({"rows": int(len(df)), "columns": int(df.shape[1])})
        tables[rel] = entry
    (DATA / "CHECKSUMS.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    clean_meta = tables["processed/aa_clean_v1.0.parquet"]
    cap_row = duckdb.sql(f"SELECT MIN(arr_delay_min_capped) lo, MAX(arr_delay_min_capped) hi FROM read_parquet('{(DATA / 'processed/aa_clean_v1.0.parquet').as_posix()}')").fetchone()
    manifest = {
        "data_version": DATA_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "authoritative_checksum": "file_sha256 in data/CHECKSUMS.sha256 (identical bytes on repeated runs); content_sha256 is the fallback for parquet tables",
        "row_count_clean": clean_meta["rows"], "column_count_clean": clean_meta["columns"], "schema_sha256_clean": clean_meta["schema_sha256"],
        "expected_rows": C.EXPECTED_ROWS,
        "sources": source_dates(),
        "reference_data": {"airports_tz_csv": "data/reference/airports_tz.csv (derived from OurAirports lat/lon with timezonefinder; one airport from OpenFlights)",
                           "details": "data/reference/source_log.csv"},
        "code_git_commit_at_build": git("rev-parse", "HEAD"),
        "code_git_dirty_at_build": bool(git("status", "--porcelain")),
        "note_on_commit": "The release commit contains this manifest, so it cannot record its own hash. The tag data-v1.0 points to that commit; code_git_commit_at_build is its parent.",
        "environment": {"python": platform.python_version(), "pandas": pd.__version__, "pyarrow": pa.__version__, "duckdb": duckdb.__version__},
        "parameters": {
            "split": {"train": "2025-01-01..2025-09-30", "validate": "2025-10-01..2025-12-31", "test": "2026-01-01..2026-08-31"},
            "arr_delay_cap_quantiles": list(C.CAP_QUANTILES), "arr_delay_cap_low_min": cap_row[0], "arr_delay_cap_high_min": cap_row[1],
            "extreme_delay_min": C.EXTREME_DELAY_MIN, "taxi_extreme_min": C.TAXI_EXTREME_MIN, "elapsed_diff_extreme_min": C.ELAPSED_DIFF_EXTREME_MIN,
            "parquet_compression": f"zstd level {C.ZSTD_LEVEL}", "sample_rows": C.SAMPLE_ROWS, "sample_seed": C.SAMPLE_SEED,
            "rest_gap_min": 300, "mtt_percentile_default": 0.10, "mtt_floor_min": 25, "mtt_cap_min": 60,
        },
        "tables": tables,
    }
    (DATA / "data_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote CHECKSUMS.sha256 ({len(lines)} files) and data_manifest.json")
    return manifest


def verify_checksums() -> list[str]:
    """Return a list of problems (empty if every file matches CHECKSUMS.sha256)."""
    problems = []
    for line in (DATA / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        p = DATA / rel
        if not p.exists():
            problems.append(f"missing {rel}")
        elif file_sha256(p) != digest:
            problems.append(f"checksum mismatch {rel}")
    return problems


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Write or verify data/CHECKSUMS.sha256 and data/data_manifest.json")
    ap.add_argument("--verify", action="store_true", help="only check that every file matches CHECKSUMS.sha256")
    args = ap.parse_args()
    if args.verify:
        problems = verify_checksums()
        print("OK: all files match CHECKSUMS.sha256" if not problems else "PROBLEMS: " + "; ".join(problems))
        raise SystemExit(1 if problems else 0)
    write_checksums_and_manifest()
