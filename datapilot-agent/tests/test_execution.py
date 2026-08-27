from datapilot.execution import execute_sql
from datapilot.ingestion import load_fixture


def test_safe_aggregate_executes():
    result = execute_sql(load_fixture(), "SELECT region, SUM(CAST(revenue AS REAL)) AS total FROM sales GROUP BY region")
    assert "total" in result.columns
    assert result.error is None
