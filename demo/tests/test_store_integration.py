"""Runs against the PostgreSQL service container in Demo CI (job "integration")."""

from decimal import Decimal

import pytest

from invoicer import LineItem, invoice_totals
from invoicer.store import InvoiceStore

pytestmark = pytest.mark.integration


def test_round_trip(db_store: InvoiceStore) -> None:
    totals = invoice_totals([LineItem("consulting", 3, Decimal("120.00"))], Decimal("0.21"))
    invoice_id = db_store.save("ACME B.V.", totals)
    assert db_store.total_for(invoice_id) == Decimal("435.60")


def test_unknown_invoice(db_store: InvoiceStore) -> None:
    assert db_store.total_for(-1) is None
