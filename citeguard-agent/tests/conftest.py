import pytest

from citeguard import state


@pytest.fixture(autouse=True)
def _restore_index():
    """Keep the process-wide index from leaking between tests.

    ``state`` is shared by the HTTP API and the MCP server, so the API test that uploads a
    document replaces the fixture index for every test that runs after it. Restoring the
    original here keeps each test independent of the order pytest happens to pick.
    """
    original = state.get_index()
    yield
    state.set_index(original)
