import pytest

from datapilot import state


@pytest.fixture(autouse=True)
def _restore_handle():
    """Keep the process-wide dataset from leaking between tests.

    ``state`` is shared by the HTTP API and the MCP server, so the API test that uploads a
    dataset replaces the fixture dataset for every test that runs after it. Restoring the
    original here keeps each test independent of the order pytest happens to pick.
    """
    original = state.get_handle()
    yield
    state.set_handle(original)
