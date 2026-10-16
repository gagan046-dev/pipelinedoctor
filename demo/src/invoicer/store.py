"""A tiny PostgreSQL-backed invoice store (exercised by the integration tests in Demo CI)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import psycopg

from invoicer.totals import Totals

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id        SERIAL PRIMARY KEY,
    customer  TEXT           NOT NULL,
    subtotal  NUMERIC(12, 2) NOT NULL,
    tax       NUMERIC(12, 2) NOT NULL,
    total     NUMERIC(12, 2) NOT NULL
)
"""


def dsn_from_env(env: Mapping[str, str] | None = None) -> str:
    """Build a libpq connection string from DEMO_DB_* variables (CI defaults)."""
    env = os.environ if env is None else env
    return (
        f"host={env.get('DEMO_DB_HOST', 'localhost')} "
        f"port={env.get('DEMO_DB_PORT', '5432')} "
        f"user={env.get('DEMO_DB_USER', 'postgres')} "
        f"password={env.get('DEMO_DB_PASSWORD', 'demo')} "
        f"dbname={env.get('DEMO_DB_NAME', 'postgres')} "
        "connect_timeout=5"
    )


def connect(dsn: str | None = None) -> psycopg.Connection[tuple[Any, ...]]:
    return psycopg.connect(dsn or dsn_from_env())


class InvoiceStore:
    def __init__(self, conn: psycopg.Connection[tuple[Any, ...]]) -> None:
        self._conn = conn

    def create_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(SCHEMA)
        self._conn.commit()

    def save(self, customer: str, totals: Totals) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO invoices (customer, subtotal, tax, total)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (customer, totals.subtotal, totals.tax, totals.total),
            )
            row = cur.fetchone()
        self._conn.commit()
        if row is None:
            raise RuntimeError("INSERT ... RETURNING returned no row")
        return int(row[0])

    def total_for(self, invoice_id: int) -> Decimal | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT total FROM invoices WHERE id = %s", (invoice_id,))
            row = cur.fetchone()
        return None if row is None else Decimal(row[0])
