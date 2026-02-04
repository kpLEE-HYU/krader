"""Event dataclasses for the trading system."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from krader.execution.order import Order
    from krader.market.types import Candle, Tick


@dataclass(frozen=True)
class Event:
    """Base event class."""

    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class MarketEvent(Event):
    """Market data event (tick or candle)."""

    symbol: str = ""
    event_type: Literal["tick", "candle"] = "tick"
    data: "Tick | Candle | None" = None


@dataclass(frozen=True)
class SignalEvent(Event):
    """Trading signal event."""

    signal_id: str = ""
    symbol: str = ""
    action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    confidence: float = 0.0
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderEvent(Event):
    """Order lifecycle event."""

    order_id: str = ""
    event_type: Literal["new", "partial", "filled", "canceled", "rejected"] = "new"
    order: "Order | None" = None


@dataclass(frozen=True)
class FillEvent(Event):
    """Order fill event."""

    fill_id: str = ""
    order_id: str = ""
    quantity: int = 0
    price: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass(frozen=True)
class ControlEvent(Event):
    """System control event."""

    command: Literal["pause", "resume", "shutdown", "kill"] = "pause"


@dataclass(frozen=True)
class ErrorEvent(Event):
    """Error notification event."""

    error_type: str = ""  # Category: "broker_connection", "tick_validation", etc.
    message: str = ""
    severity: Literal["warning", "error", "critical"] = "error"
    context: dict[str, Any] = field(default_factory=dict)
