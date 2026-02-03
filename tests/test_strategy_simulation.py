"""Test PullbackV1 strategy with simulated market data."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import random

from krader.strategy import PullbackV1
from krader.strategy.base import MarketSnapshot, StrategyContext
from krader.risk.portfolio import Portfolio


def generate_trending_candles(
    symbol: str,
    timeframe: str,
    count: int,
    start_price: float = 50000,
    trend: str = "up",
    volatility: float = 0.02,
) -> list[dict]:
    """Generate fake candles with a trend."""
    candles = []
    price = start_price
    base_time = datetime.now() - timedelta(minutes=count * _timeframe_minutes(timeframe))

    for i in range(count):
        # Trend bias
        if trend == "up":
            drift = random.uniform(0, volatility * 1.5)
        elif trend == "down":
            drift = random.uniform(-volatility * 1.5, 0)
        else:
            drift = random.uniform(-volatility, volatility)

        # Random movement
        change = price * (drift + random.uniform(-volatility, volatility))

        open_price = price
        close_price = price + change
        high_price = max(open_price, close_price) * (1 + random.uniform(0, volatility/2))
        low_price = min(open_price, close_price) * (1 - random.uniform(0, volatility/2))
        volume = random.randint(10000, 100000)

        candle_time = base_time + timedelta(minutes=i * _timeframe_minutes(timeframe))

        candles.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "open_time": int(candle_time.timestamp()),
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
        })

        price = close_price

    return candles


def generate_pullback_scenario(
    symbol: str,
    base_price: float = 50000,
) -> dict[str, list[dict]]:
    """
    Generate a scenario where pullback entry conditions are likely to trigger.

    Scenario:
    1. Strong uptrend (EMA50 > EMA200)
    2. Recent pullback to EMA20-EMA50 zone
    3. RSI recovering from oversold
    """
    # HTF (60m): 250 candles of uptrend with recent pullback
    htf_candles = []
    price = base_price * 0.8  # Start lower
    base_time = datetime.now() - timedelta(hours=250)

    for i in range(250):
        candle_time = base_time + timedelta(hours=i)

        # First 200 candles: strong uptrend
        if i < 200:
            drift = random.uniform(0.001, 0.005)
        # Last 50 candles: pullback
        elif i < 240:
            drift = random.uniform(-0.003, 0.001)
        # Last 10 candles: starting to recover
        else:
            drift = random.uniform(-0.001, 0.003)

        change = price * drift
        volatility = 0.01

        open_price = price
        close_price = price + change
        high_price = max(open_price, close_price) * (1 + random.uniform(0, volatility))
        low_price = min(open_price, close_price) * (1 - random.uniform(0, volatility))

        htf_candles.append({
            "symbol": symbol,
            "timeframe": "60m",
            "open_time": int(candle_time.timestamp()),
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": random.randint(50000, 200000),
        })

        price = close_price

    # LTF (5m): Generate based on last HTF price, with RSI cross setup
    ltf_candles = []
    ltf_price = price * 0.995  # Slightly below HTF close
    ltf_base_time = datetime.now() - timedelta(minutes=100 * 5)

    for i in range(100):
        candle_time = ltf_base_time + timedelta(minutes=i * 5)

        # First 80 candles: weak/oversold
        if i < 80:
            drift = random.uniform(-0.002, 0.001)
        # Last 20 candles: recovery (RSI crossing up)
        else:
            drift = random.uniform(0.001, 0.004)

        change = ltf_price * drift
        volatility = 0.005

        open_price = ltf_price
        close_price = ltf_price + change
        high_price = max(open_price, close_price) * (1 + random.uniform(0, volatility))
        low_price = min(open_price, close_price) * (1 - random.uniform(0, volatility))

        ltf_candles.append({
            "symbol": symbol,
            "timeframe": "5m",
            "open_time": int(candle_time.timestamp()),
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": random.randint(10000, 50000),
        })

        ltf_price = close_price

    return {
        "60m": htf_candles,
        "5m": ltf_candles,
    }


def generate_exit_scenario(
    symbol: str,
    base_price: float = 55000,
) -> dict[str, list[dict]]:
    """Generate scenario where exit conditions trigger."""
    # HTF: Still in uptrend
    htf_candles = generate_trending_candles(symbol, "60m", 250, base_price * 0.9, "up", 0.01)

    # LTF: Price breaking down below EMA20, RSI falling
    ltf_candles = []
    ltf_price = base_price
    ltf_base_time = datetime.now() - timedelta(minutes=100 * 5)

    for i in range(100):
        candle_time = ltf_base_time + timedelta(minutes=i * 5)

        # First 70 candles: stable/up
        if i < 70:
            drift = random.uniform(-0.001, 0.002)
        # Next 20: starting to fall
        elif i < 90:
            drift = random.uniform(-0.003, 0)
        # Last 10: sharp drop (exit trigger)
        else:
            drift = random.uniform(-0.005, -0.002)

        change = ltf_price * drift
        open_price = ltf_price
        close_price = ltf_price + change
        high_price = max(open_price, close_price) * 1.002
        low_price = min(open_price, close_price) * 0.998

        ltf_candles.append({
            "symbol": symbol,
            "timeframe": "5m",
            "open_time": int(candle_time.timestamp()),
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": random.randint(10000, 50000),
        })

        ltf_price = close_price

    return {
        "60m": htf_candles,
        "5m": ltf_candles,
    }


def _timeframe_minutes(tf: str) -> int:
    return {"1m": 1, "5m": 5, "15m": 15, "60m": 60, "1h": 60, "4h": 240, "1d": 1440}.get(tf, 1)


async def run_strategy_test():
    """Run the strategy with simulated data."""
    print("=" * 70)
    print("PullbackV1 Strategy Simulation Test")
    print("=" * 70)

    strategy = PullbackV1()
    universe = ["005930", "000660", "035420"]  # Samsung, SK Hynix, NAVER

    portfolio = Portfolio(
        cash=Decimal("10000000"),
        total_equity=Decimal("10000000"),
    )

    context = StrategyContext(
        portfolio=portfolio,
        active_orders_count=0,
        daily_trades_count=0,
        is_market_open=True,
        metadata={"universe_top20": universe},
    )

    print("\n[Test 1] Symbol NOT in universe - should return empty")
    print("-" * 50)

    candles = generate_trending_candles("999999", "60m", 250, 50000, "up")
    snapshot = MarketSnapshot(
        symbol="999999",
        timestamp=datetime.now(),
        historical_candles={"60m": candles, "5m": generate_trending_candles("999999", "5m", 100, 50000, "up")},
    )

    signals = await strategy.on_market_data(snapshot, context)
    print(f"Symbol: 999999 (not in universe)")
    print(f"Signals: {signals}")
    print(f"Result: {'PASS' if signals == [] else 'FAIL'}")

    print("\n[Test 2] Downtrend - should return HOLD (trend filter fail)")
    print("-" * 50)

    symbol = "005930"
    htf_candles = generate_trending_candles(symbol, "60m", 250, 50000, "down", 0.01)
    ltf_candles = generate_trending_candles(symbol, "5m", 100, 45000, "down", 0.005)

    snapshot = MarketSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        historical_candles={"60m": htf_candles, "5m": ltf_candles},
    )

    signals = await strategy.on_market_data(snapshot, context)
    print(f"Symbol: {symbol}")
    print(f"Scenario: Downtrend (EMA50 < EMA200)")
    if signals:
        sig = signals[0]
        print(f"Action: {sig.action}")
        print(f"Reason: {sig.reason}")
        print(f"Metadata: htf_ema50={sig.metadata.get('htf_ema50')}, htf_ema200={sig.metadata.get('htf_ema200')}")
    print(f"Result: {'PASS' if signals and signals[0].action == 'HOLD' else 'FAIL'}")

    print("\n[Test 3] Uptrend with pullback - potential BUY scenario")
    print("-" * 50)

    symbol = "000660"
    candles_data = generate_pullback_scenario(symbol, 120000)

    snapshot = MarketSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        historical_candles=candles_data,
    )

    signals = await strategy.on_market_data(snapshot, context)
    print(f"Symbol: {symbol}")
    print(f"Scenario: Uptrend with pullback recovery")
    if signals:
        sig = signals[0]
        print(f"Action: {sig.action}")
        print(f"Confidence: {sig.confidence}")
        print(f"Reason: {sig.reason}")
        print(f"Metadata:")
        for key in ["htf_ema50", "htf_ema200", "htf_rsi14", "ltf_rsi14", "swing_high"]:
            print(f"  {key}: {sig.metadata.get(key)}")

    print("\n[Test 4] Exit scenario - should return SELL")
    print("-" * 50)

    symbol = "035420"
    candles_data = generate_exit_scenario(symbol, 180000)

    snapshot = MarketSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        historical_candles=candles_data,
    )

    signals = await strategy.on_market_data(snapshot, context)
    print(f"Symbol: {symbol}")
    print(f"Scenario: Price breaking down, RSI falling")
    if signals:
        sig = signals[0]
        print(f"Action: {sig.action}")
        print(f"Confidence: {sig.confidence}")
        print(f"Reason: {sig.reason}")
        if sig.action == "SELL":
            print(f"Exit triggers: rsi_cross_down={sig.metadata.get('rsi_cross_down')}, below_ema={sig.metadata.get('below_ema')}")

    print("\n[Test 5] Multiple candle updates simulation")
    print("-" * 50)

    symbol = "005930"
    base_candles = generate_pullback_scenario(symbol, 70000)

    print(f"Simulating 10 candle updates for {symbol}...")
    print()

    for i in range(10):
        # Add a new candle to simulate time passing
        last_htf = base_candles["60m"][-1]
        last_ltf = base_candles["5m"][-1]

        # Simulate price movement
        new_close = last_ltf["close"] * (1 + random.uniform(-0.002, 0.004))

        new_ltf_candle = {
            "symbol": symbol,
            "timeframe": "5m",
            "open_time": last_ltf["open_time"] + 300,
            "open": last_ltf["close"],
            "high": max(last_ltf["close"], new_close) * 1.001,
            "low": min(last_ltf["close"], new_close) * 0.999,
            "close": new_close,
            "volume": random.randint(10000, 50000),
        }
        base_candles["5m"].append(new_ltf_candle)
        base_candles["5m"] = base_candles["5m"][-100:]  # Keep last 100

        snapshot = MarketSnapshot(
            symbol=symbol,
            timestamp=datetime.now(),
            historical_candles=base_candles,
        )

        signals = await strategy.on_market_data(snapshot, context)

        if signals:
            sig = signals[0]
            price = new_close
            print(f"  Candle {i+1}: Price={price:,.0f} | Action={sig.action:4} | Reason={sig.reason}")

    print("\n" + "=" * 70)
    print("Simulation Complete")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_strategy_test())
