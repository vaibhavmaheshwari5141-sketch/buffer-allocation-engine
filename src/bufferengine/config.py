"""Shared paths. Everything is resolved under DATA_ROOT so the same code runs locally and in Databricks.

Locally DATA_ROOT defaults to the repo's ``data/`` folder. In Databricks set it to the Volume, e.g.
``os.environ["DATA_ROOT"] = "/Volumes/workspace/default/bufferengine"`` before importing.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_DATA = Path(__file__).resolve().parents[2] / "data"


def data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", _REPO_DATA))


def raw_dir() -> Path:
    return data_root() / "raw"


def interim_dir() -> Path:
    return data_root() / "interim"


def processed_dir() -> Path:
    return data_root() / "processed"


def reference_dir() -> Path:
    return data_root() / "reference"


BASE_TABLE_NAME = "aa_base_2025_2026.parquet"


def base_table_path() -> Path:
    return interim_dir() / BASE_TABLE_NAME
