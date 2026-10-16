from __future__ import annotations

from collections.abc import Iterator

import pytest

from invoicer.store import InvoiceStore, connect


@pytest.fixture
def db_store() -> Iterator[InvoiceStore]:
    """A store on the real PostgreSQL service. Fails (not skips) when the DB is unreachable:
    a silently skipped integration test would hide exactly the failure the demo shows."""
    conn = connect()
    try:
        store = InvoiceStore(conn)
        store.create_schema()
        yield store
    finally:
        conn.close()
