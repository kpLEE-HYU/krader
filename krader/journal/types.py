"""Data structures for trading journal entries."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class FillEntry:
    """A single fill within an order."""

    fill_id: str
    quantity: int
    price: Decimal
    commission: Decimal | None
    filled_at: datetime


@dataclass
class CandleSnapshot:
    """Candle data for journal display."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass
class TradeEntry:
    """A complete trade record for journal display."""

    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    order_type: str  # "MARKET" or "LIMIT"
    quantity: int
    created_at: datetime
    strategy_name: str
    confidence: float
    reason: str
    fills: list[FillEntry] = field(default_factory=list)
    candles_before: list[CandleSnapshot] = field(default_factory=list)
    candles_after: list[CandleSnapshot] = field(default_factory=list)

    @property
    def avg_fill_price(self) -> Decimal:
        """Calculate average fill price weighted by quantity."""
        if not self.fills:
            return Decimal("0")
        total_value = sum(f.price * f.quantity for f in self.fills)
        total_qty = sum(f.quantity for f in self.fills)
        if total_qty == 0:
            return Decimal("0")
        return total_value / total_qty

    @property
    def total_commission(self) -> Decimal:
        """Sum of all fill commissions."""
        return sum(
            (f.commission for f in self.fills if f.commission is not None),
            Decimal("0"),
        )

    @property
    def notional_value(self) -> Decimal:
        """Total notional value (avg_price * quantity)."""
        return self.avg_fill_price * self.quantity


@dataclass
class DailySummary:
    """Aggregated daily trading summary."""

    total_trades: int
    buy_count: int
    sell_count: int
    total_commission: Decimal
    symbols_traded: list[str]
    strategy_name: str


@dataclass
class TradeJournal:
    """Complete daily journal."""

    date: datetime
    summary: DailySummary
    trades: list[TradeEntry]
    portfolio_equity: Decimal
    portfolio_cash: Decimal
