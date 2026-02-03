"""Market data types: Tick and Candle."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Tick:
    """A single price tick."""

    symbol: str
    price: Decimal
    volume: int
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("Price must be positive")
        if self.volume < 0:
            raise ValueError("Volume cannot be negative")


@dataclass
class Candle:
    """OHLCV candlestick data."""

    symbol: str
    timeframe: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @property
    def is_bullish(self) -> bool:
        """Check if candle closed higher than it opened."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Check if candle closed lower than it opened."""
        return self.close < self.open

    @property
    def body_size(self) -> Decimal:
        """Get the absolute size of the candle body."""
        return abs(self.close - self.open)

    @property
    def total_range(self) -> Decimal:
        """Get the total high-low range."""
        return self.high - self.low

    def update_with_tick(self, tick: Tick) -> None:
        """Update candle with a new tick."""
        if tick.symbol != self.symbol:
            raise ValueError(f"Tick symbol {tick.symbol} doesn't match candle {self.symbol}")

        if tick.price > self.high:
            self.high = tick.price
        if tick.price < self.low:
            self.low = tick.price

        self.close = tick.price
        self.volume += tick.volume

    @classmethod
    def from_tick(cls, tick: Tick, timeframe: str, open_time: datetime) -> "Candle":
        """Create a new candle from a tick."""
        return cls(
            symbol=tick.symbol,
            timeframe=timeframe,
            open_time=open_time,
            open=tick.price,
            high=tick.price,
            low=tick.price,
            close=tick.price,
            volume=tick.volume,
        )
