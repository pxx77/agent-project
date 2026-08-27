from __future__ import annotations

import csv
import io
import re

from .models import DatasetHandle


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return value or "dataset"


def load_bytes(name: str, data: bytes, max_rows: int = 1000) -> DatasetHandle:
    suffix = name.lower().rsplit(".", 1)[-1] if "." in name else "csv"
    if suffix != "csv":
        raise ValueError("MVP upload accepts CSV; XLSX/SQLite adapters can use the same DatasetHandle interface.")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8", errors="replace"))))[:max_rows]
    return DatasetHandle(_safe_name(name.rsplit(".", 1)[0]), rows)


def load_fixture(name: str = "sales.csv") -> DatasetHandle:
    from pathlib import Path
    return load_bytes(name, (Path(__file__).parents[2] / "fixtures" / name).read_bytes())
