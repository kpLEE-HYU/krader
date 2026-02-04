"""
Mock market data generator matching Kiwoom API format.

Generates realistic tick and candle data for testing strategies,
order execution, and the full trading pipeline.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Iterator


class ScenarioType(Enum):
    """Pre-defined market scenarios for testing."""

    STRONG_UPTREND = "strong_uptrend"
    STRONG_DOWNTREND = "strong_downtrend"
    SIDEWAYS = "sideways"
    PULLBACK_BUY = "pullback_buy"  # Ideal entry for PullbackV1
    PULLBACK_EXIT = "pullback_exit"  # Exit trigger for PullbackV1
    VOLATILE = "volatile"
    GAP_UP = "gap_up"
    GAP_DOWN = "gap_down"
    FLASH_CRASH = "flash_crash"
    MORNING_AUCTION = "morning_auction"


@dataclass
class TickData:
    """Kiwoom API tick data format."""

    symbol: str
    price: int  # Kiwoom returns int prices
    volume: int
    timestamp: datetime
    change: int = 0  # 전일대비 (FID 11)
    change_rate: float = 0.0  # 등락율 (FID 12)
    bid_price: int = 0  # 최우선매수호가 (FID 28)
    ask_price: int = 0  # 최우선매도호가 (FID 27)

    def to_kiwoom_format(self) -> dict[str, str]:
        """Convert to Kiwoom GetCommRealData format (all strings)."""
        return {
            "10": str(self.price),  # 현재가
            "11": str(self.change),  # 전일대비
            "12": f"{self.change_rate:.2f}",  # 등락율
            "15": str(self.volume),  # 거래량
            "20": self.timestamp.strftime("%H%M%S"),  # 체결시간
            "27": str(self.ask_price),  # 매도호가
            "28": str(self.bid_price),  # 매수호가
        }


@dataclass
class CandleData:
    """Candle data matching internal format."""

    symbol: str
    timeframe: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    def to_dict(self) -> dict:
        """Convert to dict format used by strategies."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": int(self.open_time.timestamp()),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class MarketScenario:
    """Complete market scenario with ticks and candles."""

    name: str
    symbol: str
    description: str
    ticks: list[TickData] = field(default_factory=list)
    candles_1m: list[CandleData] = field(default_factory=list)
    candles_5m: list[CandleData] = field(default_factory=list)
    candles_60m: list[CandleData] = field(default_factory=list)
    expected_signals: list[str] = field(default_factory=list)  # "BUY", "SELL", "HOLD"

    def get_historical_candles(self) -> dict[str, list[dict]]:
        """Get candles in format expected by MarketSnapshot."""
        return {
            "1m": [c.to_dict() for c in self.candles_1m],
            "5m": [c.to_dict() for c in self.candles_5m],
            "60m": [c.to_dict() for c in self.candles_60m],
        }


class MarketDataGenerator:
    """Generate realistic market data for testing."""

    # Korean stock price unit rules (호가단위)
    PRICE_UNITS = [
        (2000, 1),
        (5000, 5),
        (20000, 10),
        (50000, 50),
        (200000, 100),
        (500000, 500),
        (float("inf"), 1000),
    ]

    def __init__(
        self,
        symbol: str = "005930",
        base_price: int = 70000,
        prev_close: int = 69500,
        seed: int | None = None,
    ):
        self.symbol = symbol
        self.base_price = base_price
        self.prev_close = prev_close
        self.current_price = base_price
        self.rng = random.Random(seed)

    def _round_to_tick(self, price: float) -> int:
        """Round price to valid tick size (호가단위)."""
        price = max(1, price)
        for threshold, unit in self.PRICE_UNITS:
            if price < threshold:
                return int(round(price / unit) * unit)
        return int(round(price / 1000) * 1000)

    def _generate_volume(self, volatility_mult: float = 1.0) -> int:
        """Generate realistic volume."""
        base_volume = self.rng.randint(100, 5000)
        return int(base_volume * volatility_mult)

    def generate_tick(
        self,
        timestamp: datetime,
        drift: float = 0.0,
        volatility: float = 0.001,
    ) -> TickData:
        """Generate a single tick."""
        # Price movement
        change_pct = drift + self.rng.gauss(0, volatility)
        new_price = self.current_price * (1 + change_pct)
        new_price = self._round_to_tick(new_price)

        # Bid/Ask spread (usually 1 tick)
        tick_size = self._get_tick_size(new_price)
        bid_price = new_price - tick_size
        ask_price = new_price + tick_size

        # Volume increases with price movement
        vol_mult = 1.0 + abs(change_pct) * 100
        volume = self._generate_volume(vol_mult)

        # Change from previous close
        change = new_price - self.prev_close
        change_rate = (change / self.prev_close) * 100 if self.prev_close else 0

        self.current_price = new_price

        return TickData(
            symbol=self.symbol,
            price=new_price,
            volume=volume,
            timestamp=timestamp,
            change=change,
            change_rate=change_rate,
            bid_price=bid_price,
            ask_price=ask_price,
        )

    def _get_tick_size(self, price: float) -> int:
        """Get tick size for a given price."""
        for threshold, unit in self.PRICE_UNITS:
            if price < threshold:
                return unit
        return 1000

    def generate_ticks(
        self,
        duration_minutes: int = 10,
        ticks_per_minute: int = 60,
        start_time: datetime | None = None,
        drift: float = 0.0,
        volatility: float = 0.001,
    ) -> list[TickData]:
        """Generate a sequence of ticks."""
        if start_time is None:
            start_time = datetime.now().replace(second=0, microsecond=0)

        ticks = []
        total_ticks = duration_minutes * ticks_per_minute
        interval_seconds = 60 / ticks_per_minute

        for i in range(total_ticks):
            timestamp = start_time + timedelta(seconds=i * interval_seconds)
            tick = self.generate_tick(timestamp, drift, volatility)
            ticks.append(tick)

        return ticks

    def ticks_to_candles(
        self,
        ticks: list[TickData],
        timeframe: str = "1m",
    ) -> list[CandleData]:
        """Aggregate ticks into candles."""
        if not ticks:
            return []

        tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "60m": 60}.get(timeframe, 1)

        candles = []
        current_candle = None

        for tick in ticks:
            # Calculate candle open time
            minutes = tick.timestamp.minute
            candle_minute = (minutes // tf_minutes) * tf_minutes
            candle_time = tick.timestamp.replace(minute=candle_minute, second=0, microsecond=0)

            if current_candle is None or current_candle.open_time != candle_time:
                if current_candle:
                    candles.append(current_candle)
                current_candle = CandleData(
                    symbol=tick.symbol,
                    timeframe=timeframe,
                    open_time=candle_time,
                    open=float(tick.price),
                    high=float(tick.price),
                    low=float(tick.price),
                    close=float(tick.price),
                    volume=tick.volume,
                )
            else:
                current_candle.high = max(current_candle.high, float(tick.price))
                current_candle.low = min(current_candle.low, float(tick.price))
                current_candle.close = float(tick.price)
                current_candle.volume += tick.volume

        if current_candle:
            candles.append(current_candle)

        return candles

    def generate_candles(
        self,
        count: int = 250,
        timeframe: str = "60m",
        start_time: datetime | None = None,
        drift: float = 0.0,
        volatility: float = 0.01,
    ) -> list[CandleData]:
        """Generate candles directly (without tick aggregation for efficiency)."""
        tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "60m": 60}.get(timeframe, 60)

        if start_time is None:
            start_time = datetime.now() - timedelta(minutes=count * tf_minutes)

        candles = []
        price = float(self.base_price)

        for i in range(count):
            candle_time = start_time + timedelta(minutes=i * tf_minutes)

            # Price movement
            change_pct = drift + self.rng.gauss(0, volatility)
            close_price = price * (1 + change_pct)

            # OHLC generation
            intra_vol = volatility * 0.5
            high_mult = 1 + abs(self.rng.gauss(0, intra_vol))
            low_mult = 1 - abs(self.rng.gauss(0, intra_vol))

            open_price = price
            high_price = max(open_price, close_price) * high_mult
            low_price = min(open_price, close_price) * low_mult

            volume = self.rng.randint(50000, 500000)

            candle = CandleData(
                symbol=self.symbol,
                timeframe=timeframe,
                open_time=candle_time,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )
            candles.append(candle)

            price = close_price

        self.current_price = self._round_to_tick(price)
        return candles


def create_scenario(
    scenario_type: ScenarioType,
    symbol: str = "005930",
    base_price: int = 70000,
    seed: int | None = 42,
) -> MarketScenario:
    """Create a pre-defined market scenario."""
    gen = MarketDataGenerator(symbol=symbol, base_price=base_price, seed=seed)

    if scenario_type == ScenarioType.STRONG_UPTREND:
        return _create_uptrend_scenario(gen, symbol)
    elif scenario_type == ScenarioType.STRONG_DOWNTREND:
        return _create_downtrend_scenario(gen, symbol)
    elif scenario_type == ScenarioType.PULLBACK_BUY:
        return _create_pullback_buy_scenario(gen, symbol)
    elif scenario_type == ScenarioType.PULLBACK_EXIT:
        return _create_pullback_exit_scenario(gen, symbol)
    elif scenario_type == ScenarioType.VOLATILE:
        return _create_volatile_scenario(gen, symbol)
    elif scenario_type == ScenarioType.SIDEWAYS:
        return _create_sideways_scenario(gen, symbol)
    else:
        return _create_sideways_scenario(gen, symbol)


def _create_uptrend_scenario(gen: MarketDataGenerator, symbol: str) -> MarketScenario:
    """Strong uptrend: EMA50 > EMA200, RSI > 50."""
    candles_60m = gen.generate_candles(250, "60m", drift=0.002, volatility=0.008)
    gen.current_price = gen._round_to_tick(candles_60m[-1].close)

    candles_5m = gen.generate_candles(100, "5m", drift=0.001, volatility=0.003)

    return MarketScenario(
        name="strong_uptrend",
        symbol=symbol,
        description="Strong uptrend with EMA50 > EMA200, RSI > 50",
        candles_60m=candles_60m,
        candles_5m=candles_5m,
        expected_signals=["HOLD"],  # No pullback = no entry
    )


def _create_downtrend_scenario(gen: MarketDataGenerator, symbol: str) -> MarketScenario:
    """Strong downtrend: EMA50 < EMA200, RSI < 50."""
    candles_60m = gen.generate_candles(250, "60m", drift=-0.002, volatility=0.008)
    gen.current_price = gen._round_to_tick(candles_60m[-1].close)

    candles_5m = gen.generate_candles(100, "5m", drift=-0.001, volatility=0.003)

    return MarketScenario(
        name="strong_downtrend",
        symbol=symbol,
        description="Strong downtrend - trend filter should fail",
        candles_60m=candles_60m,
        candles_5m=candles_5m,
        expected_signals=["HOLD"],  # Trend filter fails
    )


def _create_pullback_buy_scenario(gen: MarketDataGenerator, symbol: str) -> MarketScenario:
    """
    Ideal pullback entry scenario for PullbackV1.

    Conditions:
    1. HTF: EMA50 > EMA200 (uptrend)
    2. HTF: RSI >= 40
    3. HTF: Price in EMA20-EMA50 zone (pullback)
    4. LTF: RSI crosses up through 40
    5. LTF: Price > EMA20
    6. LTF: Price > recent swing high

    Key insight: Need price to end up BETWEEN EMA20 and EMA50 (pullback zone)
    """
    candles_60m = []
    base_time = datetime.now() - timedelta(hours=250)
    price = gen.base_price * 0.6  # Start lower for more room

    for i in range(250):
        candle_time = base_time + timedelta(hours=i)

        if i < 150:
            # Strong uptrend phase: ~50% gain
            drift = 0.0028
        elif i < 200:
            # Continued uptrend but slower
            drift = 0.0015
        elif i < 235:
            # Pullback phase: price comes back toward EMAs
            # Need enough decline to get INTO the EMA20-EMA50 zone
            drift = -0.003
        else:
            # Stabilization with small oscillation (keeps RSI reasonable)
            drift = 0.001 if i % 2 == 0 else -0.0005

        change = price * (drift + gen.rng.gauss(0, 0.003))
        close_price = max(price * 0.98, price + change)  # Prevent extreme drops
        vol = 0.004

        candles_60m.append(CandleData(
            symbol=symbol,
            timeframe="60m",
            open_time=candle_time,
            open=price,
            high=max(price, close_price) * (1 + gen.rng.uniform(0, vol)),
            low=min(price, close_price) * (1 - gen.rng.uniform(0, vol)),
            close=close_price,
            volume=gen.rng.randint(100000, 300000),
        ))
        price = close_price

    # Get HTF final price for LTF
    htf_last_close = candles_60m[-1].close

    # LTF: Create RSI crossover setup
    candles_5m = []
    ltf_base_time = datetime.now() - timedelta(minutes=100 * 5)
    ltf_price = htf_last_close * 0.97  # Start slightly below HTF

    for i in range(100):
        candle_time = ltf_base_time + timedelta(minutes=i * 5)

        if i < 80:
            # Decline to push RSI below 40
            drift = -0.0015
        elif i < 97:
            # Continue very mild decline, RSI stays low
            drift = -0.0005
        elif i == 97:
            # Second to last: small down
            drift = -0.001
        elif i == 98:
            # Pre-cross: another small down to ensure RSI < 40
            drift = -0.0005
        else:
            # LAST CANDLE: Strong up move to trigger:
            # 1. RSI cross up through 40
            # 2. Break swing high
            # 3. Above EMA20
            drift = 0.025  # 2.5% up move

        change = ltf_price * (drift + gen.rng.gauss(0, 0.001))
        close_price = ltf_price + change

        candles_5m.append(CandleData(
            symbol=symbol,
            timeframe="5m",
            open_time=candle_time,
            open=ltf_price,
            high=max(ltf_price, close_price) * 1.002,
            low=min(ltf_price, close_price) * 0.998,
            close=close_price,
            volume=gen.rng.randint(20000, 80000),
        ))
        ltf_price = close_price

    # Ensure last candle breaks swing high decisively
    swing_highs = [c.high for c in candles_5m[-12:-1]]
    swing_high = max(swing_highs) if swing_highs else candles_5m[-2].high
    if candles_5m[-1].close <= swing_high * 1.01:
        candles_5m[-1].close = swing_high * 1.02
        candles_5m[-1].high = swing_high * 1.025

    return MarketScenario(
        name="pullback_buy",
        symbol=symbol,
        description="Ideal pullback entry - all conditions met for BUY",
        candles_60m=candles_60m,
        candles_5m=candles_5m,
        expected_signals=["BUY"],
    )


def _create_pullback_exit_scenario(gen: MarketDataGenerator, symbol: str) -> MarketScenario:
    """
    Exit trigger scenario - RSI crosses down through 50 or price below EMA20.

    Strategy flow:
    1. Pass trend filter (EMA50 > EMA200, RSI >= 40) ✓
    2. Pass pullback zone check ✓
    3. Then check exit conditions - trigger RSI cross down or below EMA20

    Key: We need to be in pullback zone FIRST, then trigger exit.
    """
    # HTF: Same setup as pullback buy - uptrend with pullback zone
    candles_60m = []
    base_time = datetime.now() - timedelta(hours=250)
    price = gen.base_price * 0.6

    for i in range(250):
        candle_time = base_time + timedelta(hours=i)

        if i < 150:
            drift = 0.0028
        elif i < 200:
            drift = 0.0015
        elif i < 235:
            drift = -0.003
        else:
            drift = 0.001 if i % 2 == 0 else -0.0005

        change = price * (drift + gen.rng.gauss(0, 0.003))
        close_price = max(price * 0.98, price + change)
        vol = 0.004

        candles_60m.append(CandleData(
            symbol=symbol,
            timeframe="60m",
            open_time=candle_time,
            open=price,
            high=max(price, close_price) * (1 + gen.rng.uniform(0, vol)),
            low=min(price, close_price) * (1 - gen.rng.uniform(0, vol)),
            close=close_price,
            volume=gen.rng.randint(100000, 300000),
        ))
        price = close_price

    htf_last_close = candles_60m[-1].close

    # LTF: Setup for EXIT trigger
    # Need RSI to start >= 50, then cross DOWN through 50
    # OR price to fall below EMA20
    candles_5m = []
    ltf_base_time = datetime.now() - timedelta(minutes=100 * 5)
    ltf_price = htf_last_close * 1.02  # Start above for RSI > 50

    for i in range(100):
        candle_time = ltf_base_time + timedelta(minutes=i * 5)

        if i < 60:
            # Stable/slight up - keeps RSI healthy (above 50)
            drift = 0.001
        elif i < 85:
            # Gradual decline - RSI approaching 50
            drift = -0.002
        elif i < 98:
            # More decline - RSI gets close to 50
            drift = -0.003
        else:
            # Sharp drop on last candles - triggers RSI cross DOWN through 50
            # and/or breaks below EMA20
            drift = -0.02

        change = ltf_price * (drift + gen.rng.gauss(0, 0.001))
        close_price = ltf_price + change

        candles_5m.append(CandleData(
            symbol=symbol,
            timeframe="5m",
            open_time=candle_time,
            open=ltf_price,
            high=max(ltf_price, close_price) * 1.001,
            low=min(ltf_price, close_price) * 0.999,
            close=close_price,
            volume=gen.rng.randint(30000, 100000),
        ))
        ltf_price = close_price

    return MarketScenario(
        name="pullback_exit",
        symbol=symbol,
        description="Exit trigger - RSI falling below 50 or price below EMA20",
        candles_60m=candles_60m,
        candles_5m=candles_5m,
        expected_signals=["SELL"],
    )


def _create_volatile_scenario(gen: MarketDataGenerator, symbol: str) -> MarketScenario:
    """High volatility scenario."""
    candles_60m = gen.generate_candles(250, "60m", drift=0.0, volatility=0.025)
    gen.current_price = gen._round_to_tick(candles_60m[-1].close)

    candles_5m = gen.generate_candles(100, "5m", drift=0.0, volatility=0.015)

    return MarketScenario(
        name="volatile",
        symbol=symbol,
        description="High volatility - strategy should be cautious",
        candles_60m=candles_60m,
        candles_5m=candles_5m,
        expected_signals=["HOLD"],
    )


def _create_sideways_scenario(gen: MarketDataGenerator, symbol: str) -> MarketScenario:
    """Sideways/ranging market."""
    candles_60m = gen.generate_candles(250, "60m", drift=0.0, volatility=0.005)
    gen.current_price = gen._round_to_tick(candles_60m[-1].close)

    candles_5m = gen.generate_candles(100, "5m", drift=0.0, volatility=0.002)

    return MarketScenario(
        name="sideways",
        symbol=symbol,
        description="Sideways market - no clear trend",
        candles_60m=candles_60m,
        candles_5m=candles_5m,
        expected_signals=["HOLD"],
    )


def save_scenario(scenario: MarketScenario, path: Path) -> None:
    """Save scenario to JSON file."""
    data = {
        "name": scenario.name,
        "symbol": scenario.symbol,
        "description": scenario.description,
        "expected_signals": scenario.expected_signals,
        "candles_60m": [c.to_dict() for c in scenario.candles_60m],
        "candles_5m": [c.to_dict() for c in scenario.candles_5m],
        "candles_1m": [c.to_dict() for c in scenario.candles_1m],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_scenario(path: Path) -> MarketScenario:
    """Load scenario from JSON file."""
    with open(path) as f:
        data = json.load(f)

    def dict_to_candle(d: dict) -> CandleData:
        return CandleData(
            symbol=d["symbol"],
            timeframe=d["timeframe"],
            open_time=datetime.fromtimestamp(d["open_time"]),
            open=d["open"],
            high=d["high"],
            low=d["low"],
            close=d["close"],
            volume=d["volume"],
        )

    return MarketScenario(
        name=data["name"],
        symbol=data["symbol"],
        description=data["description"],
        expected_signals=data.get("expected_signals", []),
        candles_60m=[dict_to_candle(c) for c in data.get("candles_60m", [])],
        candles_5m=[dict_to_candle(c) for c in data.get("candles_5m", [])],
        candles_1m=[dict_to_candle(c) for c in data.get("candles_1m", [])],
    )
