"""One agent cycle: decide the target weight, then rebalance through IRL.

The agent never touches an exchange directly. It talks to irl-gateway over
MCP, exactly as any third-party AI agent would: read balances and price,
and if the position is outside the rebalance band, call ``execute_trade``
with a written rationale that IRL seals into the trace.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from trading_bot.agent.core import Decision, decide
from trading_bot.backtest.grids import VolTargetGrid

logger = logging.getLogger(__name__)

# Cash kept back on buys so fees and slippage never overdraw the account.
BUY_HEADROOM = 0.995
MIN_TRADE_QUOTE = 10.0  # Binance's minimum notional on BTC/USDT


class TradingTools(Protocol):
    """The subset of irl-gateway's MCP tools the agent uses."""

    async def get_balances(self) -> dict[str, float]: ...

    async def get_quote(self, symbol: str) -> float: ...

    async def execute_trade(self, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgentConfig:
    symbol: str = "BTC/USDT"
    rebalance_band: float = 0.05
    model_id: str = "vol-target-core/1"


@dataclass(frozen=True)
class CycleReport:
    action: str  # "hold" | "buy" | "sell" | "skipped"
    reason: str
    decision: dict[str, Any]
    current_weight: float
    target_weight: float
    equity_quote: float
    trade: dict[str, Any] | None = None
    balances: dict[str, float] = field(default_factory=dict)


async def run_cycle(
    tools: TradingTools,
    klines: pd.DataFrame,
    now: datetime,
    config: AgentConfig | None = None,
) -> CycleReport:
    config = config or AgentConfig()
    grid = VolTargetGrid()
    decision = decide(klines, now, grid)
    base, quote = config.symbol.split("/")

    balances = await tools.get_balances()
    price = await tools.get_quote(config.symbol)
    base_qty = float(balances.get(base, 0.0))
    cash = float(balances.get(quote, 0.0))
    equity = cash + base_qty * price
    current = base_qty * price / equity if equity > 0 else 0.0
    target = decision.target_weight
    summary = {**_decision_dict(decision), "rationale": decision.describe(grid)}

    def report(action: str, reason: str, trade: dict[str, Any] | None = None) -> CycleReport:
        return CycleReport(
            action, reason, summary, round(current, 4), target, round(equity, 2), trade, balances
        )

    if equity <= 0:
        return report("skipped", f"no {quote} or {base} in the account")
    closing = target == 0.0 and base_qty > 0
    if not closing and abs(target - current) <= config.rebalance_band:
        return report(
            "hold", f"weight {current:.2f} is within {config.rebalance_band:.0%} of {target:.2f}"
        )

    delta_quote = (target - current) * equity
    args: dict[str, Any] = {"symbol": config.symbol, "model_id": config.model_id}
    if delta_quote > 0:
        amount = min(delta_quote, cash * BUY_HEADROOM)
        args.update(side="buy", notional=round(amount, 2))
        size_text = f"buy about {amount:,.2f} {quote}"
    else:
        quantity = base_qty if closing else min(base_qty, -delta_quote / price)
        amount = quantity * price
        args.update(side="sell", quantity=quantity)
        size_text = f"sell {quantity:.8f} {base} (about {amount:,.2f} {quote})"
    if amount < MIN_TRADE_QUOTE:
        return report("skipped", f"rebalance of {amount:.2f} {quote} is below the exchange minimum")

    args["rationale"] = (
        f"{decision.describe(grid)} Current weight {current:.2f} on equity "
        f"{equity:,.2f} {quote} at {price:,.2f}; outside the {config.rebalance_band:.0%} band, "
        f"so {size_text}."
    )
    outcome = await tools.execute_trade(args)
    logger.info("agent %s: %s", args["side"], outcome.get("status"))
    return report(str(args["side"]), str(outcome.get("message", "")), outcome)


def _decision_dict(decision: Decision) -> dict[str, Any]:
    data = asdict(decision)
    data["as_of"] = decision.as_of.isoformat()
    return data
