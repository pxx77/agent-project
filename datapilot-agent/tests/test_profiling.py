from datapilot.ingestion import load_fixture
from datapilot.profiling import profile_dataset


def test_profile_has_columns():
    profile = profile_dataset(load_fixture())
    assert "region" in [c.name for c in profile.tables["sales"].columns]
