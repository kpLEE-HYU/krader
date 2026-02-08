"""Universe service for fetching top traded symbols."""

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from krader.broker.kiwoom import KiwoomBroker

logger = logging.getLogger(__name__)


class UniverseService:
    """Service for fetching and caching the trading universe."""

    def __init__(
        self,
        broker: "KiwoomBroker",
        cache_duration_minutes: int = 60,
        default_size: int = 20,
    ) -> None:
        self._broker = broker
        self._cache_duration = timedelta(minutes=cache_duration_minutes)
        self._default_size = default_size
        self._cache: list[str] = []
        self._cache_time: datetime | None = None

    @property
    def cached_universe(self) -> list[str]:
        """Get cached universe without refresh."""
        return self._cache.copy()

    def is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        if not self._cache or not self._cache_time:
            return False
        return datetime.now() - self._cache_time < self._cache_duration

    async def get_top_by_trading_value(
        self,
        size: int | None = None,
        market: str = "kospi",
        force_refresh: bool = False,
    ) -> list[str]:
        """
        Fetch top symbols by trading value.

        Args:
            size: Number of symbols to return (default: 20)
            market: Market to query - "kospi", "kosdaq", or "all"
            force_refresh: Force refresh even if cache is valid

        Returns:
            List of symbol codes sorted by trading value (descending)
        """
        if size is None:
            size = self._default_size

        if not force_refresh and self.is_cache_valid() and len(self._cache) >= size:
            return self._cache[:size]

        try:
            symbols = await self._fetch_top_traded(size, market)
            if symbols:
                self._cache = symbols
                self._cache_time = datetime.now()
                logger.info("Universe refreshed: %d symbols", len(symbols))
            return symbols[:size] if symbols else self._cache[:size]
        except Exception as e:
            logger.error("Failed to fetch universe: %s", e)
            return self._cache[:size] if self._cache else []

    async def _fetch_top_traded(self, size: int, market: str) -> list[str]:
        """Fetch top traded symbols from Kiwoom API."""
        market_code = {
            "all": "000",
            "kospi": "001",
            "kosdaq": "101",
        }.get(market, "001")

        symbols = await self._request_opt10030(market_code, size)
        return symbols

    async def _request_opt10030(self, market_code: str, size: int) -> list[str]:
        """
        Request opt10030 TR (거래량상위 - Top by Volume/Value).

        Uses the broker's TR request infrastructure which properly handles
        the Qt signal-based OnReceiveTrData callback.

        Input fields:
        - 시장구분: 000=전체, 001=코스피, 101=코스닥
        - 정렬구분: 1=거래량, 2=거래대금(trading value)

        Output fields (per row):
        - 종목코드: Symbol code
        - 종목명: Symbol name
        - 현재가: Current price
        - 거래량: Volume
        - 거래대금: Trading value
        """
        if not self._broker.is_connected:
            logger.warning("Broker not connected, cannot fetch universe")
            return []

        try:
            await self._broker.request_tr(
                tr_code="opt10030",
                rq_name="거래량상위",
                inputs={"시장구분": market_code, "정렬구분": "2"},
                screen_no="0301",
            )
        except Exception as e:
            logger.error("opt10030 request failed: %s", e)
            return []

        symbols = []
        fetch_size = min(size + 10, 100)
        for i in range(fetch_size):
            code = await self._broker.get_comm_data("opt10030", "거래량상위", i, "종목코드")
            if code:
                code = code.strip()
                if code and len(code) == 6 and code.isdigit():
                    symbols.append(code)
            if len(symbols) >= size:
                break

        logger.debug("Fetched %d symbols from opt10030", len(symbols))
        return symbols

    async def get_top_by_volume(
        self,
        size: int | None = None,
        market: str = "kospi",
    ) -> list[str]:
        """
        Fetch top symbols by trading volume.

        Args:
            size: Number of symbols to return
            market: Market to query

        Returns:
            List of symbol codes sorted by volume (descending)
        """
        if size is None:
            size = self._default_size

        try:
            symbols = await self._request_opt10030_volume(market, size)
            return symbols
        except Exception as e:
            logger.error("Failed to fetch by volume: %s", e)
            return []

    async def _request_opt10030_volume(self, market: str, size: int) -> list[str]:
        """Request opt10030 sorted by volume instead of value."""
        market_code = {
            "all": "000",
            "kospi": "001",
            "kosdaq": "101",
        }.get(market, "001")

        if not self._broker.is_connected:
            return []

        try:
            await self._broker.request_tr(
                tr_code="opt10030",
                rq_name="거래량상위_볼륨",
                inputs={"시장구분": market_code, "정렬구분": "1"},
                screen_no="0302",
            )
        except Exception as e:
            logger.error("opt10030 volume request failed: %s", e)
            return []

        symbols = []
        for i in range(size + 10):
            code = await self._broker.get_comm_data("opt10030", "거래량상위", i, "종목코드")
            if code:
                code = code.strip()
                if code and len(code) == 6 and code.isdigit():
                    symbols.append(code)
            if len(symbols) >= size:
                break

        return symbols

    def set_static_universe(self, symbols: list[str]) -> None:
        """
        Set a static universe (useful for testing or manual override).

        Args:
            symbols: List of symbol codes
        """
        self._cache = symbols.copy()
        self._cache_time = datetime.now()
        logger.info("Static universe set: %d symbols", len(symbols))

    def clear_cache(self) -> None:
        """Clear the universe cache."""
        self._cache = []
        self._cache_time = None


KOSPI_BLUE_CHIPS = [
    "005930",  # Samsung Electronics
    "000660",  # SK Hynix
    "373220",  # LG Energy Solution
    "207940",  # Samsung Biologics
    "005380",  # Hyundai Motor
    "006400",  # Samsung SDI
    "051910",  # LG Chem
    "035420",  # NAVER
    "000270",  # Kia
    "105560",  # KB Financial
    "055550",  # Shinhan Financial
    "035720",  # Kakao
    "003670",  # POSCO Holdings
    "068270",  # Celltrion
    "028260",  # Samsung C&T
    "012330",  # Hyundai Mobis
    "066570",  # LG Electronics
    "003550",  # LG
    "096770",  # SK Innovation
    "034730",  # SK
]


def get_default_universe() -> list[str]:
    """Get default universe (KOSPI blue chips) for fallback."""
    return KOSPI_BLUE_CHIPS.copy()
