"""Abstract broker interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, Coroutine, Any

if TYPE_CHECKING:
    from krader.execution.order import Order
    from krader.market.types import Tick


@dataclass
class Position:
    """Broker position representation."""

    symbol: str
    quantity: int
    avg_price: Decimal
    current_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None


@dataclass
class Balance:
    """Broker account balance."""

    total_equity: Decimal
    available_cash: Decimal
    margin_used: Decimal = field(default_factory=lambda: Decimal("0"))
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))


TickCallback = Callable[["Tick"], Coroutine[Any, Any, None]]
ErrorCallback = Callable[[str, str, str, dict], Coroutine[Any, Any, None]]  # error_type, message, severity, context


class BaseBroker(ABC):
    """Abstract broker interface for trading operations."""

    _error_callback: ErrorCallback | None = None

    def set_error_callback(self, callback: ErrorCallback) -> None:
        """Set callback for error reporting."""
        self._error_callback = callback

    async def _report_error(
        self,
        error_type: str,
        message: str,
        severity: str = "error",
        context: dict | None = None,
    ) -> None:
        """Report an error through callback if set."""
        if self._error_callback:
            await self._error_callback(error_type, message, severity, context or {})
    """Abstract broker interface for trading operations."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the broker and authenticate."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the broker."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to broker."""
        pass

    @abstractmethod
    async def place_order(self, order: "Order") -> str:
        """
        Place an order with the broker.

        Returns:
            The broker's order ID.

        Raises:
            OrderRejectedError: If the order is rejected.
            InsufficientFundsError: If there are insufficient funds.
            ConnectionError: If not connected to broker.
        """
        pass

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """
        Cancel an order.

        Returns:
            True if cancellation was successful.
        """
        pass

    @abstractmethod
    async def amend_order(
        self, broker_order_id: str, quantity: int | None = None, price: Decimal | None = None
    ) -> bool:
        """
        Amend an existing order.

        Returns:
            True if amendment was successful.
        """
        pass

    @abstractmethod
    async def fetch_positions(self) -> list[Position]:
        """Fetch all current positions from the broker."""
        pass

    @abstractmethod
    async def fetch_open_orders(self) -> list[dict]:
        """Fetch all open orders from the broker."""
        pass

    @abstractmethod
    async def fetch_balance(self) -> Balance:
        """Fetch account balance from the broker."""
        pass

    @abstractmethod
    async def subscribe_market_data(
        self, symbols: list[str], callback: TickCallback
    ) -> None:
        """Subscribe to real-time market data for symbols."""
        pass

    @abstractmethod
    async def unsubscribe_market_data(self, symbols: list[str]) -> None:
        """Unsubscribe from real-time market data."""
        pass
