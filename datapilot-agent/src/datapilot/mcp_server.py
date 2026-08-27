from .api import _handle
from .execution import execute_sql
from .profiling import profile_dataset


def get_schema() -> dict:
    return profile_dataset(_handle).model_dump()


def run_safe_query(sql: str) -> dict:
    return execute_sql(_handle, sql).model_dump()


def make_chart_spec(columns: list[str], rows: list[list[object]]) -> dict:
    return {"kind": "bar", "x": columns[0], "y": columns[-1]} if len(columns) >= 2 else {"kind": "table"}
