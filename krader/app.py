"""Application orchestrator."""

import asyncio
import logging
import random
import signal
import time
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from krader.broker.base import BaseBroker, TickCallback
from krader.broker.kiwoom import KiwoomBroker
from krader.config import Settings
from krader.journal.service import JournalService
from krader.events import ControlEvent, ErrorEvent, EventBus, FillEvent, MarketEvent, OrderEvent, SignalEvent
from krader.notification import EmailNotifier
from krader.execution.oms import OrderManagementSystem
from krader.market.service import MarketDataService
from krader.market.types import Tick
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

# Seed prices for KOSPI blue chips (matches KOSPI_BLUE_CHIPS order in universe/service.py)
_SEED_PRICES: dict[str, int] = {
    "005930": 72000,   # Samsung Electronics
    "000660": 130000,  # SK Hynix
    "373220": 450000,  # LG Energy Solution
    "207940": 750000,  # Samsung Biologics
    "005380": 210000,  # Hyundai Motor
    "006400": 380000,  # Samsung SDI
    "051910": 460000,  # LG Chem
    "035420": 210000,  # NAVER
    "000270": 95000,   # Kia
    "105560": 65000,   # KB Financial
    "055550": 42000,   # Shinhan Financial
    "035720": 45000,   # Kakao
    "003670": 320000,  # POSCO Holdings
    "068270": 180000,  # Celltrion
    "028260": 130000,  # Samsung C&T
    "012330": 240000,  # Hyundai Mobis
    "066570": 100000,  # LG Electronics
    "003550": 80000,   # LG
    "096770": 110000,  # SK Innovation
    "034730": 170000,  # SK
}


def _round_to_tick_size(price: int) -> int:
    """Round price to KRX tick size."""
    if price < 2000:
        step = 1
    elif price < 5000:
        step = 5
    elif price < 20000:
        step = 10
    elif price < 50000:
        step = 50
    elif price < 200000:
        step = 100
    elif price < 500000:
        step = 500
    else:
        step = 1000
    return max(step, round(price / step) * step)


class MockBroker(BaseBroker):
    """Mock broker for testing with synthetic tick generation."""

    def __init__(self) -> None:
        self._connected = False
        self._order_counter = 0
        self._tick_callbacks: dict[str, TickCallback] = {}
        self._symbol_prices: dict[str, float] = {}
        self._tick_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        logger.info("Mock broker connected")

    async def disconnect(self) -> None:
        self._connected = False
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
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
        for symbol in symbols:
            self._tick_callbacks[symbol] = callback
            if symbol not in self._symbol_prices:
                self._symbol_prices[symbol] = float(
                    _SEED_PRICES.get(symbol, 50000)
                )

        if self._tick_task is None or self._tick_task.done():
            self._tick_task = asyncio.create_task(self._generate_ticks())
            logger.info("Mock tick generator started (%d symbols)", len(self._tick_callbacks))

    async def unsubscribe_market_data(self, symbols) -> None:
        for symbol in symbols:
            self._tick_callbacks.pop(symbol, None)
            self._symbol_prices.pop(symbol, None)

        if not self._tick_callbacks and self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
            logger.info("Mock tick generator stopped (no subscriptions)")

    async def _generate_ticks(self) -> None:
        """Background task: generate synthetic ticks every 0.5s."""
        logger.info("Mock tick generation loop started")
        try:
            while self._tick_callbacks:
                for symbol, callback in list(self._tick_callbacks.items()):
                    price = self._symbol_prices.get(symbol)
                    if price is None:
                        continue

                    # Random walk: ~0.03% per tick ≈ annualised ~20% vol
                    change = random.gauss(0, 0.0003)
                    price *= (1 + change)
                    price = max(price, 1)
                    int_price = _round_to_tick_size(int(round(price)))
                    self._symbol_prices[symbol] = float(int_price)

                    volume = random.randint(1, 500)

                    tick = Tick(
                        symbol=symbol,
                        price=Decimal(int_price),
                        volume=volume,
                    )
                    try:
                        await callback(tick)
                    except Exception:
                        logger.exception("Mock tick callback error for %s", symbol)

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.info("Mock tick generation loop cancelled")
            raise


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
        self._email_notifier: EmailNotifier | None = None
        self._journal_service: JournalService | None = None
        self._was_market_open: bool = False

        self._strategies: list[BaseStrategy] = []
        self._daily_trades_count: int = 0
        self._tick_count: int = 0
        self._signal_count: int = 0
        self._last_status_time: float = 0

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

        if self._settings.email.enabled:
            self._email_notifier = EmailNotifier(self._settings.email)
            await self._email_notifier.start()
            self._event_bus.subscribe(OrderEvent, self._email_notifier.on_order_event)
            self._event_bus.subscribe(FillEvent, self._email_notifier.on_fill_event)
            self._event_bus.subscribe(ControlEvent, self._email_notifier.on_control_event)
            self._event_bus.subscribe(ErrorEvent, self._email_notifier.on_error_event)
            logger.info("Email notifications enabled")

        if self._settings.broker.type == "mock":
            self._broker = MockBroker()
        else:
            self._broker = KiwoomBroker(
                account_number=self._settings.broker.account_number,
                tr_rate_limit_ms=self._settings.broker.tr_rate_limit_ms,
            )

        # Wire up broker error reporting to event bus
        self._broker.set_error_callback(self._on_broker_error)

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

        if self._settings.journal.enabled:
            self._journal_service = JournalService(
                repo=self._repo,
                journal_dir=self._settings.journal.journal_dir,
                strategy_name=self._settings.strategy,
            )
            logger.info("Journal service enabled (dir=%s)", self._settings.journal.journal_dir)

        self._setup_signal_handlers()

        self._running = True
        logger.info("Krader started successfully (run_id=%s)", self._reconciler.run_id)

    def _get_market_status(self) -> str:
        """Get current market status string."""
        now = datetime.now()
        risk = self._settings.risk
        market_open = now.replace(
            hour=risk.trading_start_hour, minute=risk.trading_start_minute,
            second=0, microsecond=0,
        )
        market_close = now.replace(
            hour=risk.trading_end_hour, minute=risk.trading_end_minute,
            second=0, microsecond=0,
        )

        if market_open <= now <= market_close:
            remaining = market_close - now
            mins = int(remaining.total_seconds()) // 60
            return f"장중 (마감까지 {mins // 60}시간 {mins % 60}분)"
        elif now < market_open:
            remaining = market_open - now
            mins = int(remaining.total_seconds()) // 60
            return f"장 시작 대기 ({mins // 60}시간 {mins % 60}분 후)"
        else:
            return "장 마감"

    def _is_market_open(self) -> bool:
        """Check if the market is currently open."""
        now = datetime.now()
        risk = self._settings.risk
        market_open = now.replace(
            hour=risk.trading_start_hour, minute=risk.trading_start_minute,
            second=0, microsecond=0,
        )
        market_close = now.replace(
            hour=risk.trading_end_hour, minute=risk.trading_end_minute,
            second=0, microsecond=0,
        )
        return market_open <= now <= market_close

    async def _on_market_close(self) -> None:
        """Triggered when market transitions from open to closed."""
        logger.info("Market close detected, generating daily journal...")
        await self._generate_journal()

    async def _generate_journal(self) -> None:
        """Generate daily journal if service is enabled and not yet generated."""
        if not self._journal_service or self._journal_service.generated_today:
            return
        try:
            portfolio = self._portfolio_tracker.portfolio
            path = await self._journal_service.generate_journal(
                date=datetime.now(),
                portfolio_equity=portfolio.total_equity,
                portfolio_cash=portfolio.cash,
            )
            if path:
                logger.info("Daily journal saved: %s", path)
        except Exception as e:
            logger.error("Failed to generate journal: %s", e)

    def _log_status(self) -> None:
        """Log periodic status update."""
        now = time.time()
        if now - self._last_status_time < 30:
            return
        self._last_status_time = now

        portfolio = self._portfolio_tracker.portfolio
        positions = len(portfolio.positions)
        market_status = self._get_market_status()
        kill = " [KILL SWITCH]" if self._control.is_kill_switch_active else ""
        paused = " [PAUSED]" if self._control.is_paused else ""

        logger.info(
            "STATUS | %s%s%s | ticks=%d | signals=%d | trades=%d/%d | "
            "positions=%d | cash=%s | universe=%d",
            market_status,
            kill,
            paused,
            self._tick_count,
            self._signal_count,
            self._daily_trades_count,
            self._settings.risk.max_trades_per_day,
            positions,
            f"{portfolio.cash:,.0f}",
            len(self._universe),
        )

    async def run(self) -> None:
        """Run the main application loop."""
        try:
            await self.start()

            while self._running and not self._control.shutdown_requested:
                await asyncio.sleep(0.1)
                self._log_status()

                # Detect market close transition
                is_open = self._is_market_open()
                if self._was_market_open and not is_open:
                    await self._on_market_close()
                self._was_market_open = is_open

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

        if self._email_notifier:
            await self._email_notifier.stop()

        if self._event_bus:
            await self._event_bus.stop()

        # Generate journal before closing DB (if not already generated)
        await self._generate_journal()

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

    async def _on_broker_error(
        self,
        error_type: str,
        message: str,
        severity: str,
        context: dict,
    ) -> None:
        """Handle errors reported by broker - emit as ErrorEvent."""
        if self._event_bus:
            await self._event_bus.publish(
                ErrorEvent(
                    error_type=error_type,
                    message=message,
                    severity=severity,
                    context=context,
                )
            )

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
                if self._event_bus:
                    await self._event_bus.publish(
                        ErrorEvent(
                            error_type="universe_refresh",
                            message="Universe refresh returned empty, keeping old universe",
                            severity="warning",
                        )
                    )
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
        if event.event_type == "tick":
            self._tick_count += 1
            return

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
        self._signal_count += 1

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
