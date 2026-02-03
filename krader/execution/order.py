"""Order dataclass and state machine."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal


class OrderStatus(Enum):
    """Order lifecycle states."""

    PENDING_NEW = "PENDING_NEW"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"

    @property
    def is_terminal(self) -> bool:
        """Check if this is a terminal state."""
        return self in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
        )

    @property
    def is_active(self) -> bool:
        """Check if order is still active/working."""
        return self in (
            OrderStatus.PENDING_NEW,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIAL_FILL,
        )


# Valid state transitions
VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING_NEW: {
        OrderStatus.SUBMITTED,
        OrderStatus.REJECTED,
    },
    OrderStatus.SUBMITTED: {
        OrderStatus.PARTIAL_FILL,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    },
    OrderStatus.PARTIAL_FILL: {
        OrderStatus.PARTIAL_FILL,  # Can receive multiple partial fills
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
    },
    OrderStatus.FILLED: set(),  # Terminal
    OrderStatus.CANCELED: set(),  # Terminal
    OrderStatus.REJECTED: set(),  # Terminal
}


@dataclass
class Order:
    """Order representation with state machine."""

    order_id: str
    signal_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    quantity: int
    price: Decimal | None = None
    broker_order_id: str | None = None
    filled_quantity: int = 0
    status: OrderStatus = OrderStatus.PENDING_NEW
    reject_reason: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_terminal(self) -> bool:
        """Check if order is in a terminal state."""
        return self.status.is_terminal

    @property
    def is_active(self) -> bool:
        """Check if order is still active/working."""
        return self.status.is_active

    @property
    def remaining_quantity(self) -> int:
        """Get unfilled quantity."""
        return self.quantity - self.filled_quantity

    def can_transition_to(self, new_status: OrderStatus) -> bool:
        """Check if transition to new status is valid."""
        return new_status in VALID_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status: OrderStatus) -> None:
        """Transition to a new status."""
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Invalid transition: {self.status.value} -> {new_status.value}"
            )
        self.status = new_status
        self.updated_at = datetime.now()

    def apply_fill(self, quantity: int) -> None:
        """Apply a fill to the order."""
        if quantity <= 0:
            raise ValueError("Fill quantity must be positive")

        if quantity > self.remaining_quantity:
            raise ValueError(
                f"Fill quantity {quantity} exceeds remaining {self.remaining_quantity}"
            )

        self.filled_quantity += quantity
        self.updated_at = datetime.now()

        if self.filled_quantity >= self.quantity:
            self.transition_to(OrderStatus.FILLED)
        elif self.status == OrderStatus.SUBMITTED:
            self.transition_to(OrderStatus.PARTIAL_FILL)

    def mark_rejected(self, reason: str) -> None:
        """Mark order as rejected."""
        self.reject_reason = reason
        self.transition_to(OrderStatus.REJECTED)

    def mark_canceled(self) -> None:
        """Mark order as canceled."""
        self.transition_to(OrderStatus.CANCELED)

    def mark_submitted(self, broker_order_id: str) -> None:
        """Mark order as submitted to broker."""
        self.broker_order_id = broker_order_id
        self.transition_to(OrderStatus.SUBMITTED)
