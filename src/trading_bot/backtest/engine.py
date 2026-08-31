"""Long-only, single-position backtester for a Strategy over historical OHLCV data.

Signals are evaluated on the close of each bar and executed at that same
close price (no slippage or fees modeled) -- a standard simplification for
a first-pass backtest, not a claim of realistic fill behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading_bot.risk.position_sizing import fixed_fraction_quantity
from trading_bot.strategy.base import Signal, Strategy


@dataclass(frozen=True)
class Trade:
    entry_time: object
    entry_price: float
    exit_time: object
    exit_price: float
    quantity: float

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0


@dataclass(frozen=True)
class BacktestResult:
    initial_balance: float
    equity_curve: tuple[float, ...]
    trades: tuple[Trade, ...]

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.initial_balance

    @property
    def total_return_pct(self) -> float:
        return (self.final_equity - self.initial_balance) / self.initial_balance * 100.0

    @property
    def num_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate_pct(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for trade in self.trades if trade.pnl_pct > 0)
        return wins / len(self.trades) * 100.0

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]
        max_drawdown = 0.0
        for equity in self.equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
        return max_drawdown


def run_backtest(
    strategy: Strategy,
    df: pd.DataFrame,
    initial_balance: float = 1000.0,
    position_size_fraction: float = 0.1,
) -> BacktestResult:
    """Replay `strategy` bar-by-bar over `df` (must contain 'open_time' and 'close',
    ordered oldest-to-newest) and report the resulting trades and equity curve."""
    if initial_balance <= 0:
        raise ValueError("initial_balance must be positive")
    if not 0 < position_size_fraction <= 1.0:
        raise ValueError("position_size_fraction must be in (0, 1]")

    min_lookback = getattr(strategy, "min_lookback", 0)

    quote_balance = initial_balance
    base_quantity = 0.0
    entry_price: float | None = None
    entry_time: object = None
    trades: list[Trade] = []
    equity_curve: list[float] = []

    for i in range(min_lookback, len(df)):
        window = df.iloc[: i + 1]
        row = df.iloc[i]
        price = float(row["close"])
        signal = strategy.generate_signal(window)

        if signal == Signal.BUY and base_quantity == 0.0:
            spend_quantity = fixed_fraction_quantity(quote_balance, price, position_size_fraction)
            quote_balance -= spend_quantity * price
            base_quantity = spend_quantity
            entry_price = price
            entry_time = row["open_time"]
        elif signal == Signal.SELL and base_quantity > 0.0:
            quote_balance += base_quantity * price
            trades.append(
                Trade(
                    entry_time=entry_time,
                    entry_price=entry_price,  # type: ignore[arg-type]
                    exit_time=row["open_time"],
                    exit_price=price,
                    quantity=base_quantity,
                )
            )
            base_quantity = 0.0
            entry_price = None
            entry_time = None

        equity_curve.append(quote_balance + base_quantity * price)

    return BacktestResult(
        initial_balance=initial_balance,
        equity_curve=tuple(equity_curve),
        trades=tuple(trades),
    )
