"""Typed, validated settings loaded from environment / .env.

Testnet is the hard default: going live requires two separate opt-in flags
(USE_TESTNET=false AND I_UNDERSTAND_LIVE_TRADING_RISK=true) so a single
misconfigured variable can't accidentally route orders to mainnet.
"""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    binance_api_key: str = Field(default="")
    binance_api_secret: str = Field(default="")

    use_testnet: bool = Field(default=True)
    i_understand_live_trading_risk: bool = Field(default=False)

    max_daily_loss_pct: float = Field(default=3.0, gt=0)
    max_drawdown_pct: float = Field(default=10.0, gt=0)
    position_size_fraction: float = Field(default=0.1, gt=0, le=1.0)

    db_path: str = Field(default="trading_bot.db")

    @model_validator(mode="after")
    def _validate_live_trading_gate(self) -> Settings:
        if not self.use_testnet and not self.i_understand_live_trading_risk:
            raise ValueError(
                "Refusing to start: USE_TESTNET=false requires "
                "I_UNDERSTAND_LIVE_TRADING_RISK=true to also be set explicitly."
            )
        return self

    @model_validator(mode="after")
    def _validate_credentials_present(self) -> Settings:
        if not self.binance_api_key or not self.binance_api_secret:
            raise ValueError(
                "BINANCE_API_KEY and BINANCE_API_SECRET must be set (in .env or the "
                "environment) before starting the bot. Public-data-only commands "
                "like `backtest` do not require this."
            )
        return self


def load_settings() -> Settings:
    return Settings()


def load_public_data_settings() -> Settings:
    """Relaxed settings loader for commands that only need public market data (e.g. backtest)."""
    return Settings.model_construct(
        binance_api_key="",
        binance_api_secret="",
        use_testnet=True,
        i_understand_live_trading_risk=False,
        max_daily_loss_pct=3.0,
        max_drawdown_pct=10.0,
        position_size_fraction=0.1,
        db_path="trading_bot.db",
    )
