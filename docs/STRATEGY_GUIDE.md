# Strategy Development Guide

How to create trading strategies for Krader.

## Overview

Strategies in Krader are **signal generators**. They:
- Receive market data (candles, indicators)
- Analyze conditions
- Return `Signal` objects (BUY/SELL/HOLD)

Strategies do **NOT**:
- Place orders directly
- Access broker APIs
- Manage positions

The OMS and risk validator handle all order logic.

---

## Strategy Interface

```python
from abc import ABC, abstractmethod
from krader.strategy.base import BaseStrategy, MarketSnapshot, StrategyContext
from krader.strategy.signal import Signal


class MyStrategy(BaseStrategy):

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier."""
        pass

    @property
    @abstractmethod
    def symbols(self) -> list[str]:
        """
        Symbols to trade.
        Return [] for dynamic universe (uses context.metadata["universe_top20"]).
        """
        pass

    @abstractmethod
    async def on_market_data(
        self,
        snapshot: MarketSnapshot,
        context: StrategyContext,
    ) -> list[Signal]:
        """
        Called on every candle close.
        Returns list of signals (can be empty).
        """
        pass

    async def on_start(self) -> None:
        """Called when strategy starts. Override for initialization."""
        pass

    async def on_stop(self) -> None:
        """Called when strategy stops. Override for cleanup."""
        pass

    async def on_fill(self, symbol: str, side: str, quantity: int, price: Decimal) -> None:
        """Called when an order fills. Override to track fills."""
        pass
```

---

## Input Data

### MarketSnapshot

```python
@dataclass
class MarketSnapshot:
    symbol: str                              # e.g., "005930"
    timestamp: datetime                      # Current time
    last_tick: Tick | None                   # Most recent tick
    current_candles: dict[str, Candle]       # In-progress candles by timeframe
    historical_candles: dict[str, list[dict]] # Historical candles by timeframe

    @property
    def last_price(self) -> Decimal | None:  # Convenience property
```

**Historical Candles Structure:**
```python
snapshot.historical_candles = {
    "1m": [
        {"open": 50000, "high": 50100, "low": 49900, "close": 50050, "volume": 1000, "open_time": 1234567890},
        ...  # Up to 250 candles, newest last
    ],
    "5m": [...],
    "15m": [...],
    "60m": [...],
}
```

### StrategyContext

```python
@dataclass
class StrategyContext:
    portfolio: Portfolio           # Current positions and cash
    active_orders_count: int       # Orders in flight
    daily_trades_count: int        # Trades today
    last_signal_time: datetime | None
    is_market_open: bool           # Trading hours check
    metadata: dict                 # Contains "universe_top20"
```

**Accessing Universe:**
```python
universe = context.metadata.get("universe_top20", [])
if snapshot.symbol not in universe:
    return []  # Skip symbols not in top 20
```

---

## Output: Signal

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Any

@dataclass(frozen=True)
class Signal:
    signal_id: str                           # Unique ID (use uuid4)
    strategy_name: str                       # Your strategy name
    symbol: str                              # Stock code
    action: Literal["BUY", "SELL", "HOLD"]   # Action
    confidence: float                        # 0.0 to 1.0
    reason: str                              # Human-readable reason
    suggested_quantity: int | None           # Optional quantity hint
    metadata: dict[str, Any]                 # Additional data
    timestamp: datetime                      # Signal time
```

**Signal Guidelines:**
- Use `uuid4()` for `signal_id`
- `confidence` affects logging/analysis (not position sizing currently)
- `suggested_quantity` is validated by risk rules
- Include relevant indicators in `metadata`

---

## Basic Strategy Template

```python
"""Simple moving average crossover strategy."""

from datetime import datetime
from uuid import uuid4

from krader.strategy.base import BaseStrategy, MarketSnapshot, StrategyContext
from krader.strategy.signal import Signal


class MACrossStrategy(BaseStrategy):

    def __init__(self, fast_period: int = 10, slow_period: int = 20):
        self._fast_period = fast_period
        self._slow_period = slow_period

    @property
    def name(self) -> str:
        return "ma_cross_v1"

    @property
    def symbols(self) -> list[str]:
        # Empty = use dynamic universe
        return []

    async def on_market_data(
        self,
        snapshot: MarketSnapshot,
        context: StrategyContext,
    ) -> list[Signal]:
        # 1. Check universe
        universe = context.metadata.get("universe_top20", [])
        if snapshot.symbol not in universe:
            return []

        # 2. Check market hours
        if not context.is_market_open:
            return []

        # 3. Get historical candles
        candles = snapshot.historical_candles.get("5m", [])
        if len(candles) < self._slow_period:
            return []  # Not enough data

        # 4. Calculate indicators
        closes = [c["close"] for c in candles]
        fast_ma = sum(closes[-self._fast_period:]) / self._fast_period
        slow_ma = sum(closes[-self._slow_period:]) / self._slow_period

        prev_closes = closes[:-1]
        prev_fast_ma = sum(prev_closes[-self._fast_period:]) / self._fast_period
        prev_slow_ma = sum(prev_closes[-self._slow_period:]) / self._slow_period

        # 5. Generate signal
        metadata = {
            "fast_ma": round(fast_ma, 2),
            "slow_ma": round(slow_ma, 2),
            "price": closes[-1],
        }

        # Bullish crossover
        if prev_fast_ma <= prev_slow_ma and fast_ma > slow_ma:
            return [self._make_signal(snapshot, "BUY", 0.7, "ma_cross_up", metadata)]

        # Bearish crossover
        if prev_fast_ma >= prev_slow_ma and fast_ma < slow_ma:
            return [self._make_signal(snapshot, "SELL", 0.7, "ma_cross_down", metadata)]

        return [self._make_signal(snapshot, "HOLD", 0.0, "no_cross", metadata)]

    def _make_signal(
        self,
        snapshot: MarketSnapshot,
        action: str,
        confidence: float,
        reason: str,
        metadata: dict,
    ) -> Signal:
        return Signal(
            signal_id=str(uuid4()),
            strategy_name=self.name,
            symbol=snapshot.symbol,
            action=action,
            confidence=confidence,
            reason=reason,
            suggested_quantity=10 if action in ("BUY", "SELL") else None,
            metadata=metadata,
            timestamp=snapshot.timestamp,
        )
```

---

## Implementing Indicators

Krader strategies should implement indicators locally (no TA libraries required).

### EMA (Exponential Moving Average)

```python
def ema(values: list[float], period: int) -> list[float]:
    """Calculate EMA. Returns list of same length."""
    if not values or period <= 0:
        return []

    result = []
    multiplier = 2.0 / (period + 1)
    ema_val = 0.0

    for i, val in enumerate(values):
        if i < period - 1:
            result.append(0.0)  # Not enough data
        elif i == period - 1:
            ema_val = sum(values[:period]) / period  # SMA for first value
            result.append(ema_val)
        else:
            ema_val = (val - ema_val) * multiplier + ema_val
            result.append(ema_val)

    return result
```

### RSI (Relative Strength Index)

```python
def rsi(values: list[float], period: int = 14) -> list[float]:
    """Calculate RSI using Wilder's smoothing."""
    if len(values) < period + 1:
        return [50.0] * len(values)

    result = [50.0] * len(values)

    # Calculate price changes
    deltas = [0.0]
    for i in range(1, len(values)):
        deltas.append(values[i] - values[i - 1])

    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [abs(d) if d < 0 else 0.0 for d in deltas]

    # First RSI
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    # Subsequent RSI values
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - (100.0 / (1.0 + rs))

    return result
```

### Swing High/Low

```python
def swing_high(highs: list[float], lookback: int = 10) -> float:
    """Find highest high in lookback period (excluding current)."""
    if len(highs) < lookback + 1:
        return highs[-2] if len(highs) >= 2 else 0.0
    return max(highs[-(lookback + 1):-1])

def swing_low(lows: list[float], lookback: int = 10) -> float:
    """Find lowest low in lookback period (excluding current)."""
    if len(lows) < lookback + 1:
        return lows[-2] if len(lows) >= 2 else float('inf')
    return min(lows[-(lookback + 1):-1])
```

---

## Multi-Timeframe Analysis

Use different timeframes for trend vs. entry:

```python
async def on_market_data(self, snapshot: MarketSnapshot, context: StrategyContext) -> list[Signal]:
    # Higher timeframe for trend
    htf_candles = snapshot.historical_candles.get("60m", [])
    if len(htf_candles) < 200:
        return []

    htf_closes = [c["close"] for c in htf_candles]
    htf_ema50 = ema(htf_closes, 50)[-1]
    htf_ema200 = ema(htf_closes, 200)[-1]

    # Trend filter
    uptrend = htf_ema50 > htf_ema200

    if not uptrend:
        return [self._hold("downtrend")]

    # Lower timeframe for entry
    ltf_candles = snapshot.historical_candles.get("5m", [])
    if len(ltf_candles) < 20:
        return []

    ltf_closes = [c["close"] for c in ltf_candles]
    ltf_rsi = rsi(ltf_closes, 14)

    # Entry trigger on LTF
    if ltf_rsi[-2] < 30 and ltf_rsi[-1] >= 30:
        return [self._buy("rsi_oversold_bounce")]

    return [self._hold("waiting")]
```

---

## Cooldown Management

Prevent signal spam:

```python
class MyStrategy(BaseStrategy):

    def __init__(self, cooldown_minutes: int = 30):
        self._cooldown_minutes = cooldown_minutes
        self._last_signal_time: dict[str, datetime] = {}

    async def on_market_data(self, snapshot: MarketSnapshot, context: StrategyContext) -> list[Signal]:
        symbol = snapshot.symbol

        # Check cooldown
        if symbol in self._last_signal_time:
            elapsed = (snapshot.timestamp - self._last_signal_time[symbol]).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return []  # Still in cooldown

        # ... strategy logic ...

        if should_buy:
            self._last_signal_time[symbol] = snapshot.timestamp
            return [self._make_signal(...)]
```

---

## Error Handling

Always degrade gracefully:

```python
async def on_market_data(self, snapshot: MarketSnapshot, context: StrategyContext) -> list[Signal]:
    try:
        # Get data
        candles = snapshot.historical_candles.get("60m", [])

        # Validate data
        if not candles:
            return []

        if len(candles) < self._min_candles:
            return [self._hold("insufficient_data", {"candles": len(candles)})]

        # Extract prices safely
        closes = []
        for c in candles:
            close = c.get("close")
            if close is not None:
                closes.append(float(close))

        if len(closes) < self._min_candles:
            return []

        # ... rest of logic ...

    except Exception as e:
        # Log but don't crash
        return [self._hold("error", {"error": str(e)})]
```

---

## Registering Your Strategy

### In main.py

```python
from krader.app import Application
from krader.config import load_settings
from krader.strategy import PullbackV1
from my_strategy import MACrossStrategy

async def main():
    settings = load_settings()
    app = Application(settings)

    # Add multiple strategies
    app.add_strategy(PullbackV1())
    app.add_strategy(MACrossStrategy(fast_period=5, slow_period=20))

    await app.run()
```

### In strategy/__init__.py (optional)

```python
from krader.strategy.base import BaseStrategy, MarketSnapshot, StrategyContext
from krader.strategy.signal import Signal
from krader.strategy.pullback_v1 import PullbackV1
from krader.strategy.ma_cross import MACrossStrategy  # Add your strategy

__all__ = [
    "BaseStrategy",
    "MarketSnapshot",
    "StrategyContext",
    "Signal",
    "PullbackV1",
    "MACrossStrategy",
]
```

---

## Testing Your Strategy

### Create Test File

```python
# tests/test_my_strategy.py

import asyncio
from datetime import datetime
from decimal import Decimal

from krader.strategy.base import MarketSnapshot, StrategyContext
from krader.risk.portfolio import Portfolio
from my_strategy import MACrossStrategy


def create_test_candles(prices: list[float]) -> list[dict]:
    """Create candles from price list."""
    candles = []
    base_time = datetime.now()
    for i, price in enumerate(prices):
        candles.append({
            "open": price * 0.999,
            "high": price * 1.001,
            "low": price * 0.998,
            "close": price,
            "volume": 10000,
            "open_time": int(base_time.timestamp()) + i * 300,
        })
    return candles


async def test_bullish_crossover():
    strategy = MACrossStrategy(fast_period=3, slow_period=5)

    # Prices that create bullish crossover
    prices = [100, 99, 98, 97, 96, 97, 98, 99, 100, 101, 102]

    snapshot = MarketSnapshot(
        symbol="005930",
        timestamp=datetime.now(),
        historical_candles={"5m": create_test_candles(prices)},
    )

    context = StrategyContext(
        portfolio=Portfolio(cash=Decimal("10000000"), total_equity=Decimal("10000000")),
        active_orders_count=0,
        daily_trades_count=0,
        is_market_open=True,
        metadata={"universe_top20": ["005930"]},
    )

    signals = await strategy.on_market_data(snapshot, context)

    print(f"Signals: {signals}")
    if signals and signals[0].action == "BUY":
        print("✅ PASS: BUY signal generated")
    else:
        print("❌ FAIL: Expected BUY signal")


if __name__ == "__main__":
    asyncio.run(test_bullish_crossover())
```

### Run Test

```bash
PYTHONPATH=. python3 tests/test_my_strategy.py
```

---

## Best Practices

### 1. Use Dynamic Universe

```python
@property
def symbols(self) -> list[str]:
    return []  # Let app provide universe

async def on_market_data(self, snapshot, context):
    universe = context.metadata.get("universe_top20", [])
    if snapshot.symbol not in universe:
        return []
```

### 2. Always Return Something

```python
# Bad - returns None
if not condition:
    return

# Good - returns empty list
if not condition:
    return []
```

### 3. Include Metadata

```python
Signal(
    ...
    metadata={
        "ema_fast": round(ema_fast, 2),
        "ema_slow": round(ema_slow, 2),
        "rsi": round(rsi_value, 2),
        "trigger": "crossover",
    }
)
```

### 4. Validate Data

```python
closes = []
for c in candles:
    close = c.get("close")
    if close is not None:
        try:
            closes.append(float(close))
        except (ValueError, TypeError):
            continue

if len(closes) < required:
    return []
```

### 5. Use Cooldowns

Prevent overtrading:

```python
if self._is_in_cooldown(symbol):
    return []
```

### 6. Log Decisions

Include reason in every signal:

```python
return [Signal(
    ...
    reason="ema_cross_bullish",  # Clear, searchable
)]
```

---

## Reference: PullbackV1 Strategy

See `krader/strategy/pullback_v1.py` for a complete, production-ready example:

- Multi-timeframe analysis (60m + 5m)
- Trend filter (EMA50 > EMA200)
- Pullback detection (price in EMA zone)
- Entry trigger (RSI cross + swing break)
- Exit trigger (RSI cross down + below EMA)
- Cooldown management
- Comprehensive metadata
