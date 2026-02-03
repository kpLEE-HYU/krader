# Krader Architecture

Technical documentation of Krader's internal architecture.

## System Overview

Krader is an **event-driven**, **async** trading system built on:
- **asyncio** for concurrent operations
- **SQLite** for persistence
- **Kiwoom COM** for broker connectivity

```
┌────────────────────────────────────────────────────────────────────────┐
│                           APPLICATION                                   │
├────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌───────────┐ │
│  │  Universe   │   │   Market    │   │  Strategy   │   │   Risk    │ │
│  │  Service    │   │  Service    │   │ (PullbackV1)│   │ Validator │ │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘   └─────┬─────┘ │
│         │                 │                 │                 │       │
│         │                 ▼                 ▼                 │       │
│         │          ┌─────────────────────────────┐            │       │
│         │          │        EVENT BUS            │            │       │
│         │          │  (asyncio.Queue pub/sub)    │◀───────────┘       │
│         │          └─────────────┬───────────────┘                    │
│         │                        │                                     │
│         │                        ▼                                     │
│         │          ┌─────────────────────────────┐                    │
│         │          │           OMS               │                    │
│         │          │  (Order Management System)  │                    │
│         │          └─────────────┬───────────────┘                    │
│         │                        │                                     │
│         ▼                        ▼                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                      BROKER ADAPTER                              │ │
│  │                   (Kiwoom / Mock)                                │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                  │                                     │
├──────────────────────────────────┼─────────────────────────────────────┤
│                                  │                                     │
│  ┌─────────────┐   ┌─────────────┴─────────────┐   ┌─────────────┐   │
│  │ Repository  │   │      Reconciler           │   │  Portfolio  │   │
│  │  (SQLite)   │   │   (Startup Recovery)      │   │  Tracker    │   │
│  └─────────────┘   └───────────────────────────┘   └─────────────┘   │
│                                                                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. Application (`krader/app.py`)

The central orchestrator that:
- Initializes all components
- Manages lifecycle (start/stop)
- Wires event handlers
- Runs the main loop

```python
class Application:
    def __init__(self, settings: Settings):
        self._db: Database
        self._repo: Repository
        self._event_bus: EventBus
        self._broker: BaseBroker
        self._oms: OrderManagementSystem
        self._market_service: MarketDataService
        self._portfolio_tracker: PortfolioTracker
        self._risk_validator: RiskValidator
        self._universe_service: UniverseService
        self._reconciler: Reconciler
        self._control: ControlManager
        self._strategies: list[BaseStrategy]
```

**Startup Sequence:**
1. Connect database
2. Start event bus
3. Connect broker
4. Initialize OMS, portfolio, risk validator
5. Run reconciliation
6. Initialize market service
7. Fetch universe (top 20)
8. Subscribe to market data
9. Start universe refresh task (every 30 min)
10. Start strategies
11. Enter main loop

### 2. Event Bus (`krader/events/bus.py`)

Async pub/sub system using `asyncio.Queue`:

```python
class EventBus:
    def subscribe(self, event_type: type[Event], handler: EventHandler) -> None
    async def publish(self, event: Event) -> None
    async def start(self) -> None
    async def stop(self) -> None
```

**Event Types:**
- `MarketEvent` - Tick or candle data
- `SignalEvent` - Strategy signal
- `OrderEvent` - Order lifecycle
- `FillEvent` - Execution
- `ControlEvent` - System commands

### 3. Broker Adapter (`krader/broker/`)

Abstract interface with Kiwoom implementation:

```python
class BaseBroker(ABC):
    async def connect(self) -> None
    async def disconnect(self) -> None
    async def place_order(self, order: Order) -> str
    async def cancel_order(self, broker_order_id: str) -> bool
    async def fetch_positions(self) -> list[Position]
    async def fetch_open_orders(self) -> list[dict]
    async def fetch_balance(self) -> Balance
    async def subscribe_market_data(self, symbols: list[str], callback) -> None
```

**Kiwoom Implementation:**
- Uses `PyQt5` with `QAxWidget` for COM/OCX interface
- Qt event loop runs in a separate daemon thread
- Rate limiting: 200ms between TR requests
- Callbacks converted to asyncio events via `call_soon_threadsafe()`
- Paper trading auto-detects via `GetLoginInfo("GetServerGubun")`

### 4. Universe Service (`krader/universe/service.py`)

Dynamic stock selection:

```python
class UniverseService:
    async def get_top_by_trading_value(self, size: int = 20) -> list[str]
    def set_static_universe(self, symbols: list[str]) -> None
```

**Features:**
- Fetches from Kiwoom TR `opt10030` (거래량상위)
- 60-minute cache
- Auto-refresh every 30 minutes
- Fallback to KOSPI blue chips when API fails or returns empty
- Default universe: Samsung, SK Hynix, LG Energy, Hyundai Motor, etc.

### 5. Market Data Service (`krader/market/service.py`)

Manages real-time data:

```python
class MarketDataService:
    async def subscribe(self, symbols: list[str]) -> None
    async def unsubscribe(self, symbols: list[str]) -> None
    def get_current_candle(self, symbol: str, timeframe: str) -> Candle | None
```

**Candle Builder:**
- Aggregates ticks into OHLCV candles
- Timeframes: 1m, 5m, 15m, 60m
- Emits `MarketEvent` on candle close
- Stores candles in database

### 6. Strategy Interface (`krader/strategy/base.py`)

Abstract base for trading strategies:

```python
class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def symbols(self) -> list[str]: ...

    @abstractmethod
    async def on_market_data(
        self,
        snapshot: MarketSnapshot,
        context: StrategyContext,
    ) -> list[Signal]: ...
```

**Key Contracts:**
- Return `Signal` objects, never place orders directly
- Use `context.metadata["universe_top20"]` for dynamic universe
- Return `[]` if symbol not in universe
- Degrade gracefully on missing data

### 7. Risk Validator (`krader/risk/validator.py`)

Signal validation:

```python
class RiskValidator:
    async def validate_signal(
        self,
        signal: Signal,
        portfolio: Portfolio,
        current_price: Decimal | None,
    ) -> ValidationResult
```

**Rules:**
- Max position size per symbol
- Max portfolio exposure (%)
- Available cash check
- Daily loss limit
- Trading hours check
- Kill switch check

### 8. Order Management System (`krader/execution/oms.py`)

Order lifecycle management:

```python
class OrderManagementSystem:
    async def process_approved_signal(self, signal: Signal, approved_qty: int, price: Decimal | None) -> Order | None
    async def handle_fill(self, broker_order_id: str, quantity: int, price: Decimal) -> None
    async def cancel_order(self, order_id: str) -> bool
    async def cancel_all_orders(self) -> int
```

**Order State Machine:**
```
PENDING_NEW ──► SUBMITTED ──► PARTIAL_FILL ──► FILLED
                    │              │
                    ▼              ▼
                REJECTED       CANCELED
```

**Idempotency:**
- Order ID = hash(signal_id + symbol + side + quantity + time_bucket)
- Prevents duplicate orders on restart

### 9. Reconciler (`krader/recovery/reconciler.py`)

Startup recovery:

```python
class Reconciler:
    async def reconcile(self) -> ReconciliationResult
```

**Reconciliation Steps:**
1. Mark unfinished runs as crashed
2. Fetch positions from broker
3. Fetch open orders from broker
4. Sync local state (broker wins)
5. Create new bot_run entry

### 10. Portfolio Tracker (`krader/risk/portfolio.py`)

Position tracking:

```python
class PortfolioTracker:
    @property
    def portfolio(self) -> Portfolio

    async def sync_with_broker(self, positions: list[Position], balance: Balance) -> None
```

**Updates from:**
- Broker sync (on startup)
- Fill events (during trading)

---

## Data Flow

### Market Data Flow

```
Kiwoom API
    │
    │ OnReceiveRealData callback
    ▼
KiwoomBroker._handle_tick_data()
    │
    │ Creates Tick object
    ▼
MarketDataService._on_tick()
    │
    │ Publishes MarketEvent (tick)
    │ Updates CandleBuilder
    ▼
CandleBuilder.process_tick()
    │
    │ On candle close:
    ▼
MarketDataService._on_candle_close()
    │
    │ Saves to DB
    │ Publishes MarketEvent (candle)
    ▼
EventBus
    │
    ▼
Application._on_market_event()
    │
    │ Loads historical candles
    │ Creates MarketSnapshot
    ▼
Strategy.on_market_data()
    │
    │ Returns Signal[]
    ▼
EventBus (SignalEvent)
```

### Signal to Order Flow

```
SignalEvent
    │
    ▼
Application._on_signal_event()
    │
    │ Saves signal to DB
    ▼
RiskValidator.validate_signal()
    │
    │ Returns ValidationResult
    │ (approved_quantity or reject_reason)
    ▼
OMS.process_approved_signal()
    │
    │ Generate idempotency key
    │ Check for existing order
    │ Create Order object
    │ Save to DB
    ▼
Broker.place_order()
    │
    │ Returns broker_order_id
    ▼
OMS updates order status
    │
    │ Publishes OrderEvent
    ▼
EventBus
```

### Fill Flow

```
Kiwoom API
    │
    │ OnReceiveChejanData callback
    ▼
KiwoomBroker._handle_fill()
    │
    ▼
OMS.handle_fill()
    │
    │ Update order filled_quantity
    │ Save fill to DB
    │ Publish FillEvent
    ▼
EventBus
    │
    ▼
PortfolioTracker._on_fill()
    │
    │ Update position
    │ Save to DB
    ▼
Application._on_fill_event()
    │
    │ Notify strategies
    ▼
Strategy.on_fill()
```

---

## Database Schema

```sql
-- Candles (OHLCV data)
CREATE TABLE candles (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    UNIQUE(symbol, timeframe, open_time)
);

-- Signals (strategy output)
CREATE TABLE signals (
    signal_id TEXT PRIMARY KEY,
    strategy_name TEXT,
    symbol TEXT, action TEXT,
    confidence REAL, reason TEXT,
    suggested_quantity INTEGER,
    metadata TEXT,  -- JSON
    created_at INTEGER
);

-- Orders (lifecycle)
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,  -- Idempotency key
    broker_order_id TEXT,
    signal_id TEXT,
    symbol TEXT, side TEXT, order_type TEXT,
    quantity INTEGER, filled_quantity INTEGER,
    price REAL, status TEXT, reject_reason TEXT,
    created_at INTEGER, updated_at INTEGER
);

-- Fills (executions)
CREATE TABLE fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT, broker_fill_id TEXT,
    quantity INTEGER, price REAL,
    commission REAL, filled_at INTEGER
);

-- Positions (cached)
CREATE TABLE positions (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER, avg_price REAL,
    updated_at INTEGER
);

-- Bot runs (history)
CREATE TABLE bot_runs (
    run_id TEXT PRIMARY KEY,
    started_at INTEGER, ended_at INTEGER,
    status TEXT, error_message TEXT
);

-- Errors (log)
CREATE TABLE errors (
    id INTEGER PRIMARY KEY,
    run_id TEXT, error_type TEXT,
    message TEXT, context TEXT,
    occurred_at INTEGER
);
```

---

## Threading Model

```
┌─────────────────────────────────────────────────────────────────┐
│                      MAIN ASYNCIO LOOP                          │
│                                                                  │
│   ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│   │  Event Bus      │  │  Main Loop      │  │  Universe     │  │
│   │  (processing)   │  │  (0.1s tick)    │  │  Refresh Task │  │
│   └─────────────────┘  └─────────────────┘  └───────────────┘  │
│                                                                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               │ call_soon_threadsafe()
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      QT THREAD (daemon)                          │
│                                                                  │
│   ┌─────────────────┐  ┌─────────────────┐                     │
│   │  QApplication   │  │  QAxWidget      │                     │
│   │  (event loop)   │  │  (Kiwoom OCX)   │                     │
│   └─────────────────┘  └─────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      DATABASE                                    │
│                                                                  │
│   ┌─────────────────┐                                           │
│   │  aiosqlite      │                                           │
│   │  (async wrapper)│                                           │
│   └─────────────────┘                                           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

- Main loop runs in asyncio (main thread)
- Qt event loop runs in separate daemon thread (`_qt_thread`)
- Kiwoom OCX calls made via `QAxWidget.dynamicCall()`
- OCX callbacks signal asyncio events via `call_soon_threadsafe()`
- Database uses aiosqlite (async wrapper)
- On shutdown, `QMetaObject.invokeMethod()` used for thread-safe Qt quit

---

## Error Handling

### Error Categories

| Category | Handling |
|----------|----------|
| Connection errors | Logged, retry on reconciliation |
| Order rejection | Logged, order marked REJECTED |
| Rate limit | Wait and retry |
| Data errors | Degrade gracefully |
| Repeated errors | Trigger kill switch (3+ in 5 min) |

### Kill Switch

Automatic protection that:
1. Cancels all open orders
2. Blocks new order placement
3. Logs critical event

Triggers:
- 3+ errors in 5 minutes (automatic)
- Manual activation
- Critical system error

---

## Configuration

### Settings Hierarchy

```
Environment Variables (KRADER_*)
         │
         ▼
    Settings class (pydantic)
         │
         ▼
    CLI Arguments (override)
```

### Key Settings

```python
class Settings:
    mode: Literal["live", "paper", "test"]

    class DatabaseConfig:
        path: Path = "krader.db"

    class BrokerConfig:
        type: Literal["kiwoom", "mock"]
        account_number: str
        tr_rate_limit_ms: int = 200

    class RiskConfig:
        max_position_size: int = 1000
        max_portfolio_exposure_pct: float = 0.8
        daily_loss_limit: float = 1_000_000
        trading_start_hour: int = 9
        trading_end_hour: int = 15

    class LoggingConfig:
        level: str = "INFO"
        log_dir: Path = "logs"
        json_format: bool = True
```

---

## Extension Points

### Adding a New Strategy

1. Create `krader/strategy/my_strategy.py`
2. Inherit from `BaseStrategy`
3. Implement `name`, `symbols`, `on_market_data`
4. Register in `main.py`

### Adding a New Broker

1. Create `krader/broker/my_broker.py`
2. Inherit from `BaseBroker`
3. Implement all abstract methods
4. Add to broker factory in `app.py`

### Adding New Events

1. Define dataclass in `krader/events/types.py`
2. Subscribe handlers in `Application.start()`
3. Publish with `event_bus.publish()`

---

## Performance Considerations

| Area | Design Choice |
|------|---------------|
| Market data | Async processing, no blocking |
| Database | WAL mode, async wrapper |
| Kiwoom calls | Thread pool isolation |
| Event processing | Queue-based, decoupled |
| Candle building | In-memory, flush on close |

---

## Testing

### Unit Tests

```bash
# Strategy logic
PYTHONPATH=. python3 tests/test_buy_signal.py
```

### Integration Tests

```bash
# Full flow with controlled data
PYTHONPATH=. python3 tests/test_strategy_controlled.py
```

### Mock Testing

```bash
# Run with mock broker
python main.py --mode=test --broker=mock
```
