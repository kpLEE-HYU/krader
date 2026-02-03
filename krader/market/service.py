"""Market data service for managing subscriptions and candle building."""

import logging

from krader.broker.base import BaseBroker
from krader.events import EventBus, MarketEvent
from krader.market.candle import CandleBuilder
from krader.market.types import Candle, Tick
from krader.persistence.repository import Repository

logger = logging.getLogger(__name__)


class MarketDataService:
    """Manages market data subscriptions and candle aggregation."""

    def __init__(
        self,
        broker: BaseBroker,
        repository: Repository,
        event_bus: EventBus,
        timeframes: list[str] | None = None,
    ) -> None:
        self._broker = broker
        self._repo = repository
        self._event_bus = event_bus
        self._timeframes = timeframes or ["1m", "5m", "15m", "60m"]

        self._candle_builder = CandleBuilder(
            timeframes=self._timeframes,
            on_candle_close=self._on_candle_close,
        )

        self._subscribed_symbols: set[str] = set()

    async def _on_tick(self, tick: Tick) -> None:
        """Handle incoming tick from broker."""
        await self._event_bus.publish(
            MarketEvent(
                symbol=tick.symbol,
                event_type="tick",
                data=tick,
                timestamp=tick.timestamp,
            )
        )

        await self._candle_builder.process_tick(tick)

    async def _on_candle_close(self, candle: Candle) -> None:
        """Handle candle close."""
        await self._repo.save_candle(candle)

        await self._event_bus.publish(
            MarketEvent(
                symbol=candle.symbol,
                event_type="candle",
                data=candle,
                timestamp=candle.open_time,
            )
        )

        logger.debug(
            "Candle closed and saved: %s %s C=%.2f V=%d",
            candle.symbol,
            candle.timeframe,
            candle.close,
            candle.volume,
        )

    async def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to market data for symbols."""
        new_symbols = [s for s in symbols if s not in self._subscribed_symbols]

        if not new_symbols:
            return

        await self._broker.subscribe_market_data(new_symbols, self._on_tick)
        self._subscribed_symbols.update(new_symbols)

        logger.info("Subscribed to market data: %s", new_symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        """Unsubscribe from market data for symbols."""
        existing_symbols = [s for s in symbols if s in self._subscribed_symbols]

        if not existing_symbols:
            return

        await self._broker.unsubscribe_market_data(existing_symbols)
        self._subscribed_symbols.difference_update(existing_symbols)

        for symbol in existing_symbols:
            self._candle_builder.clear(symbol)

        logger.info("Unsubscribed from market data: %s", existing_symbols)

    async def unsubscribe_all(self) -> None:
        """Unsubscribe from all market data."""
        if self._subscribed_symbols:
            await self.unsubscribe(list(self._subscribed_symbols))

    def get_current_candle(self, symbol: str, timeframe: str) -> Candle | None:
        """Get the current (incomplete) candle."""
        return self._candle_builder.get_current_candle(symbol, timeframe)

    def get_all_current_candles(self, symbol: str) -> dict[str, Candle]:
        """Get all current candles for a symbol."""
        return self._candle_builder.get_all_current_candles(symbol)

    async def get_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[dict]:
        """Get historical candles from database."""
        return await self._repo.get_candles(symbol, timeframe, limit)

    @property
    def subscribed_symbols(self) -> set[str]:
        """Get currently subscribed symbols."""
        return self._subscribed_symbols.copy()

    async def shutdown(self) -> None:
        """Shutdown the market data service."""
        await self.unsubscribe_all()
        await self._candle_builder.flush_all()
        logger.info("Market data service shutdown complete")
