"""Email notification service for trade events."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

import aiosmtplib

if TYPE_CHECKING:
    from krader.config import EmailConfig
    from krader.events import ControlEvent, FillEvent, OrderEvent

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    """Email message to be sent."""

    event_id: str
    subject: str
    body: str
    created_at: datetime


class EmailNotifier:
    """
    Async email notifier for trade events.

    Features:
    - Non-blocking: Events are queued and processed by background worker
    - Retry with exponential backoff (1s, 2s, 4s)
    - Rate limiting (max 10 emails/minute)
    - Deduplication (5-minute TTL by event_id)
    - Queue overflow protection (max 1000 items)
    """

    MAX_QUEUE_SIZE = 1000
    RATE_LIMIT_PER_MINUTE = 10
    DEDUP_TTL_SECONDS = 300  # 5 minutes
    BACKOFF_BASE_SECONDS = 1.0

    def __init__(self, config: "EmailConfig") -> None:
        self._config = config
        self._queue: asyncio.Queue[EmailMessage] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._worker_task: asyncio.Task | None = None
        self._running = False

        # Deduplication cache: event_id -> timestamp
        self._sent_cache: dict[str, datetime] = {}

        # Rate limiting: timestamps of recent sends
        self._send_timestamps: list[datetime] = []

    async def start(self) -> None:
        """Start the background worker."""
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Email notifier started")

    async def stop(self) -> None:
        """Stop the background worker gracefully."""
        self._running = False

        if self._worker_task:
            # Give worker time to drain queue
            try:
                await asyncio.wait_for(self._worker_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass

        logger.info("Email notifier stopped")

    async def on_order_event(self, event: "OrderEvent") -> None:
        """Handle OrderEvent - queue email for order lifecycle events."""
        if event.order is None:
            return

        order = event.order
        env_prefix = f"[{self._config.environment.upper()}] "

        status_labels = {
            "new": "Order Submitted",
            "partial": "Partial Fill",
            "filled": "Order Filled",
            "canceled": "Order Canceled",
            "rejected": "Order Rejected",
        }

        label = status_labels.get(event.event_type, event.event_type.title())
        subject = f"{env_prefix}Krader: {label} - {order.symbol} {order.side}"

        body = f"""Order Event: {event.event_type.upper()}

Symbol: {order.symbol}
Side: {order.side}
Quantity: {order.quantity}
Filled: {order.filled_quantity}
Status: {order.status.value}
Order ID: {order.order_id}
Broker Order ID: {order.broker_order_id or 'N/A'}

Time: {event.timestamp.isoformat()}
"""

        await self._enqueue(
            event_id=f"order_{order.order_id}_{event.event_type}",
            subject=subject,
            body=body,
        )

    async def on_fill_event(self, event: "FillEvent") -> None:
        """Handle FillEvent - queue email for trade fills."""
        env_prefix = f"[{self._config.environment.upper()}] "
        subject = f"{env_prefix}Krader: Fill Executed - {event.quantity}@{event.price}"

        body = f"""Fill Executed

Order ID: {event.order_id}
Fill ID: {event.fill_id}
Quantity: {event.quantity}
Price: {event.price}

Time: {event.timestamp.isoformat()}
"""

        await self._enqueue(
            event_id=f"fill_{event.fill_id}",
            subject=subject,
            body=body,
        )

    async def on_control_event(self, event: "ControlEvent") -> None:
        """Handle ControlEvent - queue email for kill/shutdown only."""
        if event.command not in ("kill", "shutdown"):
            return

        env_prefix = f"[{self._config.environment.upper()}] "

        if event.command == "kill":
            subject = f"{env_prefix}ALERT: Krader Kill Switch Activated"
            body = f"""KILL SWITCH ACTIVATED

The kill switch has been triggered. All trading is halted.
All open orders will be canceled.

Time: {event.timestamp.isoformat()}

Please investigate immediately.
"""
        else:  # shutdown
            subject = f"{env_prefix}Krader: System Shutdown"
            body = f"""System Shutdown

Krader trading system is shutting down.

Time: {event.timestamp.isoformat()}
"""

        await self._enqueue(
            event_id=f"control_{event.command}_{event.timestamp.isoformat()}",
            subject=subject,
            body=body,
        )

    async def _enqueue(self, event_id: str, subject: str, body: str) -> None:
        """Add message to queue with deduplication check."""
        # Check deduplication cache
        now = datetime.now()
        if event_id in self._sent_cache:
            cache_time = self._sent_cache[event_id]
            if now - cache_time < timedelta(seconds=self.DEDUP_TTL_SECONDS):
                logger.debug("Skipping duplicate event: %s", event_id)
                return

        # Add to dedup cache
        self._sent_cache[event_id] = now

        # Clean old cache entries
        self._cleanup_dedup_cache(now)

        message = EmailMessage(
            event_id=event_id,
            subject=subject,
            body=body,
            created_at=now,
        )

        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("Email queue full, dropping message: %s", subject)

    def _cleanup_dedup_cache(self, now: datetime) -> None:
        """Remove expired entries from deduplication cache."""
        cutoff = now - timedelta(seconds=self.DEDUP_TTL_SECONDS)
        expired = [k for k, v in self._sent_cache.items() if v < cutoff]
        for key in expired:
            del self._sent_cache[key]

    async def _worker_loop(self) -> None:
        """Background worker that processes the email queue."""
        while self._running or not self._queue.empty():
            try:
                # Wait for next message with timeout
                try:
                    message = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                # Apply rate limiting
                await self._wait_for_rate_limit()

                # Send with retries
                await self._send_with_retry(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Email worker error: %s", e)

    async def _wait_for_rate_limit(self) -> None:
        """Wait if rate limit would be exceeded."""
        now = datetime.now()
        cutoff = now - timedelta(minutes=1)

        # Clean old timestamps
        self._send_timestamps = [ts for ts in self._send_timestamps if ts > cutoff]

        # Wait if at limit
        while len(self._send_timestamps) >= self.RATE_LIMIT_PER_MINUTE:
            oldest = min(self._send_timestamps)
            wait_seconds = (oldest + timedelta(minutes=1) - now).total_seconds()
            if wait_seconds > 0:
                logger.debug("Rate limit reached, waiting %.1fs", wait_seconds)
                await asyncio.sleep(wait_seconds)
            now = datetime.now()
            cutoff = now - timedelta(minutes=1)
            self._send_timestamps = [ts for ts in self._send_timestamps if ts > cutoff]

    async def _send_with_retry(self, message: EmailMessage) -> None:
        """Send email with exponential backoff retry."""
        max_retries = self._config.max_retries

        for attempt in range(max_retries + 1):
            try:
                await self._send_email(message)
                self._send_timestamps.append(datetime.now())
                logger.info("Email sent: %s", message.subject)
                return
            except Exception as e:
                if attempt == max_retries:
                    logger.error(
                        "Failed to send email after %d attempts: %s - %s",
                        max_retries + 1,
                        message.subject,
                        e,
                    )
                    return

                backoff = self.BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "Email send failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    max_retries + 1,
                    backoff,
                    e,
                )
                await asyncio.sleep(backoff)

    async def _send_email(self, message: EmailMessage) -> None:
        """Send a single email via SMTP."""
        if not self._config.to_addresses:
            logger.warning("No recipients configured, skipping email")
            return

        msg = MIMEText(message.body)
        msg["Subject"] = message.subject
        msg["From"] = self._config.from_address
        msg["To"] = ", ".join(self._config.to_addresses)

        await aiosmtplib.send(
            msg,
            hostname=self._config.smtp_host,
            port=self._config.smtp_port,
            username=self._config.smtp_user or None,
            password=self._config.smtp_password or None,
            start_tls=self._config.use_tls,
            timeout=self._config.send_timeout_seconds,
        )
