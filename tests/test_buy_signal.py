"""Precise test to trigger BUY signal in PullbackV1."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from krader.strategy import PullbackV1
from krader.strategy.base import MarketSnapshot, StrategyContext
from krader.risk.portfolio import Portfolio


def create_precise_htf_candles(symbol: str) -> list[dict]:
    """
    Create HTF candles that pass ALL conditions:
    1. Trend filter: EMA50 > EMA200 ✓
    2. RSI >= 40 ✓
    3. Price in pullback zone (between EMA20 and EMA50) ✓

    Key insight:
    - RSI >= 40 requires recent gains (bullish action)
    - But we need price in pullback zone
    - Solution: Strong uptrend with mild pullback that still shows gains
    """
    candles = []
    base_time = datetime.now() - timedelta(hours=250)

    for i in range(250):
        if i < 180:
            # Strong uptrend: 40000 -> 68000 (70% gain)
            progress = i / 180
            base_price = 40000 + (28000 * progress)
        elif i < 230:
            # Mild pullback: 68000 -> 62000 (~9% pullback)
            # But with up/down oscillation to keep RSI moderate
            pullback_progress = (i - 180) / 50
            base_price = 68000 * (1 - 0.09 * pullback_progress)
            # Add oscillation
            if i % 2 == 0:
                base_price *= 1.005
            else:
                base_price *= 0.995
        else:
            # Final candles: Slight recovery (keeps RSI >= 40)
            recovery_progress = (i - 230) / 20
            base_price = 62000 * (1 + 0.02 * recovery_progress)  # Up 2%

        candles.append({
            "symbol": symbol,
            "timeframe": "60m",
            "open_time": int((base_time + timedelta(hours=i)).timestamp()),
            "open": base_price * 0.999,
            "high": base_price * 1.004,
            "low": base_price * 0.996,
            "close": base_price * 1.001,
            "volume": 100000,
        })

    return candles


def create_rsi_crossover_ltf_candles(symbol: str, base_price: float) -> list[dict]:
    """
    Create LTF candles where RSI crosses UP through 40 in the LAST candle.

    Need: RSI[-2] < 40 AND RSI[-1] >= 40

    Strategy:
    - Continuous decline for 98 candles (RSI drops to ~30)
    - Candle 98: Still declining (RSI ~35-38)
    - Candle 99: Sharp up move (RSI crosses to ~42+)
    """
    candles = []
    base_time = datetime.now() - timedelta(minutes=100 * 5)

    prices = []
    start = base_price

    for i in range(100):
        if i < 98:
            # Continuous steady decline - keeps RSI low
            # ~0.25% decline per candle = 24.5% total decline
            price = start * (1 - 0.0025 * i)
        elif i == 98:
            # Second to last: continue decline, RSI should be ~35
            price = start * (1 - 0.0025 * 98)
        else:
            # LAST CANDLE: Sharp 5% up move
            # This should push RSI from ~35 to ~45
            prev_price = start * (1 - 0.0025 * 98)
            price = prev_price * 1.05

        prices.append(price)

    # Build candles
    for i, close_price in enumerate(prices):
        if i == 0:
            open_price = close_price * 1.002
        else:
            open_price = prices[i - 1]

        if close_price > open_price:
            high_price = close_price * 1.002
            low_price = open_price * 0.998
        else:
            high_price = open_price * 1.001
            low_price = close_price * 0.999

        candles.append({
            "symbol": symbol,
            "timeframe": "5m",
            "open_time": int((base_time + timedelta(minutes=i * 5)).timestamp()),
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": 50000,
        })

    # Calculate swing high (max high from candles 89-98, i.e., 10 candles before last)
    swing_highs = [c["high"] for c in candles[-11:-1]]
    swing_high = max(swing_highs) if swing_highs else candles[-2]["high"]

    # Ensure last candle breaks swing high
    last_candle = candles[-1]
    if last_candle["close"] <= swing_high:
        last_candle["close"] = swing_high * 1.02
        last_candle["high"] = swing_high * 1.03

    # Also ensure last close > LTF EMA20
    # EMA20 will be around the average of recent prices
    # Our last candle at 1.05x should be well above the declining EMA20

    return candles


async def test_buy_signal():
    """Test to generate a BUY signal."""
    print("=" * 70)
    print("PullbackV1 - BUY Signal Test")
    print("=" * 70)

    strategy = PullbackV1(cooldown_minutes=0)
    universe = ["005930"]

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

    symbol = "005930"
    htf_candles = create_precise_htf_candles(symbol)

    # Debug: Check HTF indicators
    print("\n[HTF Analysis]")
    closes = [c["close"] for c in htf_candles]

    # Calculate EMAs manually for verification
    def calc_ema(values, period):
        if len(values) < period:
            return 0
        ema_val = sum(values[:period]) / period
        mult = 2 / (period + 1)
        for v in values[period:]:
            ema_val = (v - ema_val) * mult + ema_val
        return ema_val

    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    ema200 = calc_ema(closes, 200)
    last_close = closes[-1]

    print(f"  Last close: {last_close:,.2f}")
    print(f"  EMA20:      {ema20:,.2f}")
    print(f"  EMA50:      {ema50:,.2f}")
    print(f"  EMA200:     {ema200:,.2f}")
    print(f"  EMA50 > EMA200: {ema50 > ema200}")
    print(f"  Price in pullback zone (EMA20-EMA50): {min(ema20, ema50) <= last_close <= max(ema20, ema50)}")

    ltf_candles = create_rsi_crossover_ltf_candles(symbol, last_close)

    print("\n[LTF Analysis]")
    ltf_closes = [c["close"] for c in ltf_candles]
    ltf_highs = [c["high"] for c in ltf_candles]

    ltf_ema20 = calc_ema(ltf_closes, 20)
    ltf_last = ltf_closes[-1]
    swing_high = max(ltf_highs[-12:-1]) if len(ltf_highs) > 12 else max(ltf_highs[:-1])

    print(f"  Last close: {ltf_last:,.2f}")
    print(f"  EMA20:      {ltf_ema20:,.2f}")
    print(f"  Swing high: {swing_high:,.2f}")
    print(f"  Price > EMA20: {ltf_last > ltf_ema20}")
    print(f"  Price > Swing: {ltf_last > swing_high}")

    # Calculate RSI for last 2 candles
    def calc_rsi(values, period=14):
        if len(values) <= period:
            return [50] * len(values)
        deltas = [values[i] - values[i-1] for i in range(1, len(values))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsi_values = [50] * (period + 1)
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_values.append(100)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100 - (100 / (1 + rs)))
        return rsi_values

    rsi_values = calc_rsi(ltf_closes)
    print(f"  RSI[-2]:    {rsi_values[-2]:.2f}")
    print(f"  RSI[-1]:    {rsi_values[-1]:.2f}")
    print(f"  RSI cross up through 40: {rsi_values[-2] < 40 and rsi_values[-1] >= 40}")

    print("\n[Running Strategy]")
    print("-" * 50)

    snapshot = MarketSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        historical_candles={
            "60m": htf_candles,
            "5m": ltf_candles,
        },
    )

    signals = await strategy.on_market_data(snapshot, context)

    if signals:
        sig = signals[0]
        print(f"\nSIGNAL GENERATED:")
        print(f"  Action:     {sig.action}")
        print(f"  Confidence: {sig.confidence}")
        print(f"  Reason:     {sig.reason}")
        print(f"\nStrategy Metadata:")
        for key, value in sig.metadata.items():
            if isinstance(value, float):
                print(f"  {key}: {value:.2f}")
            else:
                print(f"  {key}: {value}")

        if sig.action == "BUY":
            print("\n" + "=" * 70)
            print("✅ SUCCESS: BUY SIGNAL TRIGGERED!")
            print("=" * 70)
        elif sig.action == "SELL":
            print("\n" + "=" * 70)
            print("✅ SELL SIGNAL TRIGGERED")
            print("=" * 70)
        else:
            print(f"\n⚠️  HOLD signal - Conditions not fully met")
            print(f"    Reason: {sig.reason}")
    else:
        print("No signals generated")


if __name__ == "__main__":
    asyncio.run(test_buy_signal())
