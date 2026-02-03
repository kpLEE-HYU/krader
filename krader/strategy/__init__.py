"""Strategy interface for pluggable trading strategies."""

from krader.strategy.base import BaseStrategy, MarketSnapshot, StrategyContext
from krader.strategy.signal import Signal
from krader.strategy.pullback_v1 import PullbackV1
from krader.strategy.registry import (
    create_strategy,
    get_available_strategies,
    register_strategy,
)

__all__ = [
    "BaseStrategy",
    "MarketSnapshot",
    "StrategyContext",
    "Signal",
    "PullbackV1",
    "create_strategy",
    "get_available_strategies",
    "register_strategy",
]
