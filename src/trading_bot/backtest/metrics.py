"""Risk/return metrics and a cost-aware buy-and-hold benchmark."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from trading_bot.backtest.engine import BacktestResult, Trade

_MINUTES_PER_YEAR = 365 * 24 * 60
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 24 * 60, "w": 7 * 24 * 60}


def annualization_factor(interval: str) -> float:
    """Bars per year for a Binance-style interval ('15m', '1h', '4h', '1d', '1w')."""
    match = re.fullmatch(r"(\d+)([mhdw])", interval)
    if not match:
        raise ValueError(f"unsupported interval {interval!r}")
    minutes = int(match.group(1)) * _UNIT_MINUTES[match.group(2)]
    return _MINUTES_PER_YEAR / minutes


def sharpe_ratio(equity_curve: Sequence[float], interval: str) -> float:
    """Annualized Sharpe of per-bar returns (risk-free rate 0)."""
    if len(equity_curve) < 3:
        return 0.0
    returns = pd.Series(equity_curve, dtype=float).pct_change().dropna()
    std = returns.std(ddof=1)
    if not std or math.isnan(std):
        return 0.0
    return float(returns.mean() / std * math.sqrt(annualization_factor(interval)))


@dataclass(frozen=True)
class Summary:
    total_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    num_trades: int
    win_rate_pct: float
    exposure_pct: float
    profit_factor: float
    fees_paid: float


def summarize(result: BacktestResult, interval: str) -> Summary:
    gross_profit = sum(t.pnl for t in result.trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in result.trades if t.pnl < 0)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = math.inf if gross_profit > 0 else 0.0
    exposure = (
        sum(result.in_position) / len(result.in_position) * 100.0 if result.in_position else 0.0
    )
    return Summary(
        total_return_pct=result.total_return_pct,
        sharpe=sharpe_ratio(result.equity_curve, interval),
        max_drawdown_pct=result.max_drawdown_pct,
        num_trades=result.num_trades,
        win_rate_pct=result.win_rate_pct,
        exposure_pct=exposure,
        profit_factor=profit_factor,
        fees_paid=sum(t.fees for t in result.trades),
    )


def buy_and_hold(
    df: pd.DataFrame,
    *,
    initial_balance: float = 1000.0,
    position_size_fraction: float = 1.0,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> BacktestResult:
    """Buy at the first bar's open, hold, sell at the last close -- paying the
    same fees, slippage and position size as the strategy, so the comparison
    is like for like."""
    fee = fee_bps / 10_000
    slip = slippage_bps / 10_000
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    times = df["open_time"].to_list()

    spend = initial_balance * position_size_fraction
    entry_fee = spend * fee
    entry_price = opens[0] * (1 + slip)
    units = (spend - entry_fee) / entry_price
    cash = initial_balance - spend

    equity = [cash + units * close for close in closes]
    exit_price = closes[-1] * (1 - slip)
    gross = units * exit_price
    exit_fee = gross * fee
    equity[-1] = cash + gross - exit_fee
    trade = Trade(
        entry_time=times[0],
        entry_price=entry_price,
        exit_time=times[-1],
        exit_price=exit_price,
        quantity=units,
        fees=entry_fee + exit_fee,
        net_pnl=gross - exit_fee - spend,
    )
    return BacktestResult(
        initial_balance=initial_balance,
        equity_curve=tuple(equity),
        trades=(trade,),
        in_position=tuple(True for _ in closes),
    )
