from __future__ import annotations

import pytest
from pydantic import ValidationError

from trading_bot.config import Settings, load_public_data_settings


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
