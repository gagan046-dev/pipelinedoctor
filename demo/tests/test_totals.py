from decimal import Decimal

import pytest

from invoicer import LineItem, apply_tax, invoice_totals


def test_line_total_rounds_to_cents() -> None:
    assert LineItem("pens", 3, Decimal("0.335")).line_total == Decimal("1.01")


def test_apply_tax_rounds_after_multiplying() -> None:
    # 19.99 * 0.21 = 4.1979 -> 4.20 (half-up). Truncating would give 4.19.
    assert apply_tax(Decimal("19.99"), Decimal("0.21")) == Decimal("4.20")


def test_total_with_tax() -> None:
    items = [LineItem("widget", 2, Decimal("49.95")), LineItem("shipping", 1, Decimal("7.10"))]
    totals = invoice_totals(items, Decimal("0.075"))
    assert totals.subtotal == Decimal("107.00")
    assert totals.tax == Decimal("8.03")
    assert totals.total == Decimal("115.03")


def test_zero_tax() -> None:
    totals = invoice_totals([LineItem("gift", 1, Decimal("5"))], Decimal("0"))
    assert totals.total == Decimal("5")


@pytest.mark.parametrize("rate", ["-0.01", "1", "1.5"])
def test_apply_tax_rejects_bad_rate(rate: str) -> None:
    with pytest.raises(ValueError, match="rate"):
        apply_tax(Decimal("10"), Decimal(rate))


def test_invoice_needs_items() -> None:
    with pytest.raises(ValueError, match="at least one"):
        invoice_totals([], Decimal("0.2"))


@pytest.mark.parametrize(("quantity", "price"), [(0, "1"), (-1, "1"), (1, "-0.01")])
def test_line_item_validation(quantity: int, price: str) -> None:
    with pytest.raises(ValueError):
        LineItem("bad", quantity, Decimal(price))
