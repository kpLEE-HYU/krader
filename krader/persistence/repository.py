"""Data access layer for trading entities."""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from krader.persistence.database import Database

if TYPE_CHECKING:
    from krader.execution.order import Order, OrderStatus
    from krader.market.types import Candle
    from krader.strategy.signal import Signal

logger = logging.getLogger(__name__)


class Repository:
    """Data access layer for all trading entities."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # --- Candle operations ---

    async def save_candle(self, candle: "Candle") -> None:
        """Save or update a candle."""
        await self._db.execute(
            """
            INSERT OR REPLACE INTO candles
            (symbol, timeframe, open_time, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candle.symbol,
                candle.timeframe,
                int(candle.open_time.timestamp()),
                float(candle.open),
                float(candle.high),
                float(candle.low),
                float(candle.close),
                candle.volume,
            ),
        )
        await self._db.commit()

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        before: datetime | None = None,
    ) -> list[dict]:
        """Get recent candles for a symbol and timeframe."""
        if before:
            rows = await self._db.fetchall(
                """
                SELECT * FROM candles
                WHERE symbol = ? AND timeframe = ? AND open_time < ?
                ORDER BY open_time DESC LIMIT ?
                """,
                (symbol, timeframe, int(before.timestamp()), limit),
            )
        else:
            rows = await self._db.fetchall(
                """
                SELECT * FROM candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY open_time DESC LIMIT ?
                """,
                (symbol, timeframe, limit),
            )
        return [dict(row) for row in rows]

    # --- Signal operations ---

    async def save_signal(self, signal: "Signal") -> None:
        """Save a trading signal."""
        await self._db.execute(
            """
            INSERT INTO signals
            (signal_id, strategy_name, symbol, action, confidence, reason,
             suggested_quantity, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.signal_id,
                signal.strategy_name,
                signal.symbol,
                signal.action,
                signal.confidence,
                signal.reason,
                signal.suggested_quantity,
                json.dumps(signal.metadata),
                int(signal.timestamp.timestamp()),
            ),
        )
        await self._db.commit()

    async def get_signal(self, signal_id: str) -> dict | None:
        """Get a signal by ID."""
        row = await self._db.fetchone(
            "SELECT * FROM signals WHERE signal_id = ?",
            (signal_id,),
        )
        return dict(row) if row else None

    # --- Order operations ---

    async def save_order(self, order: "Order") -> None:
        """Save a new order."""
        await self._db.execute(
            """
            INSERT INTO orders
            (order_id, broker_order_id, signal_id, symbol, side, order_type,
             quantity, filled_quantity, price, status, reject_reason,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.order_id,
                order.broker_order_id,
                order.signal_id,
                order.symbol,
                order.side,
                order.order_type,
                order.quantity,
                order.filled_quantity,
                float(order.price) if order.price else None,
                order.status.value,
                order.reject_reason,
                int(order.created_at.timestamp()),
                int(order.updated_at.timestamp()),
            ),
        )
        await self._db.commit()

    async def update_order(self, order: "Order") -> None:
        """Update an existing order."""
        await self._db.execute(
            """
            UPDATE orders SET
                broker_order_id = ?,
                filled_quantity = ?,
                status = ?,
                reject_reason = ?,
                updated_at = ?
            WHERE order_id = ?
            """,
            (
                order.broker_order_id,
                order.filled_quantity,
                order.status.value,
                order.reject_reason,
                int(order.updated_at.timestamp()),
                order.order_id,
            ),
        )
        await self._db.commit()

    async def get_order(self, order_id: str) -> dict | None:
        """Get an order by ID."""
        row = await self._db.fetchone(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        )
        return dict(row) if row else None

    async def get_order_by_broker_id(self, broker_order_id: str) -> dict | None:
        """Get an order by broker order ID."""
        row = await self._db.fetchone(
            "SELECT * FROM orders WHERE broker_order_id = ?",
            (broker_order_id,),
        )
        return dict(row) if row else None

    async def get_open_orders(self) -> list[dict]:
        """Get all non-terminal orders."""
        rows = await self._db.fetchall(
            """
            SELECT * FROM orders
            WHERE status NOT IN ('FILLED', 'CANCELED', 'REJECTED')
            ORDER BY created_at ASC
            """
        )
        return [dict(row) for row in rows]

    async def get_orders_by_status(self, status: "OrderStatus") -> list[dict]:
        """Get orders by status."""
        rows = await self._db.fetchall(
            "SELECT * FROM orders WHERE status = ? ORDER BY created_at ASC",
            (status.value,),
        )
        return [dict(row) for row in rows]

    async def count_orders_today(self) -> int:
        """
        Count orders submitted today (KST timezone).

        This counts all orders created today regardless of status.
        Used for enforcing max_trades_per_day limit.

        Returns:
            Number of orders submitted today
        """
        # Get today's start timestamp (midnight KST = UTC+9)
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_ts = int(today_start.timestamp())

        row = await self._db.fetchone(
            """
            SELECT COUNT(*) as count FROM orders
            WHERE created_at >= ?
            """,
            (today_start_ts,),
        )
        return row["count"] if row else 0

    # --- Fill operations ---

    async def save_fill(
        self,
        fill_id: str,
        order_id: str,
        quantity: int,
        price: Decimal,
        broker_fill_id: str | None = None,
        commission: Decimal | None = None,
        filled_at: datetime | None = None,
    ) -> None:
        """Save an order fill."""
        if filled_at is None:
            filled_at = datetime.now()
        await self._db.execute(
            """
            INSERT INTO fills
            (fill_id, order_id, broker_fill_id, quantity, price, commission, filled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill_id,
                order_id,
                broker_fill_id,
                quantity,
                float(price),
                float(commission) if commission else None,
                int(filled_at.timestamp()),
            ),
        )
        await self._db.commit()

    async def get_fills_for_order(self, order_id: str) -> list[dict]:
        """Get all fills for an order."""
        rows = await self._db.fetchall(
            "SELECT * FROM fills WHERE order_id = ? ORDER BY filled_at ASC",
            (order_id,),
        )
        return [dict(row) for row in rows]

    # --- Position operations ---

    async def save_position(
        self, symbol: str, quantity: int, avg_price: Decimal
    ) -> None:
        """Save or update a position."""
        await self._db.execute(
            """
            INSERT OR REPLACE INTO positions
            (symbol, quantity, avg_price, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (symbol, quantity, float(avg_price), int(datetime.now().timestamp())),
        )
        await self._db.commit()

    async def get_position(self, symbol: str) -> dict | None:
        """Get position for a symbol."""
        row = await self._db.fetchone(
            "SELECT * FROM positions WHERE symbol = ?",
            (symbol,),
        )
        return dict(row) if row else None

    async def get_all_positions(self) -> list[dict]:
        """Get all positions."""
        rows = await self._db.fetchall(
            "SELECT * FROM positions WHERE quantity != 0"
        )
        return [dict(row) for row in rows]

    async def delete_position(self, symbol: str) -> None:
        """Delete a position (set quantity to 0)."""
        await self._db.execute(
            "DELETE FROM positions WHERE symbol = ?",
            (symbol,),
        )
        await self._db.commit()

    # --- Bot run operations ---

    async def start_bot_run(self, run_id: str) -> None:
        """Record a new bot run."""
        await self._db.execute(
            """
            INSERT INTO bot_runs (run_id, started_at, status)
            VALUES (?, ?, 'RUNNING')
            """,
            (run_id, int(datetime.now().timestamp())),
        )
        await self._db.commit()

    async def end_bot_run(
        self, run_id: str, status: str, error_message: str | None = None
    ) -> None:
        """End a bot run."""
        await self._db.execute(
            """
            UPDATE bot_runs SET
                ended_at = ?,
                status = ?,
                error_message = ?
            WHERE run_id = ?
            """,
            (int(datetime.now().timestamp()), status, error_message, run_id),
        )
        await self._db.commit()

    async def get_last_bot_run(self) -> dict | None:
        """Get the most recent bot run."""
        row = await self._db.fetchone(
            "SELECT * FROM bot_runs ORDER BY started_at DESC LIMIT 1"
        )
        return dict(row) if row else None

    async def get_unfinished_bot_runs(self) -> list[dict]:
        """Get all bot runs without an end time."""
        rows = await self._db.fetchall(
            "SELECT * FROM bot_runs WHERE ended_at IS NULL ORDER BY started_at DESC"
        )
        return [dict(row) for row in rows]

    # --- Error operations ---

    async def log_error(
        self,
        run_id: str,
        error_type: str,
        message: str,
        context: dict | None = None,
    ) -> None:
        """Log an error."""
        await self._db.execute(
            """
            INSERT INTO errors (run_id, error_type, message, context, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                error_type,
                message,
                json.dumps(context) if context else None,
                int(datetime.now().timestamp()),
            ),
        )
        await self._db.commit()

    async def get_recent_errors(
        self, run_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Get recent errors, optionally filtered by run."""
        if run_id:
            rows = await self._db.fetchall(
                """
                SELECT * FROM errors WHERE run_id = ?
                ORDER BY occurred_at DESC LIMIT ?
                """,
                (run_id, limit),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM errors ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(row) for row in rows]
