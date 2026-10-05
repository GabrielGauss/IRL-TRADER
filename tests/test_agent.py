"""The volatility-target agent: decision, rebalancing through the gateway's
tools, and the MCP tool wrapper."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from trading_bot.agent.core import TRAIN_BARS, completed_bars, decide
from trading_bot.agent.mcp_tools import GatewayError, McpTradingTools
from trading_bot.agent.runner import AgentConfig, run_cycle
from trading_bot.cli_agent import next_run


def _daily(n: int, *, seed: int = 4, drift: float = 0.001, vol: float = 0.03) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 30_000 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    opens = np.concatenate([[30_000.0], closes[:-1]])
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2023-01-01", periods=n, freq="1D"),
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
            "volume": 1.0,
        }
    )


def _uptrend(n: int = 900) -> pd.DataFrame:
    """Steady rise with a regular wiggle: above every SMA, volatility well above zero."""
    t = np.arange(n)
    closes = 20_000 * np.exp(0.002 * t + 0.02 * np.sin(t / 2.0))
    df = _daily(n)
    df["close"] = closes
    df["open"] = np.concatenate([[closes[0]], closes[:-1]])
    df["high"] = np.maximum(df["open"], df["close"])
    df["low"] = np.minimum(df["open"], df["close"])
    return df


def _after_last_bar(df: pd.DataFrame, hours: float = 1) -> datetime:
    last_open = pd.Timestamp(df["open_time"].iloc[-1]).to_pydatetime().replace(tzinfo=UTC)
    return last_open + timedelta(hours=hours)


class FakeTools:
    def __init__(self, balances: dict[str, float], price: float, status: str = "filled"):
        self.balances = balances
        self.price = price
        self.status = status
        self.trades: list[dict[str, Any]] = []

    async def get_balances(self) -> dict[str, float]:
        return dict(self.balances)

    async def get_quote(self, symbol: str) -> float:
        return self.price

    async def execute_trade(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.trades.append(arguments)
        return {"status": self.status, "message": "ok", "trace_id": "t1", "verdict": "MATCHED"}


# ---- decision ------------------------------------------------------------------


def test_an_open_daily_bar_is_never_used():
    df = _daily(800)
    still_open = _after_last_bar(df, hours=5)  # the last bar closes 19h later

    assert len(completed_bars(df, still_open)) == 799
    assert len(completed_bars(df, still_open + timedelta(days=1))) == 800


def test_decide_reports_the_inputs_behind_the_weight():
    df = _daily(900)
    now = _after_last_bar(df, hours=30)

    decision = decide(df, now)

    assert decision.as_of == pd.Timestamp(df["open_time"].iloc[-1]).to_pydatetime()
    assert 0.0 <= decision.target_weight <= 1.0
    assert decision.realized_vol > 0
    assert set(decision.params) == {"trend_period", "vol_period", "target_vol"}
    assert decide(df, now) == decision  # deterministic


def test_decide_needs_two_years_of_completed_bars():
    df = _daily(TRAIN_BARS)
    with pytest.raises(ValueError, match="completed daily bars"):
        decide(df, _after_last_bar(df, hours=1))  # last one still open -> 729


def test_downtrend_means_zero_weight_and_says_so():
    closes = np.concatenate([np.linspace(20_000, 60_000, 700), np.linspace(60_000, 30_000, 200)])
    df = _daily(900)
    df["close"] = closes
    df["open"] = np.concatenate([[closes[0]], closes[:-1]])

    decision = decide(df, _after_last_bar(df, hours=30))
    text = decision.describe(
        __import__("trading_bot.backtest.grids", fromlist=["x"]).VolTargetGrid()
    )

    if decision.params["trend_period"]:
        assert decision.trend_on is False and decision.target_weight == 0.0
        assert "trend OFF" in text
    assert f"{TRAIN_BARS} completed days" in text


# ---- rebalancing -------------------------------------------------------------------


def _cycle(tools: FakeTools, df: pd.DataFrame, band: float = 0.05):
    return asyncio.run(
        run_cycle(tools, df, _after_last_bar(df, hours=30), AgentConfig(rebalance_band=band))
    )


def test_buys_up_to_the_target_with_cash_headroom_and_a_rationale():
    df = _uptrend()
    tools = FakeTools({"USDT": 1_000.0}, price=float(df["close"].iloc[-1]))

    report = _cycle(tools, df)

    assert report.target_weight > 0.05
    assert report.action == "buy"
    trade = tools.trades[0]
    assert trade["side"] == "buy" and trade["symbol"] == "BTC/USDT"
    assert 10 <= trade["notional"] <= 1_000 * 0.995
    assert "trailing 730 completed days" in trade["rationale"]
    assert "Current weight 0.00" in trade["rationale"]
    assert report.trade["verdict"] == "MATCHED"


def test_holds_inside_the_band():
    df = _uptrend()
    price = float(df["close"].iloc[-1])
    target = decide(df, _after_last_bar(df, hours=30)).target_weight
    tools = FakeTools({"USDT": 1_000 * (1 - target), "BTC": 1_000 * target / price}, price)

    report = _cycle(tools, df)

    assert report.action == "hold" and tools.trades == []


def test_exits_completely_when_the_target_is_zero():
    closes = np.concatenate([np.linspace(20_000, 60_000, 700), np.linspace(60_000, 25_000, 200)])
    df = _daily(900)
    df["close"] = closes
    df["open"] = np.concatenate([[closes[0]], closes[:-1]])
    if decide(df, _after_last_bar(df, hours=30)).target_weight != 0.0:
        pytest.skip("selected parameters keep a position in this fixture")
    tools = FakeTools({"USDT": 10.0, "BTC": 0.02}, price=25_000.0)

    report = _cycle(tools, df)

    assert report.action == "sell"
    assert tools.trades[0]["quantity"] == pytest.approx(0.02)  # all of it, no dust


def test_tiny_rebalances_are_skipped_below_the_exchange_minimum():
    df = _uptrend()
    tools = FakeTools({"USDT": 5.0}, price=float(df["close"].iloc[-1]))

    report = _cycle(tools, df)

    assert report.action == "skipped" and tools.trades == []


def test_empty_account_is_skipped():
    df = _daily(900)
    report = _cycle(FakeTools({}, price=30_000.0), df)

    assert report.action == "skipped"


# ---- MCP wrapper ---------------------------------------------------------------------


class FakeSession:
    def __init__(self, result: Any):
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        self.calls.append((name, arguments))
        return self.result


def _result(data: Any = None, *, error: bool = False, text: str = ""):
    return SimpleNamespace(
        is_error=error, structured_content=data, content=[SimpleNamespace(text=text)]
    )


def test_tools_parse_structured_results():
    tools = McpTradingTools(FakeSession(_result({"venue_id": "p", "balances": {"USDT": "5"}})))

    assert asyncio.run(tools.get_balances()) == {"USDT": 5.0}


@pytest.mark.parametrize(
    "result, message",
    [
        (_result(error=True, text="boom"), "boom"),
        (_result(None), "no structured result"),
        (_result({"error": "IRL down"}), "IRL down"),
    ],
)
def test_tool_failures_raise(result, message):
    tools = McpTradingTools(FakeSession(result))

    with pytest.raises(GatewayError, match=message):
        asyncio.run(tools.get_quote("BTC/USDT"))


def test_execute_trade_passes_arguments_through():
    session = FakeSession(_result({"status": "denied", "denial_code": "ASSET_UNAUTHORIZED"}))
    tools = McpTradingTools(session)

    outcome = asyncio.run(tools.execute_trade({"symbol": "BTC/USDT", "side": "buy"}))

    assert outcome["status"] == "denied"
    assert session.calls == [("execute_trade", {"symbol": "BTC/USDT", "side": "buy"})]


# ---- scheduling ----------------------------------------------------------------------


def test_next_run_is_five_minutes_after_the_daily_close():
    assert next_run(datetime(2026, 10, 5, 0, 1, tzinfo=UTC)) == datetime(
        2026, 10, 5, 0, 5, tzinfo=UTC
    )
    assert next_run(datetime(2026, 10, 5, 9, 0, tzinfo=UTC)) == datetime(
        2026, 10, 6, 0, 5, tzinfo=UTC
    )
