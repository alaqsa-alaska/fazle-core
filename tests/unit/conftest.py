"""Unit-layer conftest: clean tables before DB-dependent unit tests."""
import pytest_asyncio


# autouse=False: only runs when a test explicitly requests clean_tables or
# _clean_for_unit. Pure unit tests (no test_db_pool) are not affected.
@pytest_asyncio.fixture(autouse=False)
async def _clean_for_unit(clean_tables):
    """Delegate to the root clean_tables fixture."""
    yield
