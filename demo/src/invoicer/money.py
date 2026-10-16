"""Money as Decimal. Floats are never used: 0.1 + 0.2 != 0.3 is not acceptable on an invoice."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from typing_extensions import override

CENT = Decimal("0.01")


def to_cents(value: Decimal) -> Decimal:
    """Round half-up to two decimals (commercial rounding, not banker's rounding)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(amount: Decimal, currency: str = "EUR") -> str:
    """Format as e.g. 'EUR 1,234.50'."""
    return f"{currency} {to_cents(amount):,.2f}"


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError(f"currency must be a 3-letter ISO code, got {self.currency!r}")

    def __add__(self, other: Money) -> Money:
        if other.currency != self.currency:
            raise ValueError(f"cannot add {other.currency} to {self.currency}")
        return Money(self.amount + other.amount, self.currency)

    @override
    def __str__(self) -> str:
        return format_money(self.amount, self.currency)
