"""Journal generation service."""

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from krader.journal.types import (
    CandleSnapshot,
    DailySummary,
    FillEntry,
    TradeEntry,
    TradeJournal,
)
from krader.journal.writer import JournalWriter
from krader.persistence.repository import Repository

logger = logging.getLogger(__name__)


class JournalService:
    """Generates daily trading journals from DB data."""

    def __init__(
        self,
        repo: Repository,
        journal_dir: Path,
        strategy_name: str = "",
    ) -> None:
        self._repo = repo
        self._journal_dir = journal_dir
        self._strategy_name = strategy_name
        self._writer = JournalWriter()
        self._generated_today = False

    @property
    def generated_today(self) -> bool:
        return self._generated_today

    async def generate_journal(
        self,
        date: datetime,
        portfolio_equity: Decimal,
        portfolio_cash: Decimal,
    ) -> Path | None:
        """Generate a daily journal for the given date.

        Returns the output path if a journal was written, None if no trades
        or already generated today.
        """
        if self._generated_today:
            logger.debug("Journal already generated today, skipping")
            return None

        orders = await self._get_orders_for_date(date)
        if not orders:
            logger.info("No trades today, skipping journal generation")
            self._generated_today = True
            return None

        trades: list[TradeEntry] = []
        for order in orders:
            trade = await self._build_trade_entry(order)
            trades.append(trade)

        summary = self._build_summary(trades)

        journal = TradeJournal(
            date=date,
            summary=summary,
            trades=trades,
            portfolio_equity=portfolio_equity,
            portfolio_cash=portfolio_cash,
        )

        date_str = date.strftime("%Y-%m-%d")
        output_path = self._journal_dir / f"{date_str}.md"

        result = self._writer.write(journal, output_path)
        self._generated_today = True
        logger.info("Journal written to %s (%d trades)", result, len(trades))
        return result

    async def _get_orders_for_date(self, date: datetime) -> list[dict]:
        """Get all orders for a specific date."""
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        start_ts = int(day_start.timestamp())
        end_ts = int(day_end.timestamp()) + 1
        return await self._repo.get_orders_for_date(start_ts, end_ts)

    async def _build_trade_entry(self, order: dict) -> TradeEntry:
        """Build a TradeEntry from an order dict with signal, fills, and candle context."""
        # Signal info
        signal_id = order.get("signal_id", "")
        strategy_name = self._strategy_name
        confidence = 0.0
        reason = ""

        if signal_id:
            signal = await self._repo.get_signal(signal_id)
            if signal:
                strategy_name = signal.get("strategy_name", strategy_name)
                confidence = signal.get("confidence", 0.0)
                reason = signal.get("reason", "")

        # Fills
        fill_rows = await self._repo.get_fills_for_order(order["order_id"])
        fills = [
            FillEntry(
                fill_id=f["fill_id"],
                quantity=f["quantity"],
                price=Decimal(str(f["price"])),
                commission=Decimal(str(f["commission"])) if f.get("commission") else None,
                filled_at=datetime.fromtimestamp(f["filled_at"]),
            )
            for f in fill_rows
        ]

        # Created-at time for candle context
        created_at = datetime.fromtimestamp(order["created_at"])
        symbol = order["symbol"]

        # Candles before entry (1m, up to 10)
        candle_rows_before = await self._repo.get_candles(
            symbol, "1m", limit=10, before=created_at
        )
        # get_candles returns DESC order, reverse for chronological
        candles_before = [
            _candle_snapshot(c) for c in reversed(candle_rows_before)
        ]

        # Candles after entry (5m, up to 6)
        candle_rows_after = await self._repo.get_candles_after(
            symbol, "5m", limit=6, after=created_at
        )
        candles_after = [_candle_snapshot(c) for c in candle_rows_after]

        return TradeEntry(
            order_id=order["order_id"],
            symbol=symbol,
            side=order["side"],
            order_type=order["order_type"],
            quantity=order["quantity"],
            created_at=created_at,
            strategy_name=strategy_name,
            confidence=confidence,
            reason=reason,
            fills=fills,
            candles_before=candles_before,
            candles_after=candles_after,
        )

    def _build_summary(self, trades: list[TradeEntry]) -> DailySummary:
        """Aggregate trades into a daily summary."""
        buy_count = sum(1 for t in trades if t.side == "BUY")
        sell_count = sum(1 for t in trades if t.side == "SELL")
        total_commission = sum(t.total_commission for t in trades)
        symbols = list(dict.fromkeys(t.symbol for t in trades))  # unique, ordered

        # Use most common strategy name
        strategy_names = [t.strategy_name for t in trades if t.strategy_name]
        strategy_name = strategy_names[0] if strategy_names else self._strategy_name

        return DailySummary(
            total_trades=len(trades),
            buy_count=buy_count,
            sell_count=sell_count,
            total_commission=total_commission,
            symbols_traded=symbols,
            strategy_name=strategy_name,
        )


def _candle_snapshot(row: dict) -> CandleSnapshot:
    """Convert a candle DB row to CandleSnapshot."""
    return CandleSnapshot(
        open_time=datetime.fromtimestamp(row["open_time"]),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        volume=row["volume"],
    )
