from __future__ import annotations

import pytest
from pydantic import ValidationError

from trading_bot.config import Settings, WebhookSettings, load_public_data_settings


def test_settings_accepts_valid_testnet_configuration():
    # Arrange / Act
    settings = Settings(
        _env_file=None,
        binance_api_key="key",
        binance_api_secret="secret",
    )

    # Assert
    assert settings.use_testnet is True
    assert settings.i_understand_live_trading_risk is False


def test_settings_raises_when_credentials_are_missing():
    # Act / Assert
    with pytest.raises(ValidationError, match="BINANCE_API_KEY"):
        Settings(_env_file=None, binance_api_key="", binance_api_secret="")


def test_settings_raises_when_going_live_without_explicit_risk_ack():
    # Act / Assert
    with pytest.raises(ValidationError, match="I_UNDERSTAND_LIVE_TRADING_RISK"):
        Settings(
            _env_file=None,
            binance_api_key="key",
            binance_api_secret="secret",
            use_testnet=False,
            i_understand_live_trading_risk=False,
        )


def test_settings_allows_going_live_when_explicitly_acknowledged():
    # Act
    settings = Settings(
        _env_file=None,
        binance_api_key="key",
        binance_api_secret="secret",
        use_testnet=False,
        i_understand_live_trading_risk=True,
    )

    # Assert
    assert settings.use_testnet is False


def test_load_public_data_settings_bypasses_credential_validation():
    # Act
    settings = load_public_data_settings()

    # Assert
    assert settings.binance_api_key == ""
    assert settings.binance_api_secret == ""
    assert settings.use_testnet is True


def test_load_public_data_settings_can_target_mainnet_for_realistic_backtests():
    # Act
    settings = load_public_data_settings(use_testnet=False)

    # Assert
    assert settings.use_testnet is False
    assert settings.binance_api_key == ""


def test_webhook_settings_default_to_no_secret(monkeypatch):
    monkeypatch.delenv("SIGNAL_WEBHOOK_SECRET", raising=False)

    settings = WebhookSettings(_env_file=None)

    assert settings.signal_webhook_secret is None


def test_webhook_settings_read_secret_from_environment(monkeypatch):
    monkeypatch.setenv("SIGNAL_WEBHOOK_SECRET", "a" * 32)

    settings = WebhookSettings(_env_file=None)

    assert settings.signal_webhook_secret is not None
    assert settings.signal_webhook_secret.get_secret_value() == "a" * 32


def test_webhook_settings_reject_short_secret():
    with pytest.raises(ValidationError, match="at least 16"):
        WebhookSettings(_env_file=None, signal_webhook_secret="short")


def test_webhook_settings_do_not_leak_secret_in_repr():
    settings = WebhookSettings(_env_file=None, signal_webhook_secret="b" * 32)

    assert "b" * 32 not in repr(settings)
