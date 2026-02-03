"""Abstract strategy interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from krader.market.types import Candle, Tick
    from krader.risk.portfolio import Portfolio
    from krader.strategy.signal import Signal


@dataclass
class MarketSnapshot:
    """Current market state for a symbol."""

    symbol: str
    timestamp: datetime
    last_tick: "Tick | None" = None
    current_candles: dict[str, "Candle"] = field(default_factory=dict)
    historical_candles: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def last_price(self) -> Decimal | None:
        """Get the most recent price."""
        if self.last_tick:
            return self.last_tick.price
        for candle in self.current_candles.values():
            return candle.close
        return None


@dataclass
class StrategyContext:
    """Context provided to strategies for decision making."""

    portfolio: "Portfolio"
    active_orders_count: int
    daily_trades_count: int
    last_signal_time: datetime | None = None
    is_market_open: bool = True
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    Strategies receive market data and portfolio context, and generate
    trading signals. Strategies do NOT place orders directly - they
    return Signal objects that are validated and executed by the OMS.

    To implement a strategy:
    1. Inherit from BaseStrategy
    2. Implement the `name` and `symbols` properties
    3. Implement `on_market_data` to generate signals

    Example:
        class MyStrategy(BaseStrategy):
            @property
            def name(self) -> str:
                return "my-strategy"

            @property
            def symbols(self) -> list[str]:
                return ["005930", "000660"]

            async def on_market_data(
                self,
                snapshot: MarketSnapshot,
                context: StrategyContext,
            ) -> list[Signal]:
                # Analyze data and generate signals
                signals = []
                # ... strategy logic ...
                return signals
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique identifier for this strategy.

        Returns:
            Strategy name string (e.g., "momentum-v1", "mean-reversion")
        """
        pass

    @property
    @abstractmethod
    def symbols(self) -> list[str]:
        """
        List of symbols this strategy trades.

        Returns:
            List of symbol codes (e.g., ["005930", "000660"])
        """
        pass

    @abstractmethod
    async def on_market_data(
        self,
        snapshot: MarketSnapshot,
        context: StrategyContext,
    ) -> list["Signal"]:
        """
        Generate trading signals from market data.

        This method is called whenever relevant market data updates occur.
        The strategy should analyze the data and return zero or more signals.

        IMPORTANT:
        - Do NOT place orders directly - return Signal objects
        - Signals are validated against risk rules before execution
        - Return an empty list if no action is needed
        - Return HOLD signals only for explicit no-action decisions

        Args:
            snapshot: Current market state for the symbol
            context: Portfolio and system context

        Returns:
            List of Signal objects (can be empty)
        """
        pass

    async def on_start(self) -> None:
        """
        Called when the strategy is started.

        Override to perform initialization (load models, etc.)
        """
        pass

    async def on_stop(self) -> None:
        """
        Called when the strategy is stopped.

        Override to perform cleanup.
        """
        pass

    async def on_fill(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: Decimal,
    ) -> None:
        """
        Called when an order fill occurs for a symbol we trade.

        Override to track fills and update internal state.

        Args:
            symbol: The filled symbol
            side: "BUY" or "SELL"
            quantity: Filled quantity
            price: Fill price
        """
        pass
