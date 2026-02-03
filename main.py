"""Krader - Automated Trading System Entry Point."""

import argparse
import asyncio
import os
import sys

from krader.app import Application
from krader.config import Settings, load_settings
from krader.strategy.registry import get_available_strategies


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Krader - Automated Trading System for Kiwoom Open API+"
    )

    parser.add_argument(
        "--mode",
        choices=["live", "paper", "test"],
        default=None,
        help="Trading mode (default: from config)",
    )

    parser.add_argument(
        "--broker",
        choices=["kiwoom", "mock"],
        default=None,
        help="Broker type (default: from config)",
    )

    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Account number (default: from config)",
    )

    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Database path (default: from config)",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Log level (default: from config)",
    )

    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Strategy name to run (default: from config). Use --list-strategies to see available.",
    )

    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="List available strategies and exit",
    )

    return parser.parse_args()


def apply_args_to_settings(args: argparse.Namespace, settings: Settings) -> Settings:
    """Apply command line arguments to settings."""
    if args.mode:
        settings.mode = args.mode

    if args.broker:
        settings.broker.type = args.broker

    if args.account:
        settings.broker.account_number = args.account

    if args.db:
        from pathlib import Path
        settings.database.path = Path(args.db)

    if args.log_level:
        settings.logging.level = args.log_level

    if args.strategy:
        settings.strategy = args.strategy

    return settings


async def async_main(settings: Settings) -> int:
    """Async main entry point."""
    app = Application(settings)

    # Load strategy from config/CLI
    app.load_strategy_from_config()

    try:
        await app.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Handle --list-strategies
    if args.list_strategies:
        strategies = get_available_strategies()
        print("Available strategies:")
        for name in strategies:
            print(f"  - {name}")
        return 0

    settings = load_settings()
    settings = apply_args_to_settings(args, settings)

    if settings.mode == "test":
        settings.broker.type = "mock"

    print(f"Starting Krader in {settings.mode} mode with {settings.broker.type} broker...")
    print(f"Strategy: {settings.strategy}")
    print(f"Position size: {settings.risk.position_size_pct:.1%} of equity per trade")
    print(f"Max trades/day: {settings.risk.max_trades_per_day}")
    print(f"Transaction cost: {settings.risk.transaction_cost_rate:.4%}")

    try:
        return asyncio.run(async_main(settings))
    finally:
        # Ensure Qt is fully terminated on Windows
        try:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.quit()
        except Exception:
            pass


if __name__ == "__main__":
    exit_code = main()
    # Force exit to avoid PyQt thread hang on Windows
    os._exit(exit_code)
