"""Structured logging configuration."""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "correlation_id"):
            log_data["correlation_id"] = record.correlation_id

        if hasattr(record, "extra_data"):
            log_data.update(record.extra_data)

        return json.dumps(log_data, ensure_ascii=False)


class TradeFormatter(logging.Formatter):
    """Formatter for trade-specific logs."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": record.getMessage(),
        }

        for attr in ["order_id", "signal_id", "symbol", "side", "quantity", "price", "status"]:
            if hasattr(record, attr):
                log_data[attr] = getattr(record, attr)

        return json.dumps(log_data, ensure_ascii=False)


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    json_format: bool = True,
) -> None:
    """
    Configure logging for the application.

    Creates three log files:
    - app.log: General application logs
    - trades.log: Trade-specific events
    - errors.log: Error logs only

    Args:
        log_dir: Directory for log files
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON formatting if True
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level))

    root_logger.handlers.clear()

    if json_format:
        app_formatter = JsonFormatter()
    else:
        app_formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(app_formatter)
    root_logger.addHandler(console_handler)

    app_handler = logging.FileHandler(log_dir / "app.log")
    app_handler.setLevel(logging.DEBUG)
    app_handler.setFormatter(app_formatter)
    root_logger.addHandler(app_handler)

    error_handler = logging.FileHandler(log_dir / "errors.log")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(app_formatter)
    root_logger.addHandler(error_handler)

    trade_logger = logging.getLogger("krader.trades")
    trade_logger.setLevel(logging.INFO)
    trade_logger.propagate = False

    trade_handler = logging.FileHandler(log_dir / "trades.log")
    trade_handler.setLevel(logging.INFO)
    if json_format:
        trade_handler.setFormatter(TradeFormatter())
    else:
        trade_handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    trade_logger.addHandler(trade_handler)

    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def get_trade_logger() -> logging.Logger:
    """Get the trade-specific logger."""
    return logging.getLogger("krader.trades")


class LogContext:
    """Context manager for adding correlation ID to logs."""

    def __init__(self, correlation_id: str) -> None:
        self.correlation_id = correlation_id
        self._old_factory = None

    def __enter__(self) -> "LogContext":
        self._old_factory = logging.getLogRecordFactory()

        def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = self._old_factory(*args, **kwargs)
            record.correlation_id = self.correlation_id
            return record

        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._old_factory:
            logging.setLogRecordFactory(self._old_factory)
