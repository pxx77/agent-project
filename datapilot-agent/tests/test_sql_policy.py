import pytest

from datapilot.sql_policy import validate_sql


@pytest.mark.parametrize("sql", ["DELETE FROM sales", "DROP TABLE sales", "ATTACH 'x' AS x", "SELECT 1; SELECT 2"])
def test_unsafe_sql_is_blocked(sql):
    assert validate_sql(sql).allowed is False


def test_safe_query_is_allowed():
    assert validate_sql("SELECT region FROM sales").allowed is True
