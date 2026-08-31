from __future__ import annotations

import pytest

from trading_bot.risk.volatility_sizing import volatility_adjusted_quantity


def test_sizing_matches_fixed_fraction_when_volatility_is_at_target():
    quantity = volatility_adjusted_quantity(
        account_balance_quote=1000.0,
        price=100.0,
        base_fraction=0.1,
        current_volatility=0.02,
        target_volatility=0.02,
    )
    assert quantity == pytest.approx(1000.0 * 0.1 / 100.0)


def test_sizing_scales_down_when_current_volatility_exceeds_target():
    quantity = volatility_adjusted_quantity(
        account_balance_quote=1000.0,
        price=100.0,
        base_fraction=0.1,
        current_volatility=0.04,
        target_volatility=0.02,
    )
    # scale = 0.02/0.04 = 0.5 -> fraction 0.05
    assert quantity == pytest.approx(1000.0 * 0.05 / 100.0)


def test_sizing_scales_up_but_is_capped_at_max_fraction():
    quantity = volatility_adjusted_quantity(
        account_balance_quote=1000.0,
        price=100.0,
        base_fraction=0.1,
        current_volatility=0.01,
        target_volatility=0.05,
        max_fraction=0.3,
    )
    # scale = 5.0 -> fraction 0.5, clamped to max_fraction 0.3
    assert quantity == pytest.approx(1000.0 * 0.3 / 100.0)


def test_sizing_is_floored_at_min_fraction_in_extreme_volatility():
    quantity = volatility_adjusted_quantity(
        account_balance_quote=1000.0,
        price=100.0,
        base_fraction=0.1,
        current_volatility=10.0,
        target_volatility=0.02,
        min_fraction=0.01,
    )
    assert quantity == pytest.approx(1000.0 * 0.01 / 100.0)


@pytest.mark.parametrize("current_volatility", [0.0, -0.01])
def test_sizing_rejects_non_positive_current_volatility(current_volatility):
    with pytest.raises(ValueError, match="current_volatility must be positive"):
        volatility_adjusted_quantity(
            account_balance_quote=1000.0,
            price=100.0,
            base_fraction=0.1,
            current_volatility=current_volatility,
            target_volatility=0.02,
        )


@pytest.mark.parametrize("target_volatility", [0.0, -0.01])
def test_sizing_rejects_non_positive_target_volatility(target_volatility):
    with pytest.raises(ValueError, match="target_volatility must be positive"):
        volatility_adjusted_quantity(
            account_balance_quote=1000.0,
            price=100.0,
            base_fraction=0.1,
            current_volatility=0.02,
            target_volatility=target_volatility,
        )
