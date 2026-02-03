"""Candle aggregation from ticks."""

import logging
from datetime import datetime, timedelta
from typing import Callable, Coroutine, Any

from krader.market.types import Candle, Tick

logger = logging.getLogger(__name__)

CandleCallback = Callable[[Candle], Coroutine[Any, Any, None]]


TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


def get_candle_open_time(timestamp: datetime, timeframe: str) -> datetime:
    """Calculate the candle open time for a given timestamp and timeframe."""
    minutes = TIMEFRAME_MINUTES.get(timeframe, 1)

    if minutes >= 1440:
        return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)

    total_minutes = timestamp.hour * 60 + timestamp.minute
    candle_minutes = (total_minutes // minutes) * minutes

    return timestamp.replace(
        hour=candle_minutes // 60,
        minute=candle_minutes % 60,
        second=0,
        microsecond=0,
    )


def get_candle_close_time(open_time: datetime, timeframe: str) -> datetime:
    """Calculate when a candle should close."""
    minutes = TIMEFRAME_MINUTES.get(timeframe, 1)
    return open_time + timedelta(minutes=minutes)


class CandleBuilder:
    """Builds candles from incoming ticks."""

    def __init__(
        self,
        timeframes: list[str] | None = None,
        on_candle_close: CandleCallback | None = None,
    ) -> None:
        """
        Initialize the candle builder.

        Args:
            timeframes: List of timeframes to build (default: 1m, 5m, 15m, 60m)
            on_candle_close: Callback when a candle closes
        """
        self._timeframes = timeframes or ["1m", "5m", "15m", "60m"]
        self._on_candle_close = on_candle_close

        self._current_candles: dict[str, dict[str, Candle]] = {}

    def _get_candle_key(self, symbol: str, timeframe: str) -> str:
        """Get cache key for a candle."""
        return f"{symbol}:{timeframe}"

    async def process_tick(self, tick: Tick) -> list[Candle]:
        """
        Process a tick and update candles.

        Args:
            tick: The incoming tick

        Returns:
            List of candles that closed due to this tick
        """
        closed_candles: list[Candle] = []

        if tick.symbol not in self._current_candles:
            self._current_candles[tick.symbol] = {}

        for timeframe in self._timeframes:
            candle = self._current_candles[tick.symbol].get(timeframe)
            candle_open_time = get_candle_open_time(tick.timestamp, timeframe)

            if candle is None:
                candle = Candle.from_tick(tick, timeframe, candle_open_time)
                self._current_candles[tick.symbol][timeframe] = candle
                logger.debug(
                    "New candle started: %s %s at %s",
                    tick.symbol,
                    timeframe,
                    candle_open_time,
                )

            elif candle.open_time != candle_open_time:
                closed_candles.append(candle)
                logger.debug(
                    "Candle closed: %s %s O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
                    candle.symbol,
                    candle.timeframe,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                )

                if self._on_candle_close:
                    await self._on_candle_close(candle)

                candle = Candle.from_tick(tick, timeframe, candle_open_time)
                self._current_candles[tick.symbol][timeframe] = candle

            else:
                candle.update_with_tick(tick)

        return closed_candles

    def get_current_candle(self, symbol: str, timeframe: str) -> Candle | None:
        """Get the current (incomplete) candle for a symbol and timeframe."""
        return self._current_candles.get(symbol, {}).get(timeframe)

    def get_all_current_candles(self, symbol: str) -> dict[str, Candle]:
        """Get all current candles for a symbol."""
        return dict(self._current_candles.get(symbol, {}))

    async def flush_all(self) -> list[Candle]:
        """
        Flush all current candles as closed.

        Returns:
            List of all flushed candles
        """
        flushed: list[Candle] = []

        for symbol_candles in self._current_candles.values():
            for candle in symbol_candles.values():
                flushed.append(candle)
                if self._on_candle_close:
                    await self._on_candle_close(candle)

        self._current_candles.clear()
        logger.info("Flushed %d candles", len(flushed))
        return flushed

    def clear(self, symbol: str | None = None) -> None:
        """Clear current candles."""
        if symbol:
            self._current_candles.pop(symbol, None)
        else:
            self._current_candles.clear()
