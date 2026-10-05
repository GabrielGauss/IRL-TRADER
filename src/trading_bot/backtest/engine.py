"""Long-only, single-position backtester for a Strategy over historical OHLCV data.

Realism is opt-in to keep the original behaviour as the default:

- ``fill="close"`` (default, legacy): a signal computed on a bar's close is
  filled at that same close -- optimistic, since you can't trade on a price
  you only learn once the bar has closed.
- ``fill="next_open"``: the order is filled at the **next** bar's open, the
  earliest realistic execution. A signal on the final bar is not filled.
- ``fee_bps`` / ``slippage_bps``: taker fee on each side, and adverse price
  slippage (buys fill higher, sells lower).

The CLI uses the realistic settings by default.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading_bot.strategy.base import Signal, Strategy

FILL_MODES = ("close", "next_open")


@dataclass(frozen=True)
class Trade:
    entry_time: object
    entry_price: float
    exit_time: object
    exit_price: float
    quantity: float
    fees: float = 0.0
    net_pnl: float | None = None

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0

    @property
    def pnl(self) -> float:
        """Net P&L in quote currency (after fees when recorded by the engine)."""
        if self.net_pnl is not None:
            return self.net_pnl
        return (self.exit_price - self.entry_price) * self.quantity


@dataclass(frozen=True)
class BacktestResult:
    initial_balance: float
    equity_curve: tuple[float, ...]
    trades: tuple[Trade, ...]
    in_position: tuple[bool, ...] = ()

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
    *,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    fill: str = "close",
) -> BacktestResult:
    """Replay `strategy` over `df` (must contain 'open_time', 'open' and
    'close', ordered oldest-to-newest) and report trades and the equity curve."""
    _validate(initial_balance, position_size_fraction, fill)
    signals = strategy.generate_signals(df)
    start = getattr(strategy, "min_lookback", 0)
    return simulate(
        df,
        signals,
        start=start,
        initial_balance=initial_balance,
        position_size_fraction=position_size_fraction,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        fill=fill,
    )


def simulate(
    df: pd.DataFrame,
    signals: pd.Series,
    *,
    start: int = 0,
    initial_balance: float = 1000.0,
    position_size_fraction: float = 0.1,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    fill: str = "close",
) -> BacktestResult:
    """Simulate precomputed per-bar `signals` from bar `start` (flat at start).

    Bars before `start` only serve as indicator history; this is how
    walk-forward scores a test window that begins mid-series.
    """
    _validate(initial_balance, position_size_fraction, fill)
    fee = fee_bps / 10_000
    slip = slippage_bps / 10_000
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    times = df["open_time"].to_list()
    sigs = signals.to_list()

    quote = initial_balance
    units = 0.0
    entry_price = 0.0
    entry_time: object = None
    entry_cost = 0.0
    entry_fee = 0.0
    pending: Signal | None = None
    trades: list[Trade] = []
    equity: list[float] = []
    in_position: list[bool] = []

    def buy(raw_price: float, when: object) -> None:
        nonlocal quote, units, entry_price, entry_time, entry_cost, entry_fee
        spend = quote * position_size_fraction
        entry_fee = spend * fee
        entry_price = raw_price * (1 + slip)
        units = (spend - entry_fee) / entry_price
        quote -= spend
        entry_cost = spend
        entry_time = when

    def sell(raw_price: float, when: object) -> None:
        nonlocal quote, units
        exit_price = raw_price * (1 - slip)
        gross = units * exit_price
        exit_fee = gross * fee
        proceeds = gross - exit_fee
        quote += proceeds
        trades.append(
            Trade(
                entry_time=entry_time,
                entry_price=entry_price,
                exit_time=when,
                exit_price=exit_price,
                quantity=units,
                fees=entry_fee + exit_fee,
                net_pnl=proceeds - entry_cost,
            )
        )
        units = 0.0

    for i in range(start, len(df)):
        if pending is not None:
            if pending is Signal.BUY and units == 0.0:
                buy(opens[i], times[i])
            elif pending is Signal.SELL and units > 0.0:
                sell(opens[i], times[i])
            pending = None

        signal = sigs[i]
        if fill == "close":
            if signal == Signal.BUY and units == 0.0:
                buy(closes[i], times[i])
            elif signal == Signal.SELL and units > 0.0:
                sell(closes[i], times[i])
        elif signal in (Signal.BUY, Signal.SELL):
            pending = Signal(signal)

        equity.append(quote + units * closes[i])
        in_position.append(units > 0.0)

    return BacktestResult(
        initial_balance=initial_balance,
        equity_curve=tuple(equity),
        trades=tuple(trades),
        in_position=tuple(in_position),
    )


def _validate(initial_balance: float, position_size_fraction: float, fill: str) -> None:
    if initial_balance <= 0:
        raise ValueError("initial_balance must be positive")
    if not 0 < position_size_fraction <= 1.0:
        raise ValueError("position_size_fraction must be in (0, 1]")
    if fill not in FILL_MODES:
        raise ValueError(f"fill must be one of {FILL_MODES}, got {fill!r}")
