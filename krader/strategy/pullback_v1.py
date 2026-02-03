"""Pullback Continuation Strategy (trend-following pullback entry)."""

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from krader.strategy.base import BaseStrategy, MarketSnapshot, StrategyContext
from krader.strategy.signal import Signal


def _extract_closes(candles: list[dict]) -> list[float]:
    """Extract close prices from candle dicts, handling various field names."""
    closes = []
    for c in candles:
        close_val = c.get("close")
        if close_val is None:
            close_val = c.get("Close")
        if close_val is not None:
            try:
                closes.append(float(close_val))
            except (ValueError, TypeError):
                continue
    return closes


def _extract_highs(candles: list[dict]) -> list[float]:
    """Extract high prices from candle dicts."""
    highs = []
    for c in candles:
        high_val = c.get("high")
        if high_val is None:
            high_val = c.get("High")
        if high_val is not None:
            try:
                highs.append(float(high_val))
            except (ValueError, TypeError):
                continue
    return highs


def _extract_lows(candles: list[dict]) -> list[float]:
    """Extract low prices from candle dicts."""
    lows = []
    for c in candles:
        low_val = c.get("low")
        if low_val is None:
            low_val = c.get("Low")
        if low_val is not None:
            try:
                lows.append(float(low_val))
            except (ValueError, TypeError):
                continue
    return lows


def _extract_opens(candles: list[dict]) -> list[float]:
    """Extract open prices from candle dicts."""
    opens = []
    for c in candles:
        open_val = c.get("open")
        if open_val is None:
            open_val = c.get("Open")
        if open_val is not None:
            try:
                opens.append(float(open_val))
            except (ValueError, TypeError):
                continue
    return opens


def ema(values: list[float], period: int) -> list[float]:
    """Compute EMA over a list of values. Returns list of same length with NaN-like 0.0 for initial."""
    if not values or period <= 0:
        return []
    result = []
    multiplier = 2.0 / (period + 1)
    ema_val = 0.0
    for i, val in enumerate(values):
        if i < period - 1:
            result.append(0.0)
        elif i == period - 1:
            ema_val = sum(values[: period]) / period
            result.append(ema_val)
        else:
            ema_val = (val - ema_val) * multiplier + ema_val
            result.append(ema_val)
    return result


def rsi(values: list[float], period: int = 14) -> list[float]:
    """Compute RSI over a list of values. Returns list of same length."""
    if len(values) < period + 1:
        return [50.0] * len(values)
    result = []
    gains = []
    losses = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))
    for i in range(len(values)):
        if i < period:
            result.append(50.0)
        elif i == period:
            avg_gain = sum(gains[:period]) / period
            avg_loss = sum(losses[:period]) / period
            if avg_loss == 0:
                result.append(100.0)
            else:
                rs = avg_gain / avg_loss
                result.append(100.0 - (100.0 / (1.0 + rs)))
        else:
            idx = i - 1
            prev_avg_gain = 0.0
            prev_avg_loss = 0.0
            if result[i - 1] == 100.0:
                prev_avg_gain = 1.0
                prev_avg_loss = 0.0
            elif result[i - 1] == 0.0:
                prev_avg_gain = 0.0
                prev_avg_loss = 1.0
            else:
                prev_rs = (100.0 / (100.0 - result[i - 1])) - 1.0
                prev_avg_loss = 1.0
                prev_avg_gain = prev_rs
            avg_gain = (prev_avg_gain * (period - 1) + gains[idx]) / period
            avg_loss = (prev_avg_loss * (period - 1) + losses[idx]) / period
            if avg_loss == 0:
                result.append(100.0)
            else:
                rs = avg_gain / avg_loss
                result.append(100.0 - (100.0 / (1.0 + rs)))
    return result


def rsi_wilders(values: list[float], period: int = 14) -> list[float]:
    """Compute RSI using Wilder's smoothing method."""
    if len(values) < period + 1:
        return [50.0] * len(values)
    result = [50.0] * len(values)
    deltas = [0.0]
    for i in range(1, len(values)):
        deltas.append(values[i] - values[i - 1])
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [abs(d) if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - (100.0 / (1.0 + rs))
    return result


class PullbackV1(BaseStrategy):
    """Pullback Continuation Strategy."""

    def __init__(self, cooldown_minutes: int = 30, swing_lookback: int = 10) -> None:
        self._cooldown_minutes = cooldown_minutes
        self._swing_lookback = swing_lookback
        self._last_buy_time: dict[str, datetime] = {}
        self._prev_ltf_rsi: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "pullback_v1"

    @property
    def symbols(self) -> list[str]:
        return []

    async def on_market_data(
        self,
        snapshot: MarketSnapshot,
        context: StrategyContext,
    ) -> list[Signal]:
        symbol = snapshot.symbol
        now = snapshot.timestamp

        universe = context.metadata.get("universe_top20")
        if not universe or not isinstance(universe, list):
            return []
        if symbol not in universe:
            return []

        if not context.is_market_open:
            return []

        htf_key = "60m"
        ltf_key = "5m"
        htf_candles = snapshot.historical_candles.get(htf_key, [])
        ltf_candles = snapshot.historical_candles.get(ltf_key, [])
        if not ltf_candles:
            ltf_key = "1m"
            ltf_candles = snapshot.historical_candles.get(ltf_key, [])

        htf_closes = _extract_closes(htf_candles)
        htf_highs = _extract_highs(htf_candles)
        htf_lows = _extract_lows(htf_candles)
        htf_opens = _extract_opens(htf_candles)

        ltf_closes = _extract_closes(ltf_candles)
        ltf_highs = _extract_highs(ltf_candles)

        min_htf = 200
        min_ltf = max(20, self._swing_lookback + 2)

        if len(htf_closes) < min_htf or len(ltf_closes) < min_ltf:
            return [self._make_signal(
                symbol, now, "HOLD", 0.0, "insufficient_data",
                {"htf_candles": len(htf_closes), "ltf_candles": len(ltf_closes)},
            )]

        htf_ema20 = ema(htf_closes, 20)
        htf_ema50 = ema(htf_closes, 50)
        htf_ema200 = ema(htf_closes, 200)
        htf_rsi14 = rsi_wilders(htf_closes, 14)

        ltf_ema20 = ema(ltf_closes, 20)
        ltf_rsi14 = rsi_wilders(ltf_closes, 14)

        htf_ema20_last = htf_ema20[-1] if htf_ema20 else 0.0
        htf_ema50_last = htf_ema50[-1] if htf_ema50 else 0.0
        htf_ema200_last = htf_ema200[-1] if htf_ema200 else 0.0
        htf_rsi14_last = htf_rsi14[-1] if htf_rsi14 else 50.0
        htf_close_last = htf_closes[-1] if htf_closes else 0.0

        ltf_ema20_last = ltf_ema20[-1] if ltf_ema20 else 0.0
        ltf_rsi14_last = ltf_rsi14[-1] if ltf_rsi14 else 50.0
        ltf_rsi14_prev = ltf_rsi14[-2] if len(ltf_rsi14) >= 2 else 50.0
        ltf_close_last = ltf_closes[-1] if ltf_closes else 0.0

        swing_start = max(0, len(ltf_highs) - self._swing_lookback - 1)
        swing_end = len(ltf_highs) - 1
        swing_highs = ltf_highs[swing_start:swing_end] if swing_end > swing_start else []
        swing_high = max(swing_highs) if swing_highs else ltf_close_last

        cooldown_active = False
        last_buy = self._last_buy_time.get(symbol)
        if last_buy:
            elapsed = (now - last_buy).total_seconds() / 60.0
            if elapsed < self._cooldown_minutes:
                cooldown_active = True

        base_metadata: dict[str, Any] = {
            "htf_ema20": round(htf_ema20_last, 2),
            "htf_ema50": round(htf_ema50_last, 2),
            "htf_ema200": round(htf_ema200_last, 2),
            "htf_rsi14": round(htf_rsi14_last, 2),
            "ltf_ema20": round(ltf_ema20_last, 2),
            "ltf_rsi14": round(ltf_rsi14_last, 2),
            "swing_high": round(swing_high, 2),
            "htf": htf_key,
            "ltf": ltf_key,
            "cooldown_active": cooldown_active,
        }

        if htf_ema50_last <= 0 or htf_ema200_last <= 0:
            return [self._make_signal(symbol, now, "HOLD", 0.0, "invalid_ema", base_metadata)]

        trend_ok = htf_ema50_last > htf_ema200_last and htf_rsi14_last >= 40.0
        if not trend_ok:
            return [self._make_signal(
                symbol, now, "HOLD", 0.0, "trend_filter_fail",
                {**base_metadata, "trend_ema50_gt_ema200": htf_ema50_last > htf_ema200_last, "trend_rsi_ok": htf_rsi14_last >= 40.0},
            )]

        ema_band_low = min(htf_ema20_last, htf_ema50_last)
        ema_band_high = max(htf_ema20_last, htf_ema50_last)
        band_tolerance = 0.01 * ema_band_high
        in_pullback_zone = (ema_band_low - band_tolerance) <= htf_close_last <= (ema_band_high + band_tolerance)

        collapse = False
        if len(htf_closes) >= 3 and len(htf_opens) >= 3 and len(htf_highs) >= 3 and len(htf_lows) >= 3:
            c1_bearish = htf_closes[-1] < htf_opens[-1]
            c2_bearish = htf_closes[-2] < htf_opens[-2]
            range_curr = htf_highs[-1] - htf_lows[-1]
            range_prev = htf_highs[-2] - htf_lows[-2]
            range_prev2 = htf_highs[-3] - htf_lows[-3]
            expanding = range_curr > range_prev > range_prev2
            if c1_bearish and c2_bearish and expanding:
                collapse = True

        pullback_ok = in_pullback_zone and not collapse
        if not pullback_ok:
            return [self._make_signal(
                symbol, now, "HOLD", 0.0, "no_pullback",
                {**base_metadata, "in_zone": in_pullback_zone, "collapse": collapse},
            )]

        exit_rsi_cross_down = ltf_rsi14_prev >= 50.0 and ltf_rsi14_last < 50.0
        exit_below_ema = ltf_close_last < ltf_ema20_last
        exit_trigger = exit_rsi_cross_down or exit_below_ema

        if exit_trigger:
            return [self._make_signal(
                symbol, now, "SELL", 0.6, "exit_trigger",
                {**base_metadata, "rsi_cross_down": exit_rsi_cross_down, "below_ema": exit_below_ema},
            )]

        entry_rsi_cross_up = ltf_rsi14_prev < 40.0 and ltf_rsi14_last >= 40.0
        entry_above_ema = ltf_close_last > ltf_ema20_last
        entry_break_swing = ltf_close_last > swing_high
        entry_trigger = entry_rsi_cross_up and entry_above_ema and entry_break_swing

        if entry_trigger and not cooldown_active:
            confidence = 0.6
            if htf_ema200_last > 0 and (htf_ema50_last / htf_ema200_last) > 1.02:
                confidence += 0.1
            if htf_rsi14_last >= 50.0:
                confidence += 0.1
            confidence = max(0.0, min(1.0, confidence))

            self._last_buy_time[symbol] = now

            return [self._make_signal(
                symbol, now, "BUY", confidence, "entry_trigger",
                {**base_metadata, "rsi_cross_up": entry_rsi_cross_up, "above_ema": entry_above_ema, "break_swing": entry_break_swing},
            )]

        return [self._make_signal(symbol, now, "HOLD", 0.0, "hold", base_metadata)]

    def _make_signal(
        self,
        symbol: str,
        timestamp: datetime,
        action: str,
        confidence: float,
        reason: str,
        metadata: dict[str, Any],
    ) -> Signal:
        return Signal(
            signal_id=str(uuid4()),
            strategy_name=self.name,
            symbol=symbol,
            action=action,  # type: ignore
            confidence=confidence,
            reason=reason,
            suggested_quantity=None,
            metadata=metadata,
            timestamp=timestamp,
        )
