from __future__ import annotations


def fixed_fraction_quantity(
    account_balance_quote: float,
    price: float,
    fraction: float,
) -> float:
    """Quantity (in base asset) to buy using a fixed fraction of quote-asset balance.

    e.g. balance=1000 USDT, price=50000 USDT/BTC, fraction=0.1 -> spend 100 USDT -> 0.002 BTC.
    """
    if account_balance_quote < 0:
        raise ValueError("account_balance_quote cannot be negative")
    if price <= 0:
        raise ValueError("price must be positive")
    if not 0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")

    quote_to_spend = account_balance_quote * fraction
    return quote_to_spend / price
