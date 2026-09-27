from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
from pathlib import Path

from .models import DatasetHandle

FIXTURES_ENV = "DATAPILOT_FIXTURES"


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


def fixtures_dir() -> Path:
    """Return the directory holding the datasets bundled with the project.

    ``fixtures/`` sits beside ``src/`` in the source tree, but a container image copies it to
    the working directory and installs the package into ``site-packages``, so the directory
    cannot be derived from this module's own location. The candidates are tried in order and
    the first directory that exists wins; ``DATAPILOT_FIXTURES`` short-circuits the search.
    """
    override = os.environ.get(FIXTURES_ENV)
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend(parent / "fixtures" for parent in Path(__file__).resolve().parents)
    candidates.append(Path.cwd() / "fixtures")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    searched = "\n".join(f"  - {item}" for item in candidates)
    raise FileNotFoundError(f"Could not locate the fixtures directory. Set {FIXTURES_ENV} to point at it. Searched:\n{searched}")


def load_fixture(name: str = "sales.csv") -> DatasetHandle:
    """Load one of the bundled datasets by file name.

    ``state.py`` calls this while the module is being imported, so the lookup has to work both
    from a source checkout and from an installed package.
    """
    path = fixtures_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"{name} is not present in {path.parent}")
    return load_bytes(name, path.read_bytes())
