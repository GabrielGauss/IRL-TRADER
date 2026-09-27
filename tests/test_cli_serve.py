from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from trading_bot import cli
from trading_bot.config import Settings, WebhookSettings
from trading_bot.execution.broker import CcxtBroker, PaperBroker


def _fake_settings() -> Settings:
    return Settings.model_construct(
        binance_api_key="key",
        binance_api_secret="secret",
        use_testnet=True,
        i_understand_live_trading_risk=False,
        max_daily_loss_pct=3.0,
        max_drawdown_pct=10.0,
        position_size_fraction=0.1,
        db_path="unused.db",
    )


_WEBHOOK_SECRET = "cli-test-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def _webhook_secret_configured(monkeypatch):
    """Serve refuses to start without a webhook secret; give every test one by default."""
    monkeypatch.setattr(
        cli,
        "load_webhook_settings",
        lambda: WebhookSettings.model_construct(signal_webhook_secret=SecretStr(_WEBHOOK_SECRET)),
    )


def _no_webhook_secret(monkeypatch):
    monkeypatch.setattr(
        cli,
        "load_webhook_settings",
        lambda: WebhookSettings.model_construct(signal_webhook_secret=None),
    )


def _serve_args(parser_overrides: dict | None = None) -> object:
    parser = cli.build_parser()
    argv = ["serve"]
    for key, value in (parser_overrides or {}).items():
        argv += [key, str(value)]
    return parser.parse_args(argv)


def test_serve_defaults_to_paper_mode():
    args = _serve_args()
    assert args.paper is True
    assert args.func is cli.cmd_serve


def test_serve_live_flag_disables_paper_mode():
    parser = cli.build_parser()
    args = parser.parse_args(["serve", "--live"])
    assert args.paper is False


def test_build_serve_runtime_paper_mode_wraps_a_ccxt_price_source(tmp_path, monkeypatch):
    fake_price_broker = MagicMock(spec=CcxtBroker)
    monkeypatch.setattr(cli, "CcxtBroker", lambda *a, **k: fake_price_broker)

    parser = cli.build_parser()
    args = parser.parse_args(["serve", "--db-path", str(tmp_path / "t.db")])
    runtime = cli._build_serve_runtime(args)

    assert isinstance(runtime.broker, PaperBroker)
    assert runtime.price_broker is fake_price_broker


def test_build_serve_runtime_live_mode_uses_load_settings_and_ccxt_broker(tmp_path, monkeypatch):
    fake_broker = MagicMock(spec=CcxtBroker)
    monkeypatch.setattr(cli, "CcxtBroker", lambda *a, **k: fake_broker)
    monkeypatch.setattr(cli, "load_settings", _fake_settings)

    parser = cli.build_parser()
    args = parser.parse_args(["serve", "--live", "--db-path", str(tmp_path / "t.db")])
    runtime = cli._build_serve_runtime(args)

    assert runtime.broker is fake_broker
    assert runtime.price_broker is None


def test_build_serve_runtime_live_mode_rejects_non_binance_exchange(tmp_path):
    parser = cli.build_parser()
    args = parser.parse_args(
        ["serve", "--live", "--exchange-id", "kraken", "--db-path", str(tmp_path / "t.db")]
    )
    with pytest.raises(ValueError, match="binance"):
        cli._build_serve_runtime(args)


def test_cmd_status_prints_health_and_metrics(monkeypatch, capsys):
    health_payload = {
        "healthy": True,
        "uptime_seconds": 12.5,
        "signals_processed": 3,
        "kill_switch_tripped": False,
        "last_error": None,
    }
    metrics_payload = {
        "equity": 1000.0,
        "cash": 900.0,
        "realized_pnl": 5.0,
        "unrealized_pnl": 2.0,
        "drawdown_pct": 1.5,
        "sharpe_ratio": 0.8,
        "num_trades": 2,
    }

    responses = {"/health": health_payload, "/metrics": metrics_payload}

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=None):
        for path, payload in responses.items():
            if url.endswith(path):
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    parser = cli.build_parser()
    args = parser.parse_args(["status"])
    exit_code = cli.cmd_status(args)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Healthy" in out
    assert "1000.00" in out


def test_cmd_status_prints_last_error_when_present(monkeypatch, capsys):
    health_payload = {
        "healthy": False,
        "uptime_seconds": 1.0,
        "signals_processed": 0,
        "kill_switch_tripped": True,
        "last_error": "broker timeout",
    }
    metrics_payload = {
        "equity": 0.0,
        "cash": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "drawdown_pct": 0.0,
        "sharpe_ratio": 0.0,
        "num_trades": 0,
    }
    responses = {"/health": health_payload, "/metrics": metrics_payload}

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=None):
        for path, payload in responses.items():
            if url.endswith(path):
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    parser = cli.build_parser()
    args = parser.parse_args(["status"])
    cli.cmd_status(args)

    out = capsys.readouterr().out
    assert "broker timeout" in out


def test_cmd_status_returns_one_when_unreachable(monkeypatch, capsys):
    def fake_urlopen(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    parser = cli.build_parser()
    args = parser.parse_args(["status"])
    exit_code = cli.cmd_status(args)

    assert exit_code == 1


def test_build_serve_runtime_refuses_to_start_without_webhook_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CcxtBroker", lambda *a, **k: MagicMock(spec=CcxtBroker))
    _no_webhook_secret(monkeypatch)

    args = cli.build_parser().parse_args(["serve", "--db-path", str(tmp_path / "t.db")])
    with pytest.raises(ValueError, match="SIGNAL_WEBHOOK_SECRET"):
        cli._build_serve_runtime(args)


def test_build_serve_runtime_allows_no_auth_in_paper_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CcxtBroker", lambda *a, **k: MagicMock(spec=CcxtBroker))
    _no_webhook_secret(monkeypatch)

    args = cli.build_parser().parse_args(
        ["serve", "--no-auth", "--db-path", str(tmp_path / "t.db")]
    )
    runtime = cli._build_serve_runtime(args)

    assert isinstance(runtime.broker, PaperBroker)


def test_build_serve_runtime_rejects_no_auth_in_live_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CcxtBroker", lambda *a, **k: MagicMock(spec=CcxtBroker))
    monkeypatch.setattr(cli, "load_settings", _fake_settings)
    _no_webhook_secret(monkeypatch)

    args = cli.build_parser().parse_args(
        ["serve", "--live", "--no-auth", "--db-path", str(tmp_path / "t.db")]
    )
    with pytest.raises(ValueError, match="--no-auth"):
        cli._build_serve_runtime(args)


def test_build_serve_runtime_wires_secret_into_webhook(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CcxtBroker", lambda *a, **k: MagicMock(spec=CcxtBroker))
    args = cli.build_parser().parse_args(["serve", "--db-path", str(tmp_path / "t.db")])
    runtime = cli._build_serve_runtime(args)

    async def scenario():
        signal = {"source": "tv", "symbol": "BTC/USDT", "action": "BUY"}
        async with TestClient(TestServer(runtime.app)) as client:
            denied = await client.post("/signals", json=signal)
            allowed = await client.post(
                "/signals", json=signal, headers={"X-Signal-Secret": _WEBHOOK_SECRET}
            )
            return denied.status, allowed.status

    assert asyncio.run(scenario()) == (401, 202)
