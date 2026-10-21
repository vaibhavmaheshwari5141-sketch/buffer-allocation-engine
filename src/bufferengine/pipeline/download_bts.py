"""Download BTS Reporting Carrier On-Time Performance monthly zips into data/raw/.

Resumable: files that already exist and open as valid zips are skipped. Downloads go to a ``.part``
file first and are renamed only when complete. A short delay separates requests.

Usage:
    python -m bufferengine.pipeline.download_bts                 # 2025-01 .. 2026-08
    python -m bufferengine.pipeline.download_bts --start 2025-01 --end 2025-03
"""

from __future__ import annotations

import argparse
import csv
import io
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from bufferengine.config import interim_dir, raw_dir

URL_PATTERN = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
FILE_PATTERN = "bts_ontime_{year}_{month:02d}.zip"
DEFAULT_START = (2025, 1)
DEFAULT_END = (2026, 8)
LOG_COLUMNS = ["year", "month", "file", "url", "size_bytes", "csv_name", "rows", "status", "logged_at_utc"]


def month_range(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    (y, m), out = start, []
    while (y, m) <= end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def is_valid_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return any(n.lower().endswith(".csv") for n in zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def csv_member(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if len(names) != 1:
        raise ValueError(f"expected one CSV in zip, found {names}")
    return names[0]


def count_rows(path: Path) -> tuple[str, int]:
    """Data rows (excluding header) in the zip's CSV, parsed with csv so quoted commas are safe."""
    with zipfile.ZipFile(path) as zf:
        name = csv_member(zf)
        with zf.open(name) as fh:
            reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline=""))
            next(reader)
            return name, sum(1 for _ in reader)


def download_month(year: int, month: int, dest_dir: Path, session: requests.Session, timeout: int = 300) -> str:
    """Download one month. Returns 'skipped', 'downloaded'; raises on failure."""
    dest = dest_dir / FILE_PATTERN.format(year=year, month=month)
    if dest.exists() and is_valid_zip(dest):
        return "skipped"
    url = URL_PATTERN.format(year=year, month=month)
    part = dest.with_suffix(".zip.part")
    with session.get(url, stream=True, timeout=timeout) as r:
        if r.status_code == 404:
            raise FileNotFoundError(f"{year}-{month:02d}: BTS returned 404 (not published yet?) {url}")
        r.raise_for_status()
        if "zip" not in r.headers.get("Content-Type", ""):
            raise ValueError(f"{year}-{month:02d}: unexpected content type {r.headers.get('Content-Type')}")
        with open(part, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    if not is_valid_zip(part):
        part.unlink(missing_ok=True)
        raise ValueError(f"{year}-{month:02d}: downloaded file is not a valid zip")
    part.replace(dest)
    return "downloaded"


def write_log(rows: list[dict], log_path: Path) -> None:
    """Merge rows into the log, keeping the latest entry per (year, month)."""
    existing: dict[tuple[int, int], dict] = {}
    if log_path.exists():
        with open(log_path, newline="") as fh:
            for r in csv.DictReader(fh):
                existing[(int(r["year"]), int(r["month"]))] = r
    for r in rows:
        existing[(r["year"], r["month"])] = r
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_COLUMNS)
        w.writeheader()
        for key in sorted(existing):
            w.writerow(existing[key])


def run(start: tuple[int, int] = DEFAULT_START, end: tuple[int, int] = DEFAULT_END, delay_s: float = 3.0) -> list[dict]:
    dest_dir = raw_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "bufferengine-capstone/0.1 (university research; polite monthly download)"
    months = month_range(start, end)
    log_rows, failures = [], []
    for i, (y, m) in enumerate(months):
        fname = FILE_PATTERN.format(year=y, month=m)
        try:
            status = download_month(y, m, dest_dir, session)
            if status == "downloaded" and i < len(months) - 1:
                time.sleep(delay_s)
        except Exception as exc:  # report and continue with the remaining months
            print(f"FAILED {y}-{m:02d}: {exc}")
            failures.append((y, m, str(exc)))
            continue
        path = dest_dir / fname
        csv_name, rows = count_rows(path)
        size = path.stat().st_size
        print(f"{status:10s} {fname}  {size / 1e6:6.1f} MB  {rows:>9,} rows")
        log_rows.append({
            "year": y, "month": m, "file": fname, "url": URL_PATTERN.format(year=y, month=m),
            "size_bytes": size, "csv_name": csv_name, "rows": rows, "status": status,
            "logged_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    write_log(log_rows, interim_dir() / "download_log.csv")
    if failures:
        print(f"{len(failures)} month(s) failed; rerun to retry: {[f'{y}-{m:02d}' for y, m, _ in failures]}")
    return log_rows


def _ym(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=_ym, default=DEFAULT_START, help="YYYY-MM (default 2025-01)")
    p.add_argument("--end", type=_ym, default=DEFAULT_END, help="YYYY-MM (default 2026-08)")
    p.add_argument("--delay", type=float, default=3.0, help="seconds between downloads")
    a = p.parse_args()
    run(a.start, a.end, a.delay)
