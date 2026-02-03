"""Idempotency key generation for orders."""

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from krader.strategy.signal import Signal


def generate_idempotency_key(
    signal: "Signal",
    quantity: int,
    bucket_seconds: int = 60,
) -> str:
    """
    Generate a deterministic idempotency key for an order.

    The key is based on:
    - signal_id
    - symbol
    - action (BUY/SELL)
    - quantity
    - time bucket (to allow retries in different time windows)

    Args:
        signal: The trading signal
        quantity: The approved order quantity
        bucket_seconds: Time bucket size in seconds (default 60s)

    Returns:
        A deterministic order ID string
    """
    timestamp_bucket = int(signal.timestamp.timestamp()) // bucket_seconds

    key_parts = [
        signal.signal_id,
        signal.symbol,
        signal.action,
        str(quantity),
        str(timestamp_bucket),
    ]

    key_string = "|".join(key_parts)
    hash_digest = hashlib.sha256(key_string.encode()).hexdigest()[:16]

    return f"ORD-{hash_digest}"


def generate_fill_id(
    order_id: str,
    fill_sequence: int,
) -> str:
    """
    Generate a unique fill ID.

    Args:
        order_id: The order ID
        fill_sequence: Sequential fill number for this order

    Returns:
        A unique fill ID string
    """
    return f"FILL-{order_id}-{fill_sequence}"


def generate_signal_id(
    strategy_name: str,
    symbol: str,
    timestamp: datetime | None = None,
) -> str:
    """
    Generate a unique signal ID.

    Args:
        strategy_name: Name of the strategy
        symbol: Trading symbol
        timestamp: Signal timestamp (defaults to now)

    Returns:
        A unique signal ID string
    """
    if timestamp is None:
        timestamp = datetime.now()

    key_parts = [
        strategy_name,
        symbol,
        str(int(timestamp.timestamp() * 1000)),  # Millisecond precision
    ]

    key_string = "|".join(key_parts)
    hash_digest = hashlib.sha256(key_string.encode()).hexdigest()[:12]

    return f"SIG-{hash_digest}"
