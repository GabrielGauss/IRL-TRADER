from __future__ import annotations

import pytest

from trading_bot.risk.position_sizing import fixed_fraction_quantity


def test_fixed_fraction_quantity_computes_expected_base_asset_amount():
    # Arrange
    balance, price, fraction = 1000.0, 50000.0, 0.1

    # Act
    quantity = fixed_fraction_quantity(balance, price, fraction)

    # Assert
    assert quantity == pytest.approx(0.002)


def test_fixed_fraction_quantity_full_fraction_spends_entire_balance():
    # Act
    quantity = fixed_fraction_quantity(200.0, 100.0, 1.0)

    # Assert
    assert quantity == pytest.approx(2.0)


def test_fixed_fraction_quantity_raises_on_negative_balance():
    with pytest.raises(ValueError, match="cannot be negative"):
        fixed_fraction_quantity(-1.0, 100.0, 0.1)


@pytest.mark.parametrize("price", [0.0, -50.0])
def test_fixed_fraction_quantity_raises_on_non_positive_price(price):
    with pytest.raises(ValueError, match="price must be positive"):
        fixed_fraction_quantity(1000.0, price, 0.1)


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.1])
def test_fixed_fraction_quantity_raises_on_fraction_out_of_range(fraction):
    with pytest.raises(ValueError, match=r"fraction must be in \(0, 1\]"):
        fixed_fraction_quantity(1000.0, 100.0, fraction)
