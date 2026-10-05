from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pandas as pd
import pytest

from trading_bot import cli, cli_backtest
from trading_bot.config import Settings
from trading_bot.execution.engine import RiskLimitBreached


def _klines_df(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=len(prices), freq="1h"),
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1.0] * len(prices),
        }
    )


def _fake_settings(db_path: str) -> Settings:
    return Settings.model_construct(
        binance_api_key="key",
        binance_api_secret="secret",
        use_testnet=True,
        i_understand_live_trading_risk=False,
        max_daily_loss_pct=3.0,
        max_drawdown_pct=10.0,
        position_size_fraction=0.1,
        db_path=db_path,
    )


def test_build_parser_backtest_defaults_to_mainnet_public_data():
    parser = cli.build_parser()
    args = parser.parse_args(["backtest"])
    assert args.symbol == "BTCUSDT"
    assert args.interval == "1h"
    assert args.testnet is False
    assert args.func is cli.cmd_backtest


def test_build_parser_run_defaults():
    parser = cli.build_parser()
    args = parser.parse_args(["run"])
    assert args.base_asset == "BTC"
    assert args.quote_asset == "USDT"
    assert args.once is False
    assert args.func is cli.cmd_run


def test_build_parser_requires_a_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_cmd_backtest_prints_summary_and_returns_zero(monkeypatch, capsys):
    # Arrange
    fake_client = MagicMock()
    fake_client.get_klines.return_value = _klines_df([100.0] * 40)
    monkeypatch.setattr(cli_backtest, "BinanceClient", lambda settings: fake_client)

    parser = cli.build_parser()
    args = parser.parse_args(["backtest", "--symbol", "BTCUSDT", "--limit", "40"])

    # Act
    exit_code = cli.cmd_backtest(args)

    # Assert
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "BTCUSDT" in out
    assert "Final equity" in out
    fake_client.get_klines.assert_called_once_with("BTCUSDT", "1h", limit=40)


def test_cmd_run_executes_one_iteration_and_returns_zero_with_once_flag(monkeypatch, tmp_path):
    # Arrange
    fake_client = MagicMock()
    fake_client.get_klines.return_value = _klines_df([100.0] * 40)
    fake_client.get_balance.side_effect = lambda asset: {"BTC": 0.0, "USDT": 1000.0}[asset]
    monkeypatch.setattr(cli, "BinanceClient", lambda settings: fake_client)
    monkeypatch.setattr(cli, "load_settings", lambda: _fake_settings(str(tmp_path / "t.db")))

    parser = cli.build_parser()
    args = parser.parse_args(["run", "--once"])

    # Act
    exit_code = cli.cmd_run(args)

    # Assert
    assert exit_code == 0
    fake_client.get_klines.assert_called_once()


def test_cmd_run_returns_one_and_halts_when_risk_limit_breached(monkeypatch, tmp_path):
    # Arrange
    class RaisingEngine:
        def __init__(self, **kwargs):
            pass

        def run_once(self, interval, limit):
            raise RiskLimitBreached("Daily loss 10.00% >= limit 3.0%")

    monkeypatch.setattr(cli, "BinanceClient", lambda settings: MagicMock())
    monkeypatch.setattr(cli, "ExecutionEngine", RaisingEngine)
    monkeypatch.setattr(cli, "load_settings", lambda: _fake_settings(str(tmp_path / "t.db")))

    parser = cli.build_parser()
    args = parser.parse_args(["run"])

    # Act
    exit_code = cli.cmd_run(args)

    # Assert
    assert exit_code == 1


def test_cmd_run_loops_and_sleeps_between_iterations_when_not_once(monkeypatch, tmp_path):
    # Arrange: stop the otherwise-infinite loop after 2 iterations via the sleep call,
    # the same way a real deployment would stop it (Ctrl+C / SIGINT -> KeyboardInterrupt)
    call_count = {"n": 0}

    class CountingEngine:
        def __init__(self, **kwargs):
            pass

        def run_once(self, interval, limit):
            call_count["n"] += 1

    def fake_sleep(seconds):
        if call_count["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "BinanceClient", lambda settings: MagicMock())
    monkeypatch.setattr(cli, "ExecutionEngine", CountingEngine)
    monkeypatch.setattr(cli, "load_settings", lambda: _fake_settings(str(tmp_path / "t.db")))
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)

    parser = cli.build_parser()
    args = parser.parse_args(["run"])

    # Act / Assert
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_run(args)
    assert call_count["n"] == 2


def test_main_delegates_to_subcommand_func_and_returns_its_exit_code(monkeypatch):
    fake_args = argparse.Namespace(func=lambda args: 42)
    fake_parser = MagicMock()
    fake_parser.parse_args.return_value = fake_args
    monkeypatch.setattr(cli, "build_parser", lambda: fake_parser)

    assert cli.main([]) == 42


def test_main_returns_130_on_keyboard_interrupt(monkeypatch):
    def raising_func(args):
        raise KeyboardInterrupt

    fake_args = argparse.Namespace(func=raising_func)
    fake_parser = MagicMock()
    fake_parser.parse_args.return_value = fake_args
    monkeypatch.setattr(cli, "build_parser", lambda: fake_parser)

    assert cli.main([]) == 130


def _history(n: int = 900):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(5)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    opens = np.concatenate([[100.0], closes[:-1]])
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=n, freq="1h"),
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
            "volume": 1.0,
        }
    )


def test_backtest_defaults_to_realistic_costs_and_fills(monkeypatch, capsys):
    captured = {}

    def fake_run_backtest(strategy, df, **kwargs):
        captured.update(kwargs)
        from trading_bot.backtest.engine import BacktestResult

        return BacktestResult(initial_balance=1000.0, equity_curve=(1000.0, 1001.0), trades=())

    monkeypatch.setattr(cli_backtest, "load_public_data_settings", lambda use_testnet: object())
    monkeypatch.setattr(cli_backtest, "BinanceClient", lambda settings: object())
    monkeypatch.setattr(cli_backtest, "load_history", lambda *a, **k: _history(200))
    monkeypatch.setattr(cli_backtest, "run_backtest", fake_run_backtest)

    exit_code = cli.main(["backtest", "--start", "2024-01-01"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["fill"] == "next_open"
    assert captured["fee_bps"] == 10.0
    assert captured["slippage_bps"] == 5.0
    assert "Buy & hold" in out
    assert "Sharpe" in out


def test_walkforward_command_prints_fold_table_and_verdict(monkeypatch, capsys):
    monkeypatch.setattr(cli_backtest, "load_public_data_settings", lambda use_testnet: object())
    monkeypatch.setattr(cli_backtest, "BinanceClient", lambda settings: object())
    monkeypatch.setattr(cli_backtest, "load_history", lambda *a, **k: _history(900))

    exit_code = cli.main(
        [
            "walkforward",
            "--start",
            "2024-01-01",
            "--train-bars",
            "400",
            "--test-bars",
            "250",
            "--fast-ema-grid",
            "5,8",
            "--slow-ema-grid",
            "20",
            "--rsi-oversold-grid",
            "35",
            "--rsi-overbought-grid",
            "65",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "fold" in out.lower()
    assert "Out-of-sample" in out
    assert "Buy & hold" in out
