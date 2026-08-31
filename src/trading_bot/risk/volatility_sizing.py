"""Volatility-adjusted position sizing.

Scales the base fixed-fraction size by (target_volatility / current_volatility)
so risk-per-trade stays roughly constant in volatility-normalized terms: size
down in turbulent regimes, size up (capped) in calm ones. `current_volatility`
and `target_volatility` are on the same, caller-defined scale (e.g. ATR/price,
or stdev of returns over a lookback) -- this function doesn't compute them,
it only applies the ratio.
"""

from __future__ import annotations

from trading_bot.risk.position_sizing import fixed_fraction_quantity


def volatility_adjusted_quantity(
    account_balance_quote: float,
    price: float,
    base_fraction: float,
    current_volatility: float,
    target_volatility: float,
    min_fraction: float = 0.01,
    max_fraction: float = 1.0,
) -> float:
    if current_volatility <= 0:
        raise ValueError("current_volatility must be positive")
    if target_volatility <= 0:
        raise ValueError("target_volatility must be positive")

    scale = target_volatility / current_volatility
    adjusted_fraction = base_fraction * scale
    clamped_fraction = min(max(adjusted_fraction, min_fraction), max_fraction)
    return fixed_fraction_quantity(account_balance_quote, price, clamped_fraction)
