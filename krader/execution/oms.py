"""Order Management System for trade execution."""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from krader.broker.base import BaseBroker
from krader.broker.errors import BrokerError, OrderRejectedError
from krader.events import EventBus, FillEvent, OrderEvent
from krader.execution.idempotency import generate_fill_id, generate_idempotency_key
from krader.execution.order import Order, OrderStatus
from krader.persistence.repository import Repository

if TYPE_CHECKING:
    from krader.strategy.signal import Signal

logger = logging.getLogger(__name__)


class OrderManagementSystem:
    """Manages order lifecycle from signal to fill."""

    def __init__(
        self,
        broker: BaseBroker,
        repository: Repository,
        event_bus: EventBus,
    ) -> None:
        self._broker = broker
        self._repo = repository
        self._event_bus = event_bus
        self._active_orders: dict[str, Order] = {}
        self._paused = False

    @property
    def is_paused(self) -> bool:
        """Check if OMS is paused."""
        return self._paused

    def pause(self) -> None:
        """Pause order processing (new signals rejected)."""
        self._paused = True
        logger.warning("OMS paused - new signals will be rejected")

    def resume(self) -> None:
        """Resume order processing."""
        self._paused = False
        logger.info("OMS resumed")

    async def load_active_orders(self) -> None:
        """Load active orders from database on startup."""
        open_orders = await self._repo.get_open_orders()
        for order_data in open_orders:
            order = self._order_from_dict(order_data)
            self._active_orders[order.order_id] = order
        logger.info("Loaded %d active orders", len(self._active_orders))

    def _order_from_dict(self, data: dict) -> Order:
        """Create Order from database row."""
        return Order(
            order_id=data["order_id"],
            signal_id=data["signal_id"],
            symbol=data["symbol"],
            side=data["side"],
            order_type=data["order_type"],
            quantity=data["quantity"],
            filled_quantity=data["filled_quantity"],
            price=Decimal(str(data["price"])) if data["price"] else None,
            broker_order_id=data["broker_order_id"],
            status=OrderStatus(data["status"]),
            reject_reason=data["reject_reason"],
            created_at=datetime.fromtimestamp(data["created_at"]),
            updated_at=datetime.fromtimestamp(data["updated_at"]),
        )

    async def process_approved_signal(
        self,
        signal: "Signal",
        approved_quantity: int,
        price: Decimal | None = None,
    ) -> Order | None:
        """
        Process an approved signal by creating and submitting an order.

        Args:
            signal: The trading signal
            approved_quantity: Quantity approved by risk validation
            price: Optional limit price

        Returns:
            The created order, or None if rejected/skipped
        """
        if self._paused:
            logger.warning("OMS paused, rejecting signal %s", signal.signal_id)
            return None

        if signal.action == "HOLD":
            logger.debug("Ignoring HOLD signal %s", signal.signal_id)
            return None

        idempotency_key = generate_idempotency_key(signal, approved_quantity)

        existing = await self._repo.get_order(idempotency_key)
        if existing:
            existing_order = self._order_from_dict(existing)
            if not existing_order.is_terminal:
                logger.info(
                    "Order already in flight: %s (status=%s)",
                    idempotency_key,
                    existing_order.status.value,
                )
                return existing_order
            else:
                idempotency_key = f"{idempotency_key}-{uuid.uuid4().hex[:8]}"

        order = Order(
            order_id=idempotency_key,
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.action,  # type: ignore
            order_type="LIMIT" if price else "MARKET",
            quantity=approved_quantity,
            price=price,
        )

        await self._repo.save_order(order)
        self._active_orders[order.order_id] = order

        await self._event_bus.publish(
            OrderEvent(
                order_id=order.order_id,
                event_type="new",
                order=order,
            )
        )

        try:
            broker_order_id = await self._broker.place_order(order)
            order.mark_submitted(broker_order_id)
            logger.info(
                "Order submitted: %s -> broker:%s",
                order.order_id,
                broker_order_id,
            )

        except OrderRejectedError as e:
            order.mark_rejected(str(e))
            logger.warning("Order rejected: %s - %s", order.order_id, e)
            await self._event_bus.publish(
                OrderEvent(
                    order_id=order.order_id,
                    event_type="rejected",
                    order=order,
                )
            )

        except BrokerError as e:
            order.mark_rejected(f"Broker error: {e}")
            logger.error("Broker error for order %s: %s", order.order_id, e)
            await self._event_bus.publish(
                OrderEvent(
                    order_id=order.order_id,
                    event_type="rejected",
                    order=order,
                )
            )

        await self._repo.update_order(order)

        if order.is_terminal:
            self._active_orders.pop(order.order_id, None)

        return order

    async def handle_fill(
        self,
        broker_order_id: str,
        quantity: int,
        price: Decimal,
        broker_fill_id: str | None = None,
        commission: Decimal | None = None,
    ) -> None:
        """
        Handle a fill notification from the broker.

        Args:
            broker_order_id: The broker's order ID
            quantity: Fill quantity
            price: Fill price
            broker_fill_id: Optional broker fill ID
            commission: Optional commission
        """
        order = self._find_order_by_broker_id(broker_order_id)
        if not order:
            order_data = await self._repo.get_order_by_broker_id(broker_order_id)
            if order_data:
                order = self._order_from_dict(order_data)
            else:
                logger.warning("Unknown order for fill: %s", broker_order_id)
                return

        fills = await self._repo.get_fills_for_order(order.order_id)
        fill_sequence = len(fills) + 1
        fill_id = generate_fill_id(order.order_id, fill_sequence)

        await self._repo.save_fill(
            fill_id=fill_id,
            order_id=order.order_id,
            quantity=quantity,
            price=price,
            broker_fill_id=broker_fill_id,
            commission=commission,
        )

        old_status = order.status
        order.apply_fill(quantity)
        await self._repo.update_order(order)

        await self._event_bus.publish(
            FillEvent(
                fill_id=fill_id,
                order_id=order.order_id,
                quantity=quantity,
                price=price,
            )
        )

        event_type = "filled" if order.status == OrderStatus.FILLED else "partial"
        await self._event_bus.publish(
            OrderEvent(
                order_id=order.order_id,
                event_type=event_type,
                order=order,
            )
        )

        logger.info(
            "Fill applied: order=%s, qty=%d@%s, status=%s->%s",
            order.order_id,
            quantity,
            price,
            old_status.value,
            order.status.value,
        )

        if order.is_terminal:
            self._active_orders.pop(order.order_id, None)
        else:
            self._active_orders[order.order_id] = order

    async def handle_cancel(self, broker_order_id: str) -> None:
        """Handle a cancel confirmation from the broker."""
        order = self._find_order_by_broker_id(broker_order_id)
        if not order:
            order_data = await self._repo.get_order_by_broker_id(broker_order_id)
            if order_data:
                order = self._order_from_dict(order_data)
            else:
                logger.warning("Unknown order for cancel: %s", broker_order_id)
                return

        if order.is_terminal:
            logger.debug("Order already terminal: %s", order.order_id)
            return

        order.mark_canceled()
        await self._repo.update_order(order)

        await self._event_bus.publish(
            OrderEvent(
                order_id=order.order_id,
                event_type="canceled",
                order=order,
            )
        )

        self._active_orders.pop(order.order_id, None)
        logger.info("Order canceled: %s", order.order_id)

    async def cancel_order(self, order_id: str) -> bool:
        """
        Request cancellation of an order.

        Args:
            order_id: The internal order ID

        Returns:
            True if cancel request was sent successfully
        """
        order = self._active_orders.get(order_id)
        if not order:
            logger.warning("Cannot cancel unknown order: %s", order_id)
            return False

        if not order.broker_order_id:
            logger.warning("Order not yet submitted: %s", order_id)
            return False

        if order.is_terminal:
            logger.debug("Order already terminal: %s", order_id)
            return True

        try:
            success = await self._broker.cancel_order(order.broker_order_id)
            if success:
                logger.info("Cancel request sent: %s", order_id)
            return success
        except BrokerError as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
            return False

    async def cancel_all_orders(self) -> int:
        """
        Cancel all active orders.

        Returns:
            Number of orders for which cancel was requested
        """
        canceled = 0
        for order_id in list(self._active_orders.keys()):
            if await self.cancel_order(order_id):
                canceled += 1
        logger.warning("Cancellation requested for %d orders", canceled)
        return canceled

    def _find_order_by_broker_id(self, broker_order_id: str) -> Order | None:
        """Find an active order by broker order ID."""
        for order in self._active_orders.values():
            if order.broker_order_id == broker_order_id:
                return order
        return None

    def get_active_orders(self) -> list[Order]:
        """Get all active orders."""
        return list(self._active_orders.values())

    def get_order(self, order_id: str) -> Order | None:
        """Get an order by ID from active orders."""
        return self._active_orders.get(order_id)
