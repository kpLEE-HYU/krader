"""Control commands for the trading system."""

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from krader.events import ControlEvent, EventBus

if TYPE_CHECKING:
    from krader.execution.oms import OrderManagementSystem
    from krader.risk.validator import RiskValidator

logger = logging.getLogger(__name__)


class ControlManager:
    """Manages system control commands (pause, resume, kill switch)."""

    def __init__(
        self,
        event_bus: EventBus,
        oms: "OrderManagementSystem",
        risk_validator: "RiskValidator",
    ) -> None:
        self._event_bus = event_bus
        self._oms = oms
        self._risk_validator = risk_validator
        self._paused = False
        self._shutdown_requested = False
        self._error_timestamps: list[datetime] = []
        self._error_threshold = 3
        self._error_window_minutes = 5

    @property
    def is_paused(self) -> bool:
        """Check if trading is paused."""
        return self._paused

    @property
    def is_kill_switch_active(self) -> bool:
        """Check if kill switch is active."""
        return self._risk_validator.kill_switch_active

    @property
    def shutdown_requested(self) -> bool:
        """Check if shutdown has been requested."""
        return self._shutdown_requested

    async def pause(self) -> None:
        """Pause trading (no new orders, existing orders continue)."""
        self._paused = True
        self._oms.pause()

        await self._event_bus.publish(ControlEvent(command="pause"))
        logger.warning("Trading PAUSED")

    async def resume(self) -> None:
        """Resume trading."""
        self._paused = False
        self._oms.resume()

        await self._event_bus.publish(ControlEvent(command="resume"))
        logger.info("Trading RESUMED")

    async def activate_kill_switch(self, reason: str = "Manual activation") -> int:
        """
        Activate kill switch: cancel all orders, block new trading.

        Args:
            reason: Reason for activation

        Returns:
            Number of orders canceled
        """
        self._risk_validator.activate_kill_switch()
        self._oms.pause()

        canceled = await self._oms.cancel_all_orders()

        await self._event_bus.publish(ControlEvent(command="kill"))
        logger.critical("KILL SWITCH ACTIVATED: %s (canceled %d orders)", reason, canceled)

        return canceled

    async def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch (requires explicit action)."""
        self._risk_validator.deactivate_kill_switch()
        logger.warning("Kill switch DEACTIVATED - manual intervention")

    async def request_shutdown(self, reason: str = "Shutdown requested") -> None:
        """Request graceful shutdown."""
        self._shutdown_requested = True

        await self._event_bus.publish(ControlEvent(command="shutdown"))
        logger.warning("SHUTDOWN REQUESTED: %s", reason)

    def record_error(self) -> bool:
        """
        Record an error occurrence and check if threshold exceeded.

        Returns:
            True if error threshold exceeded (should trigger kill switch)
        """
        now = datetime.now()
        self._error_timestamps.append(now)

        cutoff = now - timedelta(minutes=self._error_window_minutes)
        self._error_timestamps = [t for t in self._error_timestamps if t > cutoff]

        if len(self._error_timestamps) >= self._error_threshold:
            logger.error(
                "Error threshold exceeded: %d errors in %d minutes",
                len(self._error_timestamps),
                self._error_window_minutes,
            )
            return True

        return False

    async def handle_repeated_errors(self) -> None:
        """Called when error threshold is exceeded."""
        await self.activate_kill_switch(
            f"Repeated errors: {len(self._error_timestamps)} in {self._error_window_minutes} min"
        )

    def reset_error_count(self) -> None:
        """Reset error tracking."""
        self._error_timestamps.clear()

    def get_status(self) -> dict:
        """Get current control status."""
        return {
            "paused": self._paused,
            "kill_switch_active": self.is_kill_switch_active,
            "shutdown_requested": self._shutdown_requested,
            "recent_errors": len(self._error_timestamps),
        }
