from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from trading_bot import cli
from trading_bot.config import IrlSettings, WebhookSettings
from trading_bot.execution.broker import CcxtBroker
from trading_bot.irl.client import IrlClient, IrlUnavailable
from trading_bot.irl.gate import IrlGate, PassthroughGate

_AGENT_ID = "00000000-0000-0000-0000-0000000000aa"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setattr(
        cli,
        "load_webhook_settings",
        lambda: WebhookSettings.model_construct(
            signal_webhook_secret=SecretStr("cli-test-secret-0123456789abcdef")
        ),
    )
    monkeypatch.setattr(cli, "CcxtBroker", lambda *a, **k: MagicMock(spec=CcxtBroker))


def _irl_settings(monkeypatch, *, base_url="http://irl-engine:4000", token="tok", agent=_AGENT_ID):
    monkeypatch.setattr(
        cli,
        "load_irl_settings",
        lambda: IrlSettings.model_construct(
            irl_base_url=base_url,
            irl_api_token=SecretStr(token) if token else None,
            irl_agent_id=agent,
        ),
    )


def _parse(*argv: str):
    return cli.build_parser().parse_args(list(argv))


def test_irl_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("IRL_BASE_URL", "http://irl-engine:4000")
    monkeypatch.setenv("IRL_API_TOKEN", "secret-token")
    monkeypatch.setenv("IRL_AGENT_ID", _AGENT_ID)

    settings = IrlSettings(_env_file=None)

    assert settings.irl_base_url == "http://irl-engine:4000"
    assert settings.irl_api_token is not None
    assert settings.irl_api_token.get_secret_value() == "secret-token"
    assert "secret-token" not in repr(settings)
    assert settings.irl_agent_id == _AGENT_ID


def test_serve_without_irl_flag_uses_passthrough_gate(tmp_path):
    runtime = cli._build_serve_runtime(_parse("serve", "--db-path", str(tmp_path / "t.db")))

    assert isinstance(runtime.gate, PassthroughGate)
    assert runtime.irl_client is None


def test_serve_with_irl_builds_irl_gate_with_paper_venue(tmp_path, monkeypatch):
    _irl_settings(monkeypatch)

    runtime = cli._build_serve_runtime(
        _parse("serve", "--irl", "--db-path", str(tmp_path / "t.db"))
    )

    assert isinstance(runtime.gate, IrlGate)
    assert isinstance(runtime.irl_client, IrlClient)
    assert runtime.gate._venue_id == "BINANCE-PAPER"
    assert runtime.gate._notional_currency == "USDT"
    assert runtime.gate._identity.agent_id == _AGENT_ID


@pytest.mark.parametrize(
    "missing", [{"base_url": ""}, {"token": ""}, {"agent": ""}], ids=["url", "token", "agent"]
)
def test_serve_with_irl_refuses_to_start_when_settings_missing(tmp_path, monkeypatch, missing):
    _irl_settings(monkeypatch, **missing)

    with pytest.raises(ValueError, match="IRL_"):
        cli._build_serve_runtime(_parse("serve", "--irl", "--db-path", str(tmp_path / "t.db")))


def test_agent_identity_is_deterministic_and_tracks_risk_parameters():
    base = cli._agent_identity(_parse("serve"), _AGENT_ID)
    same = cli._agent_identity(_parse("serve"), _AGENT_ID)
    changed = cli._agent_identity(_parse("serve", "--position-size-fraction", "0.2"), _AGENT_ID)

    assert base == same
    assert len(base.model_hash_hex) == 64
    assert changed.hyperparameter_checksum != base.hyperparameter_checksum
    assert changed.model_hash_hex != base.model_hash_hex


def test_irl_register_uses_the_same_model_hash_as_serve(monkeypatch, capsys):
    _irl_settings(monkeypatch, agent="")
    client = AsyncMock(spec=IrlClient)
    client.register_agent.return_value = "new-agent-id"
    monkeypatch.setattr(cli, "IrlClient", lambda *a, **k: client)

    exit_code = cli.cmd_irl_register(
        _parse("irl-register", "--max-notional", "500", "--position-size-fraction", "0.2")
    )

    expected = cli._agent_identity(_parse("serve", "--position-size-fraction", "0.2"), "")
    kwargs = client.register_agent.await_args.kwargs
    assert exit_code == 0
    assert kwargs["model_hash_hex"] == expected.model_hash_hex
    assert kwargs["max_notional"] == 500.0
    assert "IRL_AGENT_ID=new-agent-id" in capsys.readouterr().out
    client.close.assert_awaited()


def test_irl_register_returns_one_when_irl_unreachable(monkeypatch, caplog):
    _irl_settings(monkeypatch, agent="")
    client = AsyncMock(spec=IrlClient)
    client.register_agent.side_effect = IrlUnavailable(0, "UNREACHABLE", "connection refused")
    monkeypatch.setattr(cli, "IrlClient", lambda *a, **k: client)

    with caplog.at_level("ERROR"):
        exit_code = cli.cmd_irl_register(_parse("irl-register", "--max-notional", "500"))

    assert exit_code == 1
    assert "connection refused" in caplog.text


def test_irl_register_requires_url_and_token(monkeypatch, caplog):
    _irl_settings(monkeypatch, token="", agent="")

    with caplog.at_level("ERROR"):
        exit_code = cli.cmd_irl_register(_parse("irl-register", "--max-notional", "500"))

    assert exit_code == 2
    assert "IRL_API_TOKEN" in caplog.text


def test_serve_with_irl_attaches_heartbeat_source_when_configured(tmp_path, monkeypatch):
    from trading_bot.irl.heartbeat import MacroPulseHeartbeatSource

    monkeypatch.setattr(
        cli,
        "load_irl_settings",
        lambda: IrlSettings.model_construct(
            irl_base_url="http://irl-engine:4000",
            irl_api_token=SecretStr("tok"),
            irl_agent_id=_AGENT_ID,
            irl_heartbeat_url="http://api:8000/v1/irl/heartbeat",
            macropulse_api_key=SecretStr("mp_key"),
        ),
    )

    runtime = cli._build_serve_runtime(
        _parse("serve", "--irl", "--db-path", str(tmp_path / "t.db"))
    )

    assert isinstance(runtime.gate._heartbeat_source, MacroPulseHeartbeatSource)
    assert runtime.heartbeat_source is runtime.gate._heartbeat_source


def test_serve_with_irl_requires_api_key_when_heartbeat_url_set(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli,
        "load_irl_settings",
        lambda: IrlSettings.model_construct(
            irl_base_url="http://irl-engine:4000",
            irl_api_token=SecretStr("tok"),
            irl_agent_id=_AGENT_ID,
            irl_heartbeat_url="http://api:8000/v1/irl/heartbeat",
            macropulse_api_key=None,
        ),
    )

    with pytest.raises(ValueError, match="MACROPULSE_API_KEY"):
        cli._build_serve_runtime(_parse("serve", "--irl", "--db-path", str(tmp_path / "t.db")))


def _canary_client(monkeypatch, *, authorize_error=None):
    from trading_bot.irl.client import AuthorizeResult, BindResult

    client = AsyncMock(spec=IrlClient)
    if authorize_error is not None:
        client.authorize.side_effect = authorize_error
    else:
        client.authorize.return_value = AuthorizeResult(
            trace_id="t-1", reasoning_hash="r", authorized=True, shadow_blocked=False
        )
    client.bind.return_value = BindResult(
        trace_id="t-1", final_proof="p", verification_status="Matched", divergence_reason=None
    )
    monkeypatch.setattr(cli, "IrlClient", lambda *a, **k: client)
    return client


def test_irl_canary_authorizes_then_binds_rejected_without_trading(monkeypatch, capsys):
    _irl_settings(monkeypatch)
    client = _canary_client(monkeypatch)

    exit_code = cli.cmd_irl_canary(_parse("irl-canary"))

    auth = client.authorize.await_args.kwargs
    bind = client.bind.await_args
    assert exit_code == 0
    assert auth["client_order_id"].startswith("canary-")
    assert auth["notional"] <= 1.0
    assert bind.args[0] == "t-1"
    assert bind.kwargs["execution_status"] == "Rejected"
    assert bind.kwargs["exchange_tx_id"] == auth["client_order_id"]
    assert "Matched" in capsys.readouterr().out
    client.close.assert_awaited()


def test_irl_canary_uses_the_same_identity_as_serve(monkeypatch):
    _irl_settings(monkeypatch)
    client = _canary_client(monkeypatch)

    cli.cmd_irl_canary(_parse("irl-canary"))

    identity = client.authorize.await_args.args[0]
    assert identity == cli._agent_identity(_parse("serve"), _AGENT_ID)


def test_irl_canary_regime_policy_denial_still_counts_as_healthy(monkeypatch):
    from trading_bot.irl.client import IrlDenied

    _irl_settings(monkeypatch)
    _canary_client(monkeypatch, authorize_error=IrlDenied(403, "REGIME_VIOLATION", "risk_off"))

    assert cli.cmd_irl_canary(_parse("irl-canary")) == 0


@pytest.mark.parametrize(
    "error",
    [
        IrlUnavailable(500, "DATABASE_ERROR", "Internal storage error"),
        __import__("trading_bot.irl.client", fromlist=["IrlDenied"]).IrlDenied(
            403, "MODEL_HASH_MISMATCH", "hash"
        ),
    ],
    ids=["db-error", "model-hash"],
)
def test_irl_canary_fails_on_infrastructure_or_config_errors(monkeypatch, caplog, error):
    _irl_settings(monkeypatch)
    _canary_client(monkeypatch, authorize_error=error)

    with caplog.at_level("ERROR"):
        exit_code = cli.cmd_irl_canary(_parse("irl-canary"))

    assert exit_code == 1
    assert error.code in caplog.text


def test_irl_canary_requires_irl_settings(monkeypatch, caplog):
    _irl_settings(monkeypatch, token="")

    with caplog.at_level("ERROR"):
        assert cli.cmd_irl_canary(_parse("irl-canary")) == 2
