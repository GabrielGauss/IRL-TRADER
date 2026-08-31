"""Wires a Strategy's signal to a risk-sized live order: one call per bar/tick.

Position state is read directly from exchange balances rather than tracked
locally, so a restart can never desync from what's actually held. Risk
limits are likewise evaluated from persisted equity history, not in-memory
state, so `run_once` is safe to call again after a crash or restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from trading_bot.exchange.binance_client import BinanceClient, OrderSide
from trading_bot.persistence.repository import TradeRecord, TradeRepository
from trading_bot.risk.position_sizing import fixed_fraction_quantity
from trading_bot.strategy.base import Signal, Strategy

logger = logging.getLogger(__name__)

# Balances below this are treated as "no position" (dust / float noise), not a real holding.
MIN_POSITION_QUANTITY = 1e-8


@dataclass(frozen=True)
class RiskLimits:
    max_daily_loss_pct: float
    max_drawdown_pct: float


class RiskLimitBreached(Exception):
    """A risk limit blocked this trading decision. Caller should halt, not retry immediately."""


class ExecutionEngine:
    def __init__(
        self,
        client: BinanceClient,
        strategy: Strategy,
        repository: TradeRepository,
        risk_limits: RiskLimits,
        base_asset: str,
        quote_asset: str,
        position_size_fraction: float,
    ):
        self._client = client
        self._strategy = strategy
        self._repository = repository
        self._risk_limits = risk_limits
        self._base_asset = base_asset
        self._quote_asset = quote_asset
        self._position_size_fraction = position_size_fraction

    @property
    def symbol(self) -> str:
        return f"{self._base_asset}{self._quote_asset}"

    def run_once(self, interval: str = "1h", limit: int = 200) -> TradeRecord | None:
        """Fetch fresh data, evaluate the strategy, and place at most one order.

        Returns the resulting TradeRecord, or None if no trade was made.
        Raises RiskLimitBreached (before placing any order) if daily loss or
        drawdown limits are hit.
        """
        df = self._client.get_klines(self.symbol, interval, limit=limit)
        price = float(df["close"].iloc[-1])

        base_balance = self._client.get_balance(self._base_asset)
        quote_balance = self._client.get_balance(self._quote_asset)
        equity = quote_balance + base_balance * price

        self._check_risk_limits(equity)

        signal = self._strategy.generate_signal(df)
        in_position = base_balance > MIN_POSITION_QUANTITY

        trade_record: TradeRecord | None = None
        if signal == Signal.BUY and not in_position:
            quantity = fixed_fraction_quantity(quote_balance, price, self._position_size_fraction)
            trade_record = self._execute(OrderSide.BUY, quantity, price)
        elif signal == Signal.SELL and in_position:
            trade_record = self._execute(OrderSide.SELL, base_balance, price)
        else:
            logger.info("No action: signal=%s in_position=%s", signal, in_position)

        self._repository.record_equity(equity)
        return trade_record

    def _execute(self, side: OrderSide, quantity: float, price: float) -> TradeRecord:
        order = self._client.place_market_order(self.symbol, side, quantity)
        record = TradeRecord(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            price=price,
            status=order.status,
            executed_at=datetime.now(UTC),
        )
        self._repository.save_trade(record)
        logger.info("Executed %s %s qty=%s @ %s", side.value, self.symbol, quantity, price)
        return record

    def _check_risk_limits(self, current_equity: float) -> None:
        now = datetime.now(UTC)
        equity_history = self._repository.get_equity_since(now - timedelta(days=1))
        if not equity_history:
            return

        day_start_equity = equity_history[0].equity
        if day_start_equity > 0:
            daily_loss_pct = (day_start_equity - current_equity) / day_start_equity * 100.0
            if daily_loss_pct >= self._risk_limits.max_daily_loss_pct:
                raise RiskLimitBreached(
                    f"Daily loss {daily_loss_pct:.2f}% >= limit "
                    f"{self._risk_limits.max_daily_loss_pct}%"
                )

        peak_equity = max(snapshot.equity for snapshot in equity_history)
        peak_equity = max(peak_equity, current_equity)
        if peak_equity > 0:
            drawdown_pct = (peak_equity - current_equity) / peak_equity * 100.0
            if drawdown_pct >= self._risk_limits.max_drawdown_pct:
                raise RiskLimitBreached(
                    f"Drawdown {drawdown_pct:.2f}% >= limit {self._risk_limits.max_drawdown_pct}%"
                )
