from __future__ import annotations

import re
import sqlite3
import time

from .models import DatasetHandle, QueryResult
from .sql_policy import validate_sql


def execute_sql(handle: DatasetHandle, sql: str, max_rows: int = 1000) -> QueryResult:
    started = time.perf_counter()
    decision = validate_sql(sql)
    if not decision.allowed:
        return QueryResult(error=decision.reason, duration_ms=(time.perf_counter() - started) * 1000, policy_blocked=True)
    connection = sqlite3.connect(":memory:")
    try:
        columns = list(handle.rows[0]) if handle.rows else []
        connection.execute(f'CREATE TABLE "{handle.name}" ({", ".join(chr(34) + c.replace(chr(34), "") + chr(34) + " TEXT" for c in columns)})')
        for row in handle.rows:
            connection.execute(f'INSERT INTO "{handle.name}" VALUES ({", ".join("?" for _ in columns)})', [row.get(c) for c in columns])
        cursor = connection.execute(sql)
        names = [item[0] for item in cursor.description or []]
        rows = [list(item) for item in cursor.fetchmany(max_rows)]
        return QueryResult(columns=names, rows=rows, row_count=len(rows), duration_ms=(time.perf_counter() - started) * 1000)
    except Exception as exc:
        return QueryResult(error=str(exc), duration_ms=(time.perf_counter() - started) * 1000)
    finally:
        connection.close()
