from __future__ import annotations

import csv
import io
import re
import sqlite3

from .models import DatasetHandle


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return value or "dataset"


def load_bytes(name: str, data: bytes, max_rows: int = 1000) -> DatasetHandle:
    suffix = name.lower().rsplit(".", 1)[-1] if "." in name else "csv"
    if suffix == "csv":
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8", errors="replace"))))[:max_rows]
    elif suffix == "xlsx":
        import pandas as pd
        rows = pd.read_excel(io.BytesIO(data)).head(max_rows).fillna("").to_dict(orient="records")
    elif suffix == "sqlite":
        connection = sqlite3.connect(":memory:")
        connection.deserialize(data)
        table = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 1").fetchone()
        if not table:
            raise ValueError("SQLite file contains no tables")
        cursor = connection.execute(f'SELECT * FROM "{table[0]}" LIMIT {max_rows}')
        rows = [dict(zip([item[0] for item in cursor.description], values)) for values in cursor.fetchall()]
        connection.close()
    else:
        raise ValueError("Unsupported file type. Use CSV, XLSX, or SQLite.")
    if not rows:
        raise ValueError("Uploaded dataset contains no data rows")
    return DatasetHandle(_safe_name(name.rsplit(".", 1)[0]), rows)


def load_fixture(name: str = "sales.csv") -> DatasetHandle:
    from pathlib import Path
    return load_bytes(name, (Path(__file__).parents[2] / "fixtures" / name).read_bytes())
