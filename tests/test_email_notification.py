"""
Test email notification system with mock scenarios.

Tests:
- Error aggregation
- Rate limiting
- Deduplication
- Event handling
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from krader.notification.email_notifier import EmailNotifier, ErrorTracker
from krader.events import ErrorEvent, OrderEvent, FillEvent, ControlEvent


class MockEmailConfig:
    """Mock email configuration for testing."""

    enabled = True
    smtp_host = "smtp.test.com"
    smtp_port = 587
    smtp_user = "test@test.com"
    smtp_password = "password"
    use_tls = True
    from_address = "krader@test.com"
    to_addresses = ["user@test.com"]
    environment = "test"
    send_timeout_seconds = 5.0
    max_retries = 2


class TestErrorAggregation:
    """Test error aggregation and threshold behavior."""

    @pytest.fixture
    def notifier(self):
        return EmailNotifier(MockEmailConfig())

    @pytest.mark.asyncio
    async def test_warning_threshold_requires_3_occurrences(self, notifier):
        """Warning severity should require 3 occurrences before sending."""
        # Mock the enqueue to track calls
        enqueue_calls = []
        original_enqueue = notifier._enqueue

        async def mock_enqueue(*args, **kwargs):
            enqueue_calls.append((args, kwargs))

        notifier._enqueue = mock_enqueue

        # Send 2 warnings - should not trigger email
        await notifier.on_error("test_warning", "Error 1", "warning")
        await notifier.on_error("test_warning", "Error 2", "warning")

        assert len(enqueue_calls) == 0
        assert "test_warning" in notifier._error_trackers
        assert notifier._error_trackers["test_warning"].count == 2

        # Third warning should trigger
        await notifier.on_error("test_warning", "Error 3", "warning")

        assert len(enqueue_calls) == 1
        assert "test_warning" not in notifier._error_trackers  # Cleared after send

    @pytest.mark.asyncio
    async def test_error_threshold_requires_2_occurrences(self, notifier):
        """Error severity should require 2 occurrences."""
        enqueue_calls = []

        async def mock_enqueue(*args, **kwargs):
            enqueue_calls.append((args, kwargs))

        notifier._enqueue = mock_enqueue

        await notifier.on_error("test_error", "Error 1", "error")
        assert len(enqueue_calls) == 0

        await notifier.on_error("test_error", "Error 2", "error")
        assert len(enqueue_calls) == 1

    @pytest.mark.asyncio
    async def test_critical_triggers_immediately(self, notifier):
        """Critical severity should trigger immediately."""
        enqueue_calls = []

        async def mock_enqueue(*args, **kwargs):
            enqueue_calls.append((args, kwargs))

        notifier._enqueue = mock_enqueue

        await notifier.on_error("test_critical", "Critical error!", "critical")

        assert len(enqueue_calls) == 1

    @pytest.mark.asyncio
    async def test_different_error_types_tracked_separately(self, notifier):
        """Different error types should be tracked independently."""
        await notifier.on_error("type_a", "Error A1", "error")
        await notifier.on_error("type_b", "Error B1", "error")
        await notifier.on_error("type_a", "Error A2", "error")

        # type_a should be cleared (reached threshold)
        # type_b should still be tracked
        assert "type_a" not in notifier._error_trackers
        assert "type_b" in notifier._error_trackers
        assert notifier._error_trackers["type_b"].count == 1


class TestDeduplication:
    """Test event deduplication."""

    @pytest.fixture
    def notifier(self):
        return EmailNotifier(MockEmailConfig())

    @pytest.mark.asyncio
    async def test_duplicate_events_are_skipped(self, notifier):
        """Same event_id within TTL should be skipped."""
        send_calls = []

        async def mock_send(*args, **kwargs):
            send_calls.append((args, kwargs))

        notifier._send_email = mock_send

        # Queue same event twice
        await notifier._enqueue("event_1", "Test Subject", "Test Body")
        await notifier._enqueue("event_1", "Test Subject", "Test Body")  # Duplicate

        assert notifier._queue.qsize() == 1  # Only one queued


class TestOrderEventHandling:
    """Test handling of order events."""

    @pytest.fixture
    def notifier(self):
        return EmailNotifier(MockEmailConfig())

    @pytest.mark.asyncio
    async def test_order_event_generates_email(self, notifier):
        """Order events should generate appropriate emails."""
        from krader.execution.order import Order, OrderStatus

        enqueue_calls = []

        async def mock_enqueue(*args, **kwargs):
            enqueue_calls.append(kwargs)

        notifier._enqueue = mock_enqueue

        order = MagicMock()
        order.symbol = "005930"
        order.side = "BUY"
        order.quantity = 100
        order.filled_quantity = 0
        order.status = MagicMock()
        order.status.value = "NEW"
        order.order_id = "test-order-1"
        order.broker_order_id = "KW-12345"

        event = OrderEvent(
            order_id="test-order-1",
            event_type="new",
            order=order,
            timestamp=datetime.now(),
        )

        await notifier.on_order_event(event)

        assert len(enqueue_calls) == 1
        assert "Order Submitted" in enqueue_calls[0]["subject"]
        assert "005930" in enqueue_calls[0]["subject"]
        assert "BUY" in enqueue_calls[0]["subject"]


class TestControlEventHandling:
    """Test handling of control events."""

    @pytest.fixture
    def notifier(self):
        return EmailNotifier(MockEmailConfig())

    @pytest.mark.asyncio
    async def test_kill_event_generates_alert(self, notifier):
        """Kill switch should generate immediate alert."""
        enqueue_calls = []

        async def mock_enqueue(*args, **kwargs):
            enqueue_calls.append(kwargs)

        notifier._enqueue = mock_enqueue

        event = ControlEvent(
            command="kill",
            timestamp=datetime.now(),
        )

        await notifier.on_control_event(event)

        assert len(enqueue_calls) == 1
        assert "ALERT" in enqueue_calls[0]["subject"]
        assert "Kill Switch" in enqueue_calls[0]["subject"]

    @pytest.mark.asyncio
    async def test_pause_event_does_not_generate_email(self, notifier):
        """Pause/resume events should not generate emails."""
        enqueue_calls = []

        async def mock_enqueue(*args, **kwargs):
            enqueue_calls.append(kwargs)

        notifier._enqueue = mock_enqueue

        event = ControlEvent(
            command="pause",
            timestamp=datetime.now(),
        )

        await notifier.on_control_event(event)

        assert len(enqueue_calls) == 0


class TestErrorEventHandling:
    """Test handling of error events through event bus."""

    @pytest.fixture
    def notifier(self):
        return EmailNotifier(MockEmailConfig())

    @pytest.mark.asyncio
    async def test_error_event_triggers_aggregation(self, notifier):
        """ErrorEvent should be processed through aggregation."""
        event = ErrorEvent(
            error_type="tick_processing",
            message="Volume cannot be negative",
            severity="warning",
            context={"symbol": "005930"},
        )

        await notifier.on_error_event(event)

        assert "tick_processing" in notifier._error_trackers
        tracker = notifier._error_trackers["tick_processing"]
        assert tracker.count == 1
        assert "Volume cannot be negative" in tracker.samples


class TestRateLimiting:
    """Test rate limiting behavior."""

    @pytest.fixture
    def notifier(self):
        notifier = EmailNotifier(MockEmailConfig())
        notifier.RATE_LIMIT_PER_MINUTE = 3  # Lower for testing
        return notifier

    @pytest.mark.asyncio
    async def test_rate_limit_cleans_old_timestamps(self, notifier):
        """Rate limiting should clean old timestamps and allow sends."""
        # Add timestamps from 2 minutes ago (should be cleaned)
        old_time = datetime.now() - timedelta(minutes=2)
        notifier._send_timestamps = [old_time, old_time, old_time]

        # Should complete immediately since old timestamps are cleaned
        await asyncio.wait_for(
            notifier._wait_for_rate_limit(),
            timeout=1.0
        )

        # Timestamps should be empty after cleanup
        assert len(notifier._send_timestamps) == 0


class TestWorkerLifecycle:
    """Test notifier start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_worker_task(self):
        """Start should create background worker task."""
        notifier = EmailNotifier(MockEmailConfig())

        await notifier.start()

        assert notifier._running is True
        assert notifier._worker_task is not None

        await notifier.stop()

        assert notifier._running is False

    @pytest.mark.asyncio
    async def test_stop_drains_queue(self):
        """Stop should attempt to drain the queue."""
        notifier = EmailNotifier(MockEmailConfig())

        # Mock send to avoid actual SMTP
        notifier._send_email = AsyncMock()

        await notifier.start()

        # Queue some messages
        await notifier._enqueue("test1", "Subject 1", "Body 1")

        await notifier.stop()

        # Worker should have processed the queue
        assert notifier._queue.empty()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
