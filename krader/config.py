"""Configuration management using pydantic settings."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class DatabaseConfig(BaseModel):
    """Database configuration."""

    path: Path = Field(default=Path("krader.db"))


class BrokerConfig(BaseModel):
    """Broker configuration."""

    type: Literal["kiwoom", "mock"] = Field(default="kiwoom")
    account_number: str = Field(default="")
    tr_rate_limit_ms: int = Field(default=200)


class RiskConfig(BaseModel):
    """Risk management configuration."""

    max_position_size: int = Field(default=1000)
    max_portfolio_exposure_pct: float = Field(default=0.8)
    daily_loss_limit: float = Field(default=1_000_000)
    trading_start_hour: int = Field(default=9)
    trading_start_minute: int = Field(default=0)
    trading_end_hour: int = Field(default=15)
    trading_end_minute: int = Field(default=30)

    # Transaction cost as percentage of trade notional (0.001 = 0.1%)
    transaction_cost_rate: float = Field(
        default=0.00015,  # 0.015% (typical Korean stock commission)
        ge=0.0,
        le=0.02,
        description="Transaction cost rate (0.00015 = 0.015%)",
    )

    # Maximum trades per day (1-1000)
    max_trades_per_day: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="Maximum number of trades per day",
    )

    # Position size as percentage of total equity per trade (0.05 = 5%)
    # When strategy returns suggested_quantity=None, this is used to calculate quantity
    position_size_pct: float = Field(
        default=0.05,  # 5% of equity per trade
        ge=0.01,
        le=0.5,
        description="Position size as % of equity per trade (0.05 = 5%)",
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    log_dir: Path = Field(default=Path("logs"))
    json_format: bool = Field(default=True)


class Settings(BaseSettings):
    """Application settings."""

    mode: Literal["live", "paper", "test"] = Field(default="paper")
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # Strategy selection - must match a registered strategy name
    strategy: str = Field(
        default="pullback_v1",
        min_length=1,
        description="Active strategy name (must be registered in strategy registry)",
    )

    model_config = {
        "env_prefix": "KRADER_",
        "env_nested_delimiter": "__",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


def load_settings() -> Settings:
    """Load settings from environment variables."""
    return Settings()
