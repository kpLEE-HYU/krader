"""Application orchestrator."""

import asyncio
import logging
import signal
from decimal import Decimal
from typing import TYPE_CHECKING

from krader.broker.base import BaseBroker
from krader.broker.kiwoom import KiwoomBroker
from krader.config import Settings
from krader.events import EventBus, FillEvent, MarketEvent, SignalEvent
from krader.execution.oms import OrderManagementSystem
from krader.market.service import MarketDataService
from krader.monitor.control import ControlManager
from krader.monitor.logger import get_trade_logger, setup_logging
from krader.persistence.database import Database
from krader.persistence.repository import Repository
from krader.recovery.reconciler import Reconciler
from krader.risk.portfolio import PortfolioTracker
from krader.risk.validator import RiskValidator
from krader.strategy.base import BaseStrategy, MarketSnapshot, StrategyContext
from krader.strategy.registry import create_strategy, get_available_strategies
from krader.universe.service import UniverseService, get_default_universe

if TYPE_CHECKING:
    from krader.strategy.signal import Signal

logger = logging.getLogger(__name__)
trade_logger = get_trade_logger()


class MockBroker(BaseBroker):
    """Mock broker for testing."""

    def __init__(self) -> None:
        self._connected = False
        self._order_counter = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        logger.info("Mock broker connected")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Mock broker disconnected")

    async def place_order(self, order) -> str:
        self._order_counter += 1
        return f"MOCK-{self._order_counter}"

    async def cancel_order(self, broker_order_id: str) -> bool:
        return True

    async def amend_order(self, broker_order_id: str, quantity=None, price=None) -> bool:
        return True

    async def fetch_positions(self):
        return []

    async def fetch_open_orders(self):
        return []

    async def fetch_balance(self):
        from krader.broker.base import Balance
        return Balance(
            total_equity=Decimal("10000000"),
            available_cash=Decimal("10000000"),
        )

    async def subscribe_market_data(self, symbols, callback) -> None:
        pass

    async def unsubscribe_market_data(self, symbols) -> None:
        pass


class Application:
    """Main application orchestrator."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._running = False

        self._db: Database | None = None
        self._repo: Repository | None = None
        self._event_bus: EventBus | None = None
        self._broker: BaseBroker | None = None
        self._oms: OrderManagementSystem | None = None
        self._market_service: MarketDataService | None = None
        self._portfolio_tracker: PortfolioTracker | None = None
        self._risk_validator: RiskValidator | None = None
        self._reconciler: Reconciler | None = None
        self._control: ControlManager | None = None
        self._universe_service: UniverseService | None = None
        self._universe: list[str] = []
        self._universe_refresh_task: asyncio.Task | None = None
        self._universe_refresh_interval_minutes: int = 30

        self._strategies: list[BaseStrategy] = []
        self._daily_trades_count: int = 0

    def add_strategy(self, strategy: BaseStrategy) -> None:
        """Add a strategy to the application."""
        self._strategies.append(strategy)
        logger.info("Added strategy: %s", strategy.name)

    def load_strategy_from_config(self) -> None:
        """
        Load strategy specified in config.

        Uses the strategy registry to instantiate the configured strategy.
        """
        strategy_name = self._settings.strategy
        available = get_available_strategies()

        logger.info(
            "Loading strategy: %s (available: %s)",
            strategy_name,
            available,
        )

        try:
            strategy = create_strategy(strategy_name)
            self.add_strategy(strategy)
        except ValueError as e:
            logger.error("Failed to load strategy: %s", e)
            raise

    async def start(self) -> None:
        """Start the application."""
        logger.info("Starting Krader trading system...")

        setup_logging(
            self._settings.logging.log_dir,
            self._settings.logging.level,
            self._settings.logging.json_format,
        )

        self._db = Database(self._settings.database.path)
        await self._db.connect()
        self._repo = Repository(self._db)

        self._event_bus = EventBus()
        await self._event_bus.start()

        if self._settings.broker.type == "mock":
            self._broker = MockBroker()
        else:
            self._broker = KiwoomBroker(
                account_number=self._settings.broker.account_number,
                tr_rate_limit_ms=self._settings.broker.tr_rate_limit_ms,
            )
        await self._broker.connect()

        self._risk_validator = RiskValidator(self._settings.risk)

        self._oms = OrderManagementSystem(
            self._broker,
            self._repo,
            self._event_bus,
        )
        await self._oms.load_active_orders()

        self._portfolio_tracker = PortfolioTracker(self._repo, self._event_bus)
        await self._portfolio_tracker.initialize()

        self._reconciler = Reconciler(
            self._broker,
            self._repo,
            self._portfolio_tracker,
        )
        result = await self._reconciler.reconcile()

        if not result.success:
            logger.error("Reconciliation failed: %s", result.error)
            raise RuntimeError(f"Reconciliation failed: {result.error}")

        self._market_service = MarketDataService(
            self._broker,
            self._repo,
            self._event_bus,
        )

        if isinstance(self._broker, KiwoomBroker):
            self._universe_service = UniverseService(self._broker)
            try:
                self._universe = await self._universe_service.get_top_by_trading_value(20)
                if not self._universe:
                    logger.warning("No symbols from API, using default universe")
                    self._universe = get_default_universe()
                logger.info("Fetched universe: %d symbols", len(self._universe))
            except Exception as e:
                logger.warning("Failed to fetch universe, using default: %s", e)
                self._universe = get_default_universe()
        else:
            self._universe = get_default_universe()
            logger.info("Using default universe: %d symbols", len(self._universe))

        self._control = ControlManager(
            self._event_bus,
            self._oms,
            self._risk_validator,
        )

        # Load daily trades count from database
        self._daily_trades_count = await self._repo.count_orders_today()
        logger.info("Daily trades count at startup: %d", self._daily_trades_count)

        self._event_bus.subscribe(MarketEvent, self._on_market_event)
        self._event_bus.subscribe(SignalEvent, self._on_signal_event)
        self._event_bus.subscribe(FillEvent, self._on_fill_event)

        for strategy in self._strategies:
            await strategy.on_start()
            strategy_symbols = strategy.symbols
            if strategy_symbols:
                await self._market_service.subscribe(strategy_symbols)

        if self._universe:
            await self._market_service.subscribe(self._universe)
            logger.info("Subscribed to universe: %s", self._universe[:5])

        if self._universe_service:
            self._universe_refresh_task = asyncio.create_task(self._universe_refresh_loop())

        self._setup_signal_handlers()

        self._running = True
        logger.info("Krader started successfully (run_id=%s)", self._reconciler.run_id)

    async def run(self) -> None:
        """Run the main application loop."""
        try:
            await self.start()

            while self._running and not self._control.shutdown_requested:
                await asyncio.sleep(0.1)

                if self._control.is_kill_switch_active:
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("Application cancelled")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the application gracefully."""
        logger.info("Stopping Krader...")
        self._running = False

        if self._universe_refresh_task:
            self._universe_refresh_task.cancel()
            try:
                await self._universe_refresh_task
            except asyncio.CancelledError:
                pass

        for strategy in self._strategies:
            await strategy.on_stop()

        if self._market_service:
            await self._market_service.shutdown()

        if self._event_bus:
            await self._event_bus.stop()

        if self._reconciler:
            status = "KILLED" if self._control and self._control.is_kill_switch_active else "COMPLETED"
            await self._reconciler.end_run(status)

        if self._broker:
            await self._broker.disconnect()

        if self._db:
            await self._db.disconnect()

        logger.info("Krader stopped")

    def _setup_signal_handlers(self) -> None:
        """Setup OS signal handlers for graceful shutdown."""
        import sys

        # add_signal_handler is not supported on Windows
        if sys.platform == "win32":
            return

        loop = asyncio.get_event_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self._handle_shutdown_signal()),
            )

    async def _handle_shutdown_signal(self) -> None:
        """Handle shutdown signal."""
        logger.warning("Received shutdown signal")
        if self._control:
            await self._control.request_shutdown("OS signal received")

    async def _universe_refresh_loop(self) -> None:
        """Background task to periodically refresh universe."""
        while self._running:
            try:
                await asyncio.sleep(self._universe_refresh_interval_minutes * 60)

                if not self._running:
                    break

                await self._refresh_universe()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Universe refresh error: %s", e)

    async def _refresh_universe(self) -> None:
        """Refresh universe and update subscriptions."""
        if not self._universe_service:
            return

        try:
            new_universe = await self._universe_service.get_top_by_trading_value(
                20, force_refresh=True
            )

            if not new_universe:
                logger.warning("Universe refresh returned empty, keeping old universe")
                return

            old_set = set(self._universe)
            new_set = set(new_universe)

            added = new_set - old_set
            removed = old_set - new_set

            if added:
                await self._market_service.subscribe(list(added))
                logger.info("Universe added: %s", list(added))

            if removed:
                await self._market_service.unsubscribe(list(removed))
                logger.info("Universe removed: %s", list(removed))

            self._universe = new_universe

            if added or removed:
                logger.info(
                    "Universe refreshed: %d symbols (+%d/-%d)",
                    len(self._universe),
                    len(added),
                    len(removed),
                )

        except Exception as e:
            logger.error("Failed to refresh universe: %s", e)

    async def _on_market_event(self, event: MarketEvent) -> None:
        """Handle market data events."""
        if self._control and self._control.is_paused:
            return

        if event.event_type != "candle":
            return

        historical_candles = {}
        for tf in ["1m", "5m", "15m", "60m"]:
            candles = await self._repo.get_candles(event.symbol, tf, limit=250)
            if candles:
                historical_candles[tf] = candles

        for strategy in self._strategies:
            strategy_symbols = strategy.symbols
            if strategy_symbols and event.symbol not in strategy_symbols:
                continue

            snapshot = MarketSnapshot(
                symbol=event.symbol,
                timestamp=event.timestamp,
                current_candles=self._market_service.get_all_current_candles(event.symbol),
                historical_candles=historical_candles,
            )

            context = StrategyContext(
                portfolio=self._portfolio_tracker.portfolio,
                active_orders_count=len(self._oms.get_active_orders()),
                daily_trades_count=self._daily_trades_count,
                metadata={"universe_top20": self._universe},
            )

            try:
                signals = await strategy.on_market_data(snapshot, context)
                for sig in signals:
                    await self._event_bus.publish(
                        SignalEvent(
                            signal_id=sig.signal_id,
                            symbol=sig.symbol,
                            action=sig.action,
                            confidence=sig.confidence,
                            reason=sig.reason,
                            metadata=sig.metadata,
                            timestamp=sig.timestamp,
                        )
                    )
            except Exception as e:
                logger.error("Strategy %s error: %s", strategy.name, e)
                if self._control.record_error():
                    await self._control.handle_repeated_errors()

    async def _on_signal_event(self, event: SignalEvent) -> None:
        """Handle signal events."""
        if self._control and self._control.is_paused:
            return

        if event.action == "HOLD":
            return

        from krader.strategy.signal import Signal

        signal = Signal(
            signal_id=event.signal_id,
            strategy_name="",
            symbol=event.symbol,
            action=event.action,
            confidence=event.confidence,
            reason=event.reason,
            suggested_quantity=event.metadata.get("suggested_quantity"),
            metadata=event.metadata,
            timestamp=event.timestamp,
        )

        await self._repo.save_signal(signal)

        current_price = None
        candle = self._market_service.get_current_candle(signal.symbol, "1m")
        if candle:
            current_price = candle.close

        # Create context for validation (used for max trades check)
        context = StrategyContext(
            portfolio=self._portfolio_tracker.portfolio,
            active_orders_count=len(self._oms.get_active_orders()),
            daily_trades_count=self._daily_trades_count,
            metadata={},
        )

        result = await self._risk_validator.validate_signal(
            signal,
            self._portfolio_tracker.portfolio,
            current_price,
            context=context,
        )

        if not result.approved:
            logger.info(
                "Signal rejected: %s - %s",
                signal.signal_id,
                result.reject_reason,
            )
            return

        order = await self._oms.process_approved_signal(
            signal,
            result.approved_quantity,
            current_price,
        )

        if order:
            # Increment daily trades count when order is submitted
            self._daily_trades_count += 1
            logger.debug(
                "Daily trades count: %d/%d",
                self._daily_trades_count,
                self._settings.risk.max_trades_per_day,
            )

            trade_logger.info(
                "Order created",
                extra={
                    "order_id": order.order_id,
                    "signal_id": signal.signal_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "status": order.status.value,
                },
            )

    async def _on_fill_event(self, event: FillEvent) -> None:
        """Handle fill events."""
        trade_logger.info(
            "Fill received",
            extra={
                "order_id": event.order_id,
                "quantity": event.quantity,
                "price": str(event.price),
            },
        )

        order_data = await self._repo.get_order(event.order_id)
        if not order_data:
            return

        for strategy in self._strategies:
            strategy_symbols = strategy.symbols
            if not strategy_symbols or order_data["symbol"] in strategy_symbols:
                await strategy.on_fill(
                    order_data["symbol"],
                    order_data["side"],
                    event.quantity,
                    event.price,
                )
