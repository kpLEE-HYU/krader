# Krader Execution Flow

Step-by-step explanation of what happens when you run Krader.

## TL;DR

```bash
python main.py --mode=paper --account=1234567890
```

**What happens:**
1. Connects to Kiwoom
2. Fetches top 20 stocks by trading value (automatic)
3. Subscribes to real-time market data
4. On each candle close: Strategy analyzes → Risk validates → OMS places orders
5. Every 30 minutes: Refreshes top 20 list

**What you do:** Nothing (after starting). Monitor logs if desired.

---

## Startup Sequence

```
┌─────────────────────────────────────────────────────────────────────┐
│                      STARTUP SEQUENCE                                │
└─────────────────────────────────────────────────────────────────────┘

 Step 1: Load Configuration
         ├── Read KRADER_* environment variables
         ├── Apply CLI arguments (--mode, --account, etc.)
         └── Create Settings object

 Step 2: Initialize Database
         ├── Connect to SQLite (krader.db)
         ├── Create tables if not exist
         └── Enable WAL mode

 Step 3: Start Event Bus
         └── Initialize asyncio.Queue for pub/sub

 Step 4: Connect to Broker
         ├── Mock broker: Instant connect (testing)
         └── Kiwoom broker:
             ├── Start Qt thread (daemon) with QApplication
             ├── Create QAxWidget for Kiwoom OCX
             ├── Call CommConnect() → Login popup appears
             ├── Wait for OnEventConnect callback
             ├── Detect paper/live mode via GetServerGubun
             └── Log connection: "Connected to Kiwoom [paper/live]"

 Step 5: Initialize Components
         ├── Order Management System (OMS)
         ├── Portfolio Tracker
         ├── Risk Validator
         └── Market Data Service

 Step 6: Run Reconciliation
         ├── Create bot_run record (for error logging)
         ├── Fetch positions from broker (requires password)
         ├── Fetch open orders from broker
         ├── Sync local DB with broker (broker wins)
         └── Mark any crashed runs as ended

         NOTE: For paper trading, password "0000" must be registered
         via system tray BEFORE this step succeeds. See README.

 Step 7: Fetch Universe
         ├── Kiwoom: Call opt10030 (거래량상위)
         ├── Get top 20 by trading value
         ├── If API fails or returns empty: Use default KOSPI blue chips
         └── Default includes: Samsung, SK Hynix, LG Energy, etc.

 Step 8: Subscribe Market Data
         ├── Call SetRealReg for each symbol
         └── Register tick callback

 Step 9: Start Background Tasks
         └── Universe refresh task (every 30 min)

 Step 10: Start Strategies
          ├── Call strategy.on_start()
          └── Strategy ready to receive data

 Step 11: Enter Main Loop
          └── Process events until shutdown
```

---

## Runtime Flow

### Market Data → Signal → Order

```
┌─────────────────────────────────────────────────────────────────────┐
│                       TRADING FLOW                                   │
└─────────────────────────────────────────────────────────────────────┘

 KIWOOM                    KRADER                         DATABASE
    │                         │                               │
    │  OnReceiveRealData      │                               │
    │  (tick: 005930, 70500)  │                               │
    │────────────────────────▶│                               │
    │                         │                               │
    │                         │  CandleBuilder.process_tick() │
    │                         │  ─────────────────────────────│
    │                         │                               │
    │                         │  [If candle closes]           │
    │                         │                               │
    │                         │  Save candle ────────────────▶│
    │                         │                               │
    │                         │  Load historical candles ◀────│
    │                         │                               │
    │                         │  ┌─────────────────────────┐  │
    │                         │  │ Create MarketSnapshot   │  │
    │                         │  │ - symbol: "005930"      │  │
    │                         │  │ - 60m candles (250)     │  │
    │                         │  │ - 5m candles (250)      │  │
    │                         │  └─────────────────────────┘  │
    │                         │                               │
    │                         │  ┌─────────────────────────┐  │
    │                         │  │ Create StrategyContext  │  │
    │                         │  │ - portfolio             │  │
    │                         │  │ - universe_top20        │  │
    │                         │  │ - is_market_open        │  │
    │                         │  └─────────────────────────┘  │
    │                         │                               │
    │                         │  Strategy.on_market_data()    │
    │                         │  ─────────────────────────────│
    │                         │                               │
    │                         │  [If signal returned]         │
    │                         │                               │
    │                         │  Save signal ────────────────▶│
    │                         │                               │
    │                         │  RiskValidator.validate()     │
    │                         │  ─────────────────────────────│
    │                         │  - Check position limits      │
    │                         │  - Check portfolio exposure   │
    │                         │  - Check daily loss           │
    │                         │  - Check trading hours        │
    │                         │                               │
    │                         │  [If approved]                │
    │                         │                               │
    │                         │  OMS.process_approved_signal()│
    │                         │  ─────────────────────────────│
    │                         │  - Generate idempotency key   │
    │                         │  - Check for duplicates       │
    │                         │  - Create Order object        │
    │                         │                               │
    │                         │  Save order ─────────────────▶│
    │                         │                               │
    │  SendOrder()            │                               │
    │◀────────────────────────│                               │
    │                         │                               │
    │  OnReceiveChejanData    │                               │
    │  (fill: 10 @ 70500)     │                               │
    │────────────────────────▶│                               │
    │                         │                               │
    │                         │  Save fill ──────────────────▶│
    │                         │                               │
    │                         │  Update position ────────────▶│
    │                         │                               │
    │                         │  Strategy.on_fill()           │
    │                         │                               │
```

---

## Universe Refresh Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    UNIVERSE REFRESH (Every 30 min)                   │
└─────────────────────────────────────────────────────────────────────┘

 Time: 09:00 (Startup)
 ┌────────────────────────────────────────────────────────────────────┐
 │ Universe: [005930, 000660, 373220, 207940, 005380, ...]            │
 │ Subscribed: All 20 symbols                                         │
 └────────────────────────────────────────────────────────────────────┘

 Time: 09:30 (First refresh)
 ┌────────────────────────────────────────────────────────────────────┐
 │ Fetch new top 20 from Kiwoom                                       │
 │                                                                     │
 │ Old: [005930, 000660, 373220, 207940, 005380, ...]                 │
 │ New: [005930, 000660, 373220, 035420, 005380, ...]                 │
 │                                                                     │
 │ Diff:                                                               │
 │   Added:   [035420]  ──▶ Subscribe market data                     │
 │   Removed: [207940]  ──▶ Unsubscribe market data                   │
 │                                                                     │
 │ Universe updated for all strategies                                 │
 └────────────────────────────────────────────────────────────────────┘

 Time: 10:00 (Second refresh)
 ┌────────────────────────────────────────────────────────────────────┐
 │ Fetch new top 20 from Kiwoom                                       │
 │ ... process continues ...                                          │
 └────────────────────────────────────────────────────────────────────┘
```

---

## Signal Processing Detail

### PullbackV1 Strategy Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                  PULLBACKV1 SIGNAL GENERATION                        │
└─────────────────────────────────────────────────────────────────────┘

 Input: MarketSnapshot for symbol "005930"

 Step 1: Universe Check
         ├── Is "005930" in universe_top20?
         ├── Yes → Continue
         └── No → Return []

 Step 2: Market Hours Check
         ├── Is market open (09:00-15:30)?
         ├── Yes → Continue
         └── No → Return []

 Step 3: Data Validation
         ├── 60m candles >= 200?
         ├── 5m candles >= 20?
         └── If not → Return [HOLD("insufficient_data")]

 Step 4: HTF Trend Filter
         ├── Calculate EMA50, EMA200, RSI14 on 60m
         ├── EMA50 > EMA200?
         ├── RSI14 >= 40?
         └── If not → Return [HOLD("trend_filter_fail")]

 Step 5: HTF Pullback Check
         ├── Is price in EMA20-EMA50 zone (±1%)?
         ├── Are last 2 candles NOT bearish + expanding?
         └── If not → Return [HOLD("no_pullback")]

 Step 6: LTF Exit Check
         ├── RSI crossed down through 50?
         ├── Price < EMA20?
         └── If yes → Return [SELL("exit_trigger")]

 Step 7: LTF Entry Check
         ├── RSI crossed up through 40?
         ├── Price > EMA20?
         ├── Price > swing high?
         ├── Not in cooldown?
         └── If all yes → Return [BUY("entry_trigger")]

 Step 8: Default
         └── Return [HOLD("hold")]
```

---

## Risk Validation Detail

```
┌─────────────────────────────────────────────────────────────────────┐
│                     RISK VALIDATION                                  │
└─────────────────────────────────────────────────────────────────────┘

 Input: Signal(action=BUY, symbol=005930, suggested_quantity=100)

 Check 1: Kill Switch
          ├── Is kill switch active?
          └── Yes → REJECT("Kill switch is active")

 Check 2: Trading Hours
          ├── 09:00 <= now <= 15:30?
          └── No → REJECT("Outside trading hours")

 Check 3: Position Size
          ├── Current position: 500 shares
          ├── Max allowed: 1000 shares
          ├── Requested: 100 shares
          ├── Resulting: 600 shares
          └── 600 <= 1000 → PASS (approved_qty=100)

 Check 4: Portfolio Exposure
          ├── Current exposure: 60%
          ├── Max exposure: 80%
          ├── Order value: 7,050,000 KRW
          ├── New exposure: 67%
          └── 67% <= 80% → PASS

 Check 5: Available Cash
          ├── Available: 10,000,000 KRW
          ├── Order value: 7,050,000 KRW
          └── 7,050,000 <= 10,000,000 → PASS

 Check 6: Daily Loss Limit
          ├── Daily P&L: -500,000 KRW
          ├── Limit: -1,000,000 KRW
          └── -500,000 > -1,000,000 → PASS

 Result: ValidationResult(approved=True, approved_quantity=100)
```

---

## Order Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                      ORDER STATE MACHINE                             │
└─────────────────────────────────────────────────────────────────────┘

                    ┌──────────────┐
                    │ PENDING_NEW  │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │ broker.place_order()    │
              ▼                         ▼
      ┌──────────────┐          ┌──────────────┐
      │  SUBMITTED   │          │   REJECTED   │ (terminal)
      └──────┬───────┘          └──────────────┘
             │
    ┌────────┴────────┬─────────────────────────┐
    │                 │                         │
    ▼                 ▼                         ▼
┌──────────┐  ┌──────────────┐          ┌──────────────┐
│ CANCELED │  │ PARTIAL_FILL │          │    FILLED    │ (terminal)
│(terminal)│  └──────┬───────┘          └──────────────┘
└──────────┘         │
                     │
            ┌────────┴────────┐
            │                 │
            ▼                 ▼
    ┌──────────────┐  ┌──────────────┐
    │    FILLED    │  │   CANCELED   │
    │  (terminal)  │  │  (terminal)  │
    └──────────────┘  └──────────────┘
```

---

## Shutdown Sequence

```
┌─────────────────────────────────────────────────────────────────────┐
│                      SHUTDOWN SEQUENCE                               │
└─────────────────────────────────────────────────────────────────────┘

 Trigger: SIGINT (Ctrl+C) or SIGTERM

 Step 1: Set shutdown flag
         └── self._running = False

 Step 2: Cancel background tasks
         └── Universe refresh task cancelled

 Step 3: Stop strategies
         └── Call strategy.on_stop() for each

 Step 4: Shutdown market service
         ├── Unsubscribe from all symbols
         └── Flush pending candles

 Step 5: Stop event bus
         ├── Stop processing loop
         └── Drain remaining events

 Step 6: End bot run
         └── Update bot_runs table (status=COMPLETED)

 Step 7: Disconnect broker
         └── Release COM object

 Step 8: Disconnect database
         └── Close SQLite connection

 Step 9: Log final message
         └── "Krader stopped"
```

---

## Recovery (On Restart)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    RECOVERY ON RESTART                               │
└─────────────────────────────────────────────────────────────────────┘

 Scenario: Krader crashed or was killed unexpectedly

 Step 1: Load last bot_run
         └── Query: SELECT * FROM bot_runs WHERE ended_at IS NULL

 Step 2: Mark as crashed
         └── UPDATE bot_runs SET status='CRASHED', ended_at=now()

 Step 3: Fetch broker state
         ├── Positions from broker
         └── Open orders from broker

 Step 4: Reconcile positions
         ├── Compare local DB vs broker
         ├── Broker wins (update local)
         └── Log discrepancies

 Step 5: Reconcile orders
         ├── Local open orders not in broker → Mark as FILLED or CANCELED
         ├── Broker orders not in local → Log warning
         └── Update filled quantities

 Step 6: Create new bot_run
         └── INSERT INTO bot_runs (run_id, started_at, status='RUNNING')

 Step 7: Resume trading
         └── System ready for new signals
```

---

## Component Status Summary

| Component | Status | Notes |
|-----------|--------|-------|
| Event Bus | ✅ Working | Async pub/sub |
| Database | ✅ Working | SQLite with WAL |
| Repository | ✅ Working | All CRUD operations |
| Mock Broker | ✅ Working | For testing |
| Kiwoom Broker | ✅ Implemented | Requires Windows |
| Universe Service | ✅ Working | Auto-fetch + refresh |
| Market Data Service | ✅ Working | Candle building |
| Candle Builder | ✅ Working | 1m, 5m, 15m, 60m |
| PullbackV1 Strategy | ✅ Working | Tested |
| Risk Validator | ✅ Working | All rules |
| OMS | ✅ Working | Idempotent |
| Portfolio Tracker | ✅ Working | From fills |
| Reconciler | ✅ Working | Broker sync |
| Control Manager | ✅ Working | Kill switch |
| Structured Logging | ✅ Working | JSON format |

---

## File Outputs

After running Krader:

```
krader/
├── krader.db              # SQLite database
└── logs/
    ├── app.log            # Application logs (JSON)
    ├── trades.log         # Trade events (JSON)
    └── errors.log         # Errors only (JSON)
```

### Example app.log

```json
{"timestamp": "2024-01-15T09:00:00.123Z", "level": "INFO", "message": "Database connected: krader.db"}
{"timestamp": "2024-01-15T09:00:00.456Z", "level": "INFO", "message": "Mock broker connected"}
{"timestamp": "2024-01-15T09:00:00.789Z", "level": "INFO", "message": "Fetched universe: 20 symbols"}
{"timestamp": "2024-01-15T09:00:01.000Z", "level": "INFO", "message": "Krader started successfully (run_id=RUN-abc123)"}
```

### Example trades.log

```json
{"timestamp": "2024-01-15T10:30:00.000Z", "event": "Order created", "order_id": "ORD-xyz", "symbol": "005930", "side": "BUY", "quantity": 10}
{"timestamp": "2024-01-15T10:30:01.000Z", "event": "Fill received", "order_id": "ORD-xyz", "quantity": 10, "price": "70500"}
```
