"""Unit tests for the store with a fake connection (no database needed)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from invoicer import Totals
from invoicer import store as store_mod
from invoicer.store import InvoiceStore, dsn_from_env


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows.pop(0)


class FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...] | None] | None = None) -> None:
        self.cur = FakeCursor(rows or [])
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return self.cur

    def commit(self) -> None:
        self.commits += 1


def make_store(conn: FakeConnection) -> InvoiceStore:
    return InvoiceStore(conn)  # type: ignore[arg-type]


def test_dsn_defaults_and_overrides() -> None:
    assert "port=5432" in dsn_from_env({})
    dsn = dsn_from_env({"DEMO_DB_PORT": "5433", "DEMO_DB_HOST": "db"})
    assert "host=db port=5433" in dsn
    assert dsn.endswith("connect_timeout=5")


def test_connect_uses_env_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_connect(dsn: str) -> str:
        seen.append(dsn)
        return "conn"

    monkeypatch.setenv("DEMO_DB_PORT", "6543")
    monkeypatch.setattr(store_mod.psycopg, "connect", fake_connect)
    assert store_mod.connect() == "conn"
    assert "port=6543" in seen[0]


def test_create_schema_commits() -> None:
    conn = FakeConnection()
    make_store(conn).create_schema()
    assert "CREATE TABLE IF NOT EXISTS invoices" in conn.cur.executed[0][0]
    assert conn.commits == 1


def test_save_returns_id_and_reads_back() -> None:
    conn = FakeConnection(rows=[(42,), (Decimal("12.10"),)])
    store = make_store(conn)
    new_id = store.save("ACME", Totals(Decimal("10"), Decimal("2.10"), Decimal("12.10")))
    assert new_id == 42
    assert store.total_for(42) == Decimal("12.10")


def test_save_without_row_raises() -> None:
    store = make_store(FakeConnection(rows=[None]))
    with pytest.raises(RuntimeError):
        store.save("x", Totals(Decimal(1), Decimal(0), Decimal(1)))


def test_total_for_missing_invoice() -> None:
    assert make_store(FakeConnection(rows=[None])).total_for(7) is None
