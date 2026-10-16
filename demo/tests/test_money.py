from decimal import Decimal

import pytest

from invoicer import Money, format_money, to_cents


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.005", "1.01"), ("2.675", "2.68"), ("0.004", "0.00"), ("-1.005", "-1.01")],
)
def test_to_cents_rounds_half_up(value: str, expected: str) -> None:
    assert to_cents(Decimal(value)) == Decimal(expected)


def test_format_money_groups_thousands() -> None:
    assert format_money(Decimal("1234.5")) == "EUR 1,234.50"
    assert format_money(Decimal("7"), "USD") == "USD 7.00"


def test_money_str_and_add() -> None:
    total = Money(Decimal("10.10")) + Money(Decimal("0.20"))
    assert total == Money(Decimal("10.30"))
    assert str(total) == "EUR 10.30"


def test_money_rejects_mixed_currencies() -> None:
    with pytest.raises(ValueError, match="cannot add USD to EUR"):
        _ = Money(Decimal("1")) + Money(Decimal("1"), "USD")


@pytest.mark.parametrize("currency", ["eur", "EURO", "E1R"])
def test_money_rejects_bad_currency(currency: str) -> None:
    with pytest.raises(ValueError, match="3-letter ISO code"):
        Money(Decimal("1"), currency)
