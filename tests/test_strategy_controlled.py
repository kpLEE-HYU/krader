"""Controlled test for PullbackV1 with exact conditions for BUY/SELL triggers."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from krader.strategy import PullbackV1
from krader.strategy.base import MarketSnapshot, StrategyContext
from krader.risk.portfolio import Portfolio


def create_uptrend_htf_candles(symbol: str, count: int = 250) -> list[dict]:
    """
    Create HTF candles with clear uptrend where EMA50 > EMA200.

    Price progression: 40000 -> 60000 (50% gain over 250 candles)
    This ensures EMA50 > EMA200.
    """
    candles = []
    base_time = datetime.now() - timedelta(hours=count)

    for i in range(count):
        # Linear price increase with small noise
        progress = i / count
        base_price = 40000 + (20000 * progress)  # 40000 -> 60000

        # Last 20 candles: pullback to EMA zone
        if i >= 230:
            pullback_depth = (i - 230) / 20  # 0 to 1
            base_price = base_price * (1 - 0.05 * pullback_depth)  # Up to 5% pullback

        noise = base_price * 0.005 * (1 if i % 2 == 0 else -1)
        close_price = base_price + noise
        open_price = base_price - noise

        candles.append({
            "symbol": symbol,
            "timeframe": "60m",
            "open_time": int((base_time + timedelta(hours=i)).timestamp()),
            "open": open_price,
            "high": max(open_price, close_price) * 1.005,
            "low": min(open_price, close_price) * 0.995,
            "close": close_price,
            "volume": 100000,
        })

    return candles


def create_entry_trigger_ltf_candles(symbol: str, base_price: float, count: int = 100) -> list[dict]:
    """
    Create LTF candles that trigger entry:
    1. RSI was < 40, now crosses above 40
    2. Price > EMA20
    3. Price breaks swing high
    """
    candles = []
    base_time = datetime.now() - timedelta(minutes=count * 5)

    for i in range(count):
        progress = i / count

        if i < 70:
            # Declining phase - RSI will be low
            price = base_price * (1 - 0.08 * progress)  # Drop 8%
        elif i < 90:
            # Recovery starting - RSI approaching 40
            recovery_progress = (i - 70) / 20
            price = base_price * (0.92 + 0.04 * recovery_progress)  # Recover half
        else:
            # Breakout - RSI crosses above 40, price breaks swing high
            breakout_progress = (i - 90) / 10
            price = base_price * (0.96 + 0.06 * breakout_progress)  # Break above

        # Small alternating noise to create proper OHLC
        noise = price * 0.002 * (1 if i % 2 == 0 else -1)
        close_price = price + noise
        open_price = price - noise

        candles.append({
            "symbol": symbol,
            "timeframe": "5m",
            "open_time": int((base_time + timedelta(minutes=i * 5)).timestamp()),
            "open": open_price,
            "high": max(open_price, close_price) * 1.002,
            "low": min(open_price, close_price) * 0.998,
            "close": close_price,
            "volume": 50000,
        })

    return candles


def create_exit_trigger_ltf_candles(symbol: str, base_price: float, count: int = 100) -> list[dict]:
    """
    Create LTF candles that trigger exit:
    1. RSI crosses down below 50
    2. Price falls below EMA20
    """
    candles = []
    base_time = datetime.now() - timedelta(minutes=count * 5)

    for i in range(count):
        if i < 60:
            # Stable/rising phase - RSI high
            price = base_price * (1 + 0.03 * (i / 60))
        elif i < 85:
            # Topping out - RSI starting to fall
            price = base_price * 1.03 * (1 - 0.02 * ((i - 60) / 25))
        else:
            # Sharp decline - RSI crosses below 50
            decline_progress = (i - 85) / 15
            price = base_price * 1.01 * (1 - 0.05 * decline_progress)

        noise = price * 0.001 * (1 if i % 2 == 0 else -1)
        close_price = price + noise
        open_price = price - noise

        candles.append({
            "symbol": symbol,
            "timeframe": "5m",
            "open_time": int((base_time + timedelta(minutes=i * 5)).timestamp()),
            "open": open_price,
            "high": max(open_price, close_price) * 1.001,
            "low": min(open_price, close_price) * 0.999,
            "close": close_price,
            "volume": 50000,
        })

    return candles


def create_downtrend_htf_candles(symbol: str, count: int = 250) -> list[dict]:
    """Create HTF candles with clear downtrend where EMA50 < EMA200."""
    candles = []
    base_time = datetime.now() - timedelta(hours=count)

    for i in range(count):
        progress = i / count
        base_price = 60000 - (20000 * progress)  # 60000 -> 40000

        noise = base_price * 0.005 * (1 if i % 2 == 0 else -1)
        close_price = base_price + noise
        open_price = base_price - noise

        candles.append({
            "symbol": symbol,
            "timeframe": "60m",
            "open_time": int((base_time + timedelta(hours=i)).timestamp()),
            "open": open_price,
            "high": max(open_price, close_price) * 1.005,
            "low": min(open_price, close_price) * 0.995,
            "close": close_price,
            "volume": 100000,
        })

    return candles


async def run_controlled_tests():
    """Run strategy tests with controlled data."""
    print("=" * 70)
    print("PullbackV1 Strategy - Controlled Test Suite")
    print("=" * 70)

    strategy = PullbackV1(cooldown_minutes=0)  # Disable cooldown for testing
    universe = ["005930", "000660", "035420"]

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

    # Test 1: Not in universe
    print("\n" + "=" * 70)
    print("TEST 1: Symbol NOT in universe")
    print("=" * 70)

    snapshot = MarketSnapshot(
        symbol="999999",
        timestamp=datetime.now(),
        historical_candles={
            "60m": create_uptrend_htf_candles("999999"),
            "5m": create_entry_trigger_ltf_candles("999999", 57000),
        },
    )

    signals = await strategy.on_market_data(snapshot, context)
    print(f"Expected: [] (empty)")
    print(f"Actual:   {signals}")
    print(f"RESULT:   {'✅ PASS' if signals == [] else '❌ FAIL'}")

    # Test 2: Downtrend - trend filter fail
    print("\n" + "=" * 70)
    print("TEST 2: Downtrend - Trend Filter Fail")
    print("=" * 70)

    symbol = "005930"
    snapshot = MarketSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        historical_candles={
            "60m": create_downtrend_htf_candles(symbol),
            "5m": create_entry_trigger_ltf_candles(symbol, 42000),
        },
    )

    signals = await strategy.on_market_data(snapshot, context)
    print(f"Expected: HOLD with reason='trend_filter_fail'")
    if signals:
        sig = signals[0]
        print(f"Actual:   action={sig.action}, reason={sig.reason}")
        print(f"          EMA50={sig.metadata.get('htf_ema50'):.2f}, EMA200={sig.metadata.get('htf_ema200'):.2f}")
        passed = sig.action == "HOLD" and sig.reason == "trend_filter_fail"
    else:
        print(f"Actual:   {signals}")
        passed = False
    print(f"RESULT:   {'✅ PASS' if passed else '❌ FAIL'}")

    # Test 3: Uptrend with entry trigger - BUY
    print("\n" + "=" * 70)
    print("TEST 3: Uptrend + Pullback + Entry Trigger = BUY")
    print("=" * 70)

    symbol = "000660"
    htf_candles = create_uptrend_htf_candles(symbol)
    last_htf_close = htf_candles[-1]["close"]

    snapshot = MarketSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        historical_candles={
            "60m": htf_candles,
            "5m": create_entry_trigger_ltf_candles(symbol, last_htf_close),
        },
    )

    signals = await strategy.on_market_data(snapshot, context)
    print(f"Expected: BUY or HOLD (depends on exact entry conditions)")
    if signals:
        sig = signals[0]
        print(f"Actual:   action={sig.action}, confidence={sig.confidence}, reason={sig.reason}")
        print(f"Indicators:")
        print(f"  HTF EMA50:  {sig.metadata.get('htf_ema50'):.2f}")
        print(f"  HTF EMA200: {sig.metadata.get('htf_ema200'):.2f}")
        print(f"  HTF RSI:    {sig.metadata.get('htf_rsi14'):.2f}")
        print(f"  LTF EMA20:  {sig.metadata.get('ltf_ema20'):.2f}")
        print(f"  LTF RSI:    {sig.metadata.get('ltf_rsi14'):.2f}")
        print(f"  Swing High: {sig.metadata.get('swing_high'):.2f}")

        if sig.action == "BUY":
            print(f"RESULT:   ✅ BUY SIGNAL GENERATED!")
        elif sig.action == "HOLD":
            print(f"RESULT:   ⚠️  HOLD - {sig.reason}")
            if "no_pullback" in sig.reason:
                print(f"          (Price not in pullback zone)")
            elif "hold" == sig.reason:
                print(f"          (Entry conditions not met)")
    else:
        print(f"Actual:   {signals}")
        print(f"RESULT:   ❌ FAIL")

    # Test 4: Exit trigger - SELL
    print("\n" + "=" * 70)
    print("TEST 4: Exit Trigger = SELL")
    print("=" * 70)

    symbol = "035420"
    htf_candles = create_uptrend_htf_candles(symbol)
    last_htf_close = htf_candles[-1]["close"]

    snapshot = MarketSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        historical_candles={
            "60m": htf_candles,
            "5m": create_exit_trigger_ltf_candles(symbol, last_htf_close),
        },
    )

    signals = await strategy.on_market_data(snapshot, context)
    print(f"Expected: SELL or HOLD")
    if signals:
        sig = signals[0]
        print(f"Actual:   action={sig.action}, confidence={sig.confidence}, reason={sig.reason}")
        print(f"Indicators:")
        print(f"  LTF RSI:   {sig.metadata.get('ltf_rsi14'):.2f}")
        print(f"  LTF EMA20: {sig.metadata.get('ltf_ema20'):.2f}")

        if sig.action == "SELL":
            print(f"  RSI cross down: {sig.metadata.get('rsi_cross_down')}")
            print(f"  Below EMA:      {sig.metadata.get('below_ema')}")
            print(f"RESULT:   ✅ SELL SIGNAL GENERATED!")
        else:
            print(f"RESULT:   ⚠️  {sig.action} - {sig.reason}")

    # Test 5: Cooldown test
    print("\n" + "=" * 70)
    print("TEST 5: Cooldown Behavior")
    print("=" * 70)

    strategy_with_cooldown = PullbackV1(cooldown_minutes=30)

    symbol = "005930"
    htf_candles = create_uptrend_htf_candles(symbol)

    # First call
    snapshot = MarketSnapshot(
        symbol=symbol,
        timestamp=datetime.now(),
        historical_candles={
            "60m": htf_candles,
            "5m": create_entry_trigger_ltf_candles(symbol, htf_candles[-1]["close"]),
        },
    )

    signals1 = await strategy_with_cooldown.on_market_data(snapshot, context)
    print(f"First call:  action={signals1[0].action if signals1 else 'none'}, cooldown_active={signals1[0].metadata.get('cooldown_active') if signals1 else 'N/A'}")

    # If it was a BUY, second call should show cooldown
    if signals1 and signals1[0].action == "BUY":
        signals2 = await strategy_with_cooldown.on_market_data(snapshot, context)
        print(f"Second call: action={signals2[0].action if signals2 else 'none'}, cooldown_active={signals2[0].metadata.get('cooldown_active') if signals2 else 'N/A'}")
        if signals2 and signals2[0].metadata.get("cooldown_active"):
            print(f"RESULT:   ✅ Cooldown is working!")
        else:
            print(f"RESULT:   ⚠️  Cooldown may not be triggered (depends on signal type)")

    # Test 6: Market closed
    print("\n" + "=" * 70)
    print("TEST 6: Market Closed")
    print("=" * 70)

    closed_context = StrategyContext(
        portfolio=portfolio,
        active_orders_count=0,
        daily_trades_count=0,
        is_market_open=False,  # Market closed!
        metadata={"universe_top20": universe},
    )

    signals = await strategy.on_market_data(snapshot, closed_context)
    print(f"Expected: [] (empty - market closed)")
    print(f"Actual:   {signals}")
    print(f"RESULT:   {'✅ PASS' if signals == [] else '❌ FAIL'}")

    print("\n" + "=" * 70)
    print("Test Suite Complete")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_controlled_tests())
