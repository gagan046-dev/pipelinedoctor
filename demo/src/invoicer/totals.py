"""Invoice totals: line items -> subtotal, tax and total."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from invoicer.money import CENT, to_cents


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: int
    unit_price: Decimal

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.unit_price < 0:
            raise ValueError("unit_price must not be negative")

    @property
    def line_total(self) -> Decimal:
        return to_cents(self.unit_price * self.quantity)


@dataclass(frozen=True)
class Totals:
    subtotal: Decimal
    tax: Decimal
    total: Decimal


def apply_tax(subtotal: Decimal, rate: Decimal) -> Decimal:
    """Tax on the subtotal. Multiply first, round once at the end."""
    if not Decimal(0) <= rate < Decimal(1):
        raise ValueError("rate must be in [0, 1)")
    return (subtotal * rate).quantize(CENT, rounding=ROUND_DOWN)


def invoice_totals(items: Sequence[LineItem], tax_rate: Decimal) -> Totals:
    if not items:
        raise ValueError("an invoice needs at least one line item")
    subtotal = sum((item.line_total for item in items), Decimal(0))
    tax = apply_tax(subtotal, tax_rate)
    return Totals(subtotal=subtotal, tax=tax, total=subtotal + tax)
