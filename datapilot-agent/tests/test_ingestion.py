from datapilot.ingestion import load_fixture


def test_fixture_has_expected_rows_and_columns():
    handle = load_fixture()
    assert len(handle.rows) == 12
    assert "region" in handle.rows[0]
