"""`trading-bot agent`: the volatility-target core trading through irl-gateway.

Runs one cycle (decide, then rebalance through IRL if outside the band) and
prints a JSON report. With --loop it repeats once a day, shortly after the
daily candle closes at 00:00 UTC.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from trading_bot.agent.mcp_tools import gateway_tools
from trading_bot.agent.runner import AgentConfig, CycleReport, run_cycle
from trading_bot.config import load_public_data_settings
from trading_bot.exchange.binance_client import BinanceClient

logger = logging.getLogger(__name__)

KLINE_LIMIT = 1000  # > 730 training bars plus the longest lookback
AFTER_CLOSE = timedelta(minutes=5)


def add_agent_command(subparsers: argparse._SubParsersAction) -> None:
    agent = subparsers.add_parser(
        "agent", help="Run the volatility-target core through irl-gateway (MCP)"
    )
    agent.add_argument("--symbol", default="BTC/USDT", help="BASE/QUOTE (default BTC/USDT)")
    agent.add_argument("--rebalance-band", type=float, default=0.05)
    agent.add_argument(
        "--gateway-command",
        help="command that starts irl-gateway (default: $IRL_GATEWAY_COMMAND or irl-gateway)",
    )
    agent.add_argument("--loop", action="store_true", help="repeat daily after the 00:00 UTC close")
    agent.set_defaults(func=cmd_agent)


def cmd_agent(args: argparse.Namespace) -> int:
    config = AgentConfig(symbol=args.symbol, rebalance_band=args.rebalance_band)
    if not args.loop:
        report = asyncio.run(_cycle(config, args.gateway_command))
        print(json.dumps(asdict(report), indent=2, default=str))
        return 0 if report.trade is None or report.trade.get("status") == "filled" else 2
    while True:
        try:
            report = asyncio.run(_cycle(config, args.gateway_command))
            print(json.dumps(asdict(report), default=str), flush=True)
        except Exception:  # log and try again tomorrow; trades fail closed
            logger.exception("agent cycle failed; no trade was attempted after the failure")
        _sleep_until_next_close()


async def _cycle(config: AgentConfig, gateway_command: str | None) -> CycleReport:
    client = BinanceClient(load_public_data_settings(use_testnet=False))
    klines = await asyncio.to_thread(
        client.get_klines, config.symbol.replace("/", ""), "1d", KLINE_LIMIT
    )
    async with gateway_tools(gateway_command) as tools:
        return await run_cycle(tools, klines, datetime.now(UTC), config)


def next_run(now: datetime) -> datetime:
    today_close = now.replace(hour=0, minute=0, second=0, microsecond=0) + AFTER_CLOSE
    return today_close if now < today_close else today_close + timedelta(days=1)


def _sleep_until_next_close() -> None:
    import time

    now = datetime.now(UTC)
    wait = (next_run(now) - now).total_seconds()
    logger.info("next agent cycle at %s", next_run(now).isoformat())
    time.sleep(max(wait, 1.0))
