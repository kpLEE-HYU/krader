"""Portfolio state tracker."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from krader.broker.base import Balance, Position
from krader.events import EventBus, FillEvent
from krader.persistence.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class PortfolioPosition:
    """Internal position representation."""

    symbol: str
    quantity: int
    avg_price: Decimal
    current_price: Decimal | None = None

    @property
    def market_value(self) -> Decimal | None:
        """Get current market value if price is available."""
        if self.current_price is None:
            return None
        return self.current_price * self.quantity

    @property
    def cost_basis(self) -> Decimal:
        """Get total cost basis."""
        return self.avg_price * self.quantity

    @property
    def unrealized_pnl(self) -> Decimal | None:
        """Get unrealized P&L if price is available."""
        if self.current_price is None:
            return None
        return (self.current_price - self.avg_price) * self.quantity


@dataclass
class Portfolio:
    """Portfolio state tracker."""

    positions: dict[str, PortfolioPosition] = field(default_factory=dict)
    cash: Decimal = field(default_factory=lambda: Decimal("0"))
    total_equity: Decimal = field(default_factory=lambda: Decimal("0"))
    daily_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    daily_start_equity: Decimal | None = None
    last_updated: datetime = field(default_factory=datetime.now)

    @property
    def total_position_value(self) -> Decimal:
        """Get total value of all positions."""
        total = Decimal("0")
        for pos in self.positions.values():
            if pos.market_value is not None:
                total += pos.market_value
        return total

    @property
    def exposure_pct(self) -> float:
        """Get portfolio exposure as percentage of equity."""
        if self.total_equity <= 0:
            return 0.0
        return float(self.total_position_value / self.total_equity)

    def get_position(self, symbol: str) -> PortfolioPosition | None:
        """Get position for a symbol."""
        return self.positions.get(symbol)

    def get_position_quantity(self, symbol: str) -> int:
        """Get position quantity for a symbol (0 if no position)."""
        pos = self.positions.get(symbol)
        return pos.quantity if pos else 0


class PortfolioTracker:
    """Tracks portfolio state from fills and broker updates."""

    def __init__(
        self,
        repository: Repository,
        event_bus: EventBus,
    ) -> None:
        self._repo = repository
        self._event_bus = event_bus
        self._portfolio = Portfolio()

        event_bus.subscribe(FillEvent, self._on_fill)

    @property
    def portfolio(self) -> Portfolio:
        """Get current portfolio state."""
        return self._portfolio

    async def initialize(self) -> None:
        """Load portfolio state from database."""
        positions = await self._repo.get_all_positions()

        for pos_data in positions:
            self._portfolio.positions[pos_data["symbol"]] = PortfolioPosition(
                symbol=pos_data["symbol"],
                quantity=pos_data["quantity"],
                avg_price=Decimal(str(pos_data["avg_price"])),
            )

        logger.info("Loaded %d positions from database", len(positions))

    async def sync_with_broker(
        self,
        positions: list[Position],
        balance: Balance,
    ) -> None:
        """Sync portfolio with broker state (broker is source of truth)."""
        self._portfolio.cash = balance.available_cash
        self._portfolio.total_equity = balance.total_equity

        broker_symbols = set()
        for pos in positions:
            broker_symbols.add(pos.symbol)
            self._portfolio.positions[pos.symbol] = PortfolioPosition(
                symbol=pos.symbol,
                quantity=pos.quantity,
                avg_price=pos.avg_price,
                current_price=pos.current_price,
            )
            await self._repo.save_position(pos.symbol, pos.quantity, pos.avg_price)

        for symbol in list(self._portfolio.positions.keys()):
            if symbol not in broker_symbols:
                del self._portfolio.positions[symbol]
                await self._repo.delete_position(symbol)

        self._portfolio.last_updated = datetime.now()
        logger.info(
            "Portfolio synced: %d positions, cash=%.2f, equity=%.2f",
            len(self._portfolio.positions),
            self._portfolio.cash,
            self._portfolio.total_equity,
        )

    async def _on_fill(self, event: FillEvent) -> None:
        """Update portfolio on fill."""
        order_data = await self._repo.get_order(event.order_id)
        if not order_data:
            logger.warning("Fill for unknown order: %s", event.order_id)
            return

        symbol = order_data["symbol"]
        side = order_data["side"]
        quantity = event.quantity
        price = event.price

        current_pos = self._portfolio.positions.get(symbol)

        if side == "BUY":
            if current_pos:
                new_qty = current_pos.quantity + quantity
                total_cost = (current_pos.avg_price * current_pos.quantity) + (price * quantity)
                new_avg_price = total_cost / new_qty
                current_pos.quantity = new_qty
                current_pos.avg_price = new_avg_price
            else:
                self._portfolio.positions[symbol] = PortfolioPosition(
                    symbol=symbol,
                    quantity=quantity,
                    avg_price=price,
                )

        elif side == "SELL":
            if current_pos:
                current_pos.quantity -= quantity
                if current_pos.quantity <= 0:
                    del self._portfolio.positions[symbol]
                    await self._repo.delete_position(symbol)
                    return

        pos = self._portfolio.positions.get(symbol)
        if pos:
            await self._repo.save_position(symbol, pos.quantity, pos.avg_price)

        self._portfolio.last_updated = datetime.now()
        logger.info(
            "Position updated from fill: %s %s %d @ %.2f",
            side,
            symbol,
            quantity,
            price,
        )

    def update_price(self, symbol: str, price: Decimal) -> None:
        """Update current price for a position."""
        pos = self._portfolio.positions.get(symbol)
        if pos:
            pos.current_price = price

    def reset_daily_pnl(self) -> None:
        """Reset daily P&L tracking (call at market open)."""
        self._portfolio.daily_start_equity = self._portfolio.total_equity
        self._portfolio.daily_pnl = Decimal("0")

    def calculate_daily_pnl(self) -> Decimal:
        """Calculate current daily P&L."""
        if self._portfolio.daily_start_equity is None:
            return Decimal("0")
        self._portfolio.daily_pnl = (
            self._portfolio.total_equity - self._portfolio.daily_start_equity
        )
        return self._portfolio.daily_pnl
