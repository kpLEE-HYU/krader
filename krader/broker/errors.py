"""Normalized broker error types."""


class BrokerError(Exception):
    """Base class for all broker errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ConnectionError(BrokerError):
    """Failed to connect to broker."""

    pass


class OrderRejectedError(BrokerError):
    """Order was rejected by the broker."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        order_id: str | None = None,
    ) -> None:
        super().__init__(message, code)
        self.order_id = order_id


class InsufficientFundsError(BrokerError):
    """Insufficient funds or margin for order."""

    pass


class RateLimitError(BrokerError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message, code)
        self.retry_after_ms = retry_after_ms


class MarketClosedError(BrokerError):
    """Market is closed."""

    pass


class SymbolNotFoundError(BrokerError):
    """Symbol not found or not tradeable."""

    pass
