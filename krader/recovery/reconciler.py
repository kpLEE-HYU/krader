"""Startup reconciliation with broker state."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from krader.broker.base import BaseBroker
from krader.execution.order import Order, OrderStatus
from krader.persistence.repository import Repository
from krader.risk.portfolio import PortfolioTracker

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Result of reconciliation process."""

    success: bool
    run_id: str
    positions_synced: int = 0
    orders_updated: int = 0
    orders_canceled: int = 0
    discrepancies: list[str] = field(default_factory=list)
    error: str | None = None


class Reconciler:
    """Handles startup reconciliation with broker state."""

    def __init__(
        self,
        broker: BaseBroker,
        repository: Repository,
        portfolio_tracker: PortfolioTracker,
    ) -> None:
        self._broker = broker
        self._repo = repository
        self._portfolio_tracker = portfolio_tracker
        self._run_id: str | None = None

    @property
    def run_id(self) -> str | None:
        """Get current run ID."""
        return self._run_id

    async def reconcile(self) -> ReconciliationResult:
        """
        Perform full reconciliation on startup.

        Sequence:
        1. Mark previous unclean runs as ended
        2. Fetch positions from broker
        3. Fetch open orders from broker
        4. Compare with local state
        5. Reconcile differences (broker wins)
        6. Create new bot run entry
        """
        self._run_id = f"RUN-{uuid.uuid4().hex[:12]}"
        result = ReconciliationResult(success=False, run_id=self._run_id)

        try:
            await self._cleanup_previous_runs()

            # Create bot_run record first so errors can reference it
            await self._repo.start_bot_run(self._run_id)

            if not self._broker.is_connected:
                result.error = "Broker not connected"
                return result

            broker_positions = await self._broker.fetch_positions()
            broker_balance = await self._broker.fetch_balance()

            await self._portfolio_tracker.sync_with_broker(
                broker_positions, broker_balance
            )
            result.positions_synced = len(broker_positions)

            broker_orders = await self._broker.fetch_open_orders()
            orders_updated, orders_canceled = await self._reconcile_orders(broker_orders)
            result.orders_updated = orders_updated
            result.orders_canceled = orders_canceled

            result.success = True
            logger.info(
                "Reconciliation complete: run=%s, positions=%d, orders_updated=%d",
                self._run_id,
                result.positions_synced,
                result.orders_updated,
            )

        except Exception as e:
            result.error = str(e)
            logger.error("Reconciliation failed: %s", e)
            await self._repo.log_error(
                self._run_id,
                "RECONCILIATION_ERROR",
                str(e),
            )

        return result

    async def _cleanup_previous_runs(self) -> None:
        """Mark any unfinished runs as ended with error."""
        unfinished = await self._repo.get_unfinished_bot_runs()

        for run in unfinished:
            await self._repo.end_bot_run(
                run["run_id"],
                "CRASHED",
                "Unclean shutdown detected during reconciliation",
            )
            logger.warning("Marked previous run as crashed: %s", run["run_id"])

    async def _reconcile_orders(
        self, broker_orders: list[dict]
    ) -> tuple[int, int]:
        """
        Reconcile local orders with broker orders.

        Returns:
            Tuple of (updated_count, canceled_count)
        """
        updated = 0
        canceled = 0

        broker_order_ids = {o["broker_order_id"] for o in broker_orders}

        local_open_orders = await self._repo.get_open_orders()

        for local_order in local_open_orders:
            broker_id = local_order.get("broker_order_id")

            if broker_id and broker_id not in broker_order_ids:
                order = self._order_from_dict(local_order)

                if order.filled_quantity > 0:
                    order.status = OrderStatus.FILLED
                else:
                    order.status = OrderStatus.CANCELED

                order.updated_at = datetime.now()
                await self._repo.update_order(order)
                canceled += 1

                logger.info(
                    "Order reconciled as %s: %s (broker_id=%s)",
                    order.status.value,
                    order.order_id,
                    broker_id,
                )

        for broker_order in broker_orders:
            local = await self._repo.get_order_by_broker_id(
                broker_order["broker_order_id"]
            )

            if local:
                changed = False
                order = self._order_from_dict(local)

                if broker_order["filled_quantity"] != order.filled_quantity:
                    order.filled_quantity = broker_order["filled_quantity"]
                    changed = True

                if changed:
                    order.updated_at = datetime.now()
                    await self._repo.update_order(order)
                    updated += 1

                    logger.info(
                        "Order updated from broker: %s, filled=%d",
                        order.order_id,
                        order.filled_quantity,
                    )
            else:
                logger.warning(
                    "Unknown broker order found: %s",
                    broker_order["broker_order_id"],
                )

        return updated, canceled

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

    async def end_run(self, status: str = "COMPLETED", error: str | None = None) -> None:
        """End the current run."""
        if self._run_id:
            await self._repo.end_bot_run(self._run_id, status, error)
            logger.info("Bot run ended: %s (%s)", self._run_id, status)
