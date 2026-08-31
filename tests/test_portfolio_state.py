from __future__ import annotations

import pytest

from trading_bot.risk.portfolio import PortfolioState


def test_new_portfolio_has_zero_position_and_configured_cash():
    portfolio = PortfolioState(cash=1000.0)
    assert portfolio.position_quantity("BTCUSDT") == 0.0
    assert portfolio.cash == 1000.0


def test_apply_fill_buy_opens_a_new_position_and_debits_cash():
    portfolio = PortfolioState(cash=1000.0)

    updated = portfolio.apply_fill("BTCUSDT", "BUY", quantity=0.1, price=100.0)

    assert updated.cash == pytest.approx(990.0)
    assert updated.position_quantity("BTCUSDT") == pytest.approx(0.1)
    assert portfolio.position_quantity("BTCUSDT") == 0.0  # original untouched (immutable)


def test_apply_fill_buy_again_blends_average_entry_price():
    portfolio = PortfolioState(cash=1000.0)
    portfolio = portfolio.apply_fill("BTCUSDT", "BUY", quantity=1.0, price=100.0)

    updated = portfolio.apply_fill("BTCUSDT", "BUY", quantity=1.0, price=120.0)

    assert updated.position_quantity("BTCUSDT") == pytest.approx(2.0)
    assert updated.positions["BTCUSDT"].average_entry_price == pytest.approx(110.0)


def test_apply_fill_sell_partial_reduces_quantity_and_credits_cash():
    portfolio = PortfolioState(cash=1000.0).apply_fill("BTCUSDT", "BUY", quantity=1.0, price=100.0)

    updated = portfolio.apply_fill("BTCUSDT", "SELL", quantity=0.4, price=110.0)

    assert updated.position_quantity("BTCUSDT") == pytest.approx(0.6)
    assert updated.cash == pytest.approx(900.0 + 44.0)


def test_apply_fill_sell_full_position_realizes_pnl_and_closes_position():
    portfolio = PortfolioState(cash=1000.0).apply_fill("BTCUSDT", "BUY", quantity=1.0, price=100.0)

    updated = portfolio.apply_fill("BTCUSDT", "SELL", quantity=1.0, price=130.0)

    assert updated.position_quantity("BTCUSDT") == 0.0
    assert "BTCUSDT" not in updated.positions
    assert updated.realized_pnl == pytest.approx(30.0)


def test_apply_fill_sell_more_than_held_raises():
    portfolio = PortfolioState(cash=1000.0).apply_fill("BTCUSDT", "BUY", quantity=0.5, price=100.0)

    with pytest.raises(ValueError, match="only holding"):
        portfolio.apply_fill("BTCUSDT", "SELL", quantity=1.0, price=100.0)


def test_apply_fill_sell_while_flat_raises():
    portfolio = PortfolioState(cash=1000.0)
    with pytest.raises(ValueError, match="only holding"):
        portfolio.apply_fill("BTCUSDT", "SELL", quantity=0.1, price=100.0)


@pytest.mark.parametrize("quantity", [0.0, -1.0])
def test_apply_fill_rejects_non_positive_quantity(quantity):
    portfolio = PortfolioState(cash=1000.0)
    with pytest.raises(ValueError, match="quantity must be positive"):
        portfolio.apply_fill("BTCUSDT", "BUY", quantity=quantity, price=100.0)


def test_market_value_and_equity_use_supplied_mark_prices():
    portfolio = PortfolioState(cash=500.0).apply_fill("BTCUSDT", "BUY", quantity=2.0, price=100.0)

    market_value = portfolio.market_value({"BTCUSDT": 150.0})
    equity = portfolio.equity({"BTCUSDT": 150.0})

    assert market_value == pytest.approx(300.0)
    assert equity == pytest.approx(300.0 + 300.0)


def test_unrealized_pnl_reflects_price_move_from_entry():
    portfolio = PortfolioState(cash=1000.0).apply_fill("BTCUSDT", "BUY", quantity=2.0, price=100.0)

    unrealized = portfolio.unrealized_pnl({"BTCUSDT": 130.0})

    assert unrealized == pytest.approx(60.0)


def test_equity_raises_when_mark_price_missing_for_open_position():
    portfolio = PortfolioState(cash=1000.0).apply_fill("BTCUSDT", "BUY", quantity=1.0, price=100.0)
    with pytest.raises(KeyError):
        portfolio.equity({})
