from __future__ import annotations

from .models import ColumnProfile, DatasetHandle, Profile, TableProfile


def profile_dataset(handle: DatasetHandle) -> Profile:
    keys = list(handle.rows[0]) if handle.rows else []
    columns = []
    for key in keys:
        values = [row.get(key, "") for row in handle.rows]
        columns.append(ColumnProfile(name=key, type="number" if all(_number(v) for v in values if v != "") else "string", null_count=sum(v in {None, ""} for v in values), examples=[str(v) for v in values[:3]]))
    return Profile(tables={handle.name: TableProfile(name=handle.name, row_count=len(handle.rows), columns=columns)})


def _number(value: object) -> bool:
    try:
        float(str(value))
        return True
    except ValueError:
        return False
