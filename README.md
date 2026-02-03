# Krader

**Automated Trading System for Korean Stock Market (Kiwoom Securities)**

Krader is a modular, event-driven trading engine that connects to Kiwoom Open API+ to automatically trade Korean stocks. It features dynamic universe selection, pluggable strategies, risk management, and order lifecycle management.

## What Krader Does

```
┌─────────────────────────────────────────────────────────────────────┐
│                         KRADER AUTOMATION                            │
└─────────────────────────────────────────────────────────────────────┘

  YOU                           KRADER                         KIWOOM
   │                              │                               │
   │  1. Start Krader             │                               │
   │─────────────────────────────▶│                               │
   │                              │  2. Fetch Top 20 Stocks       │
   │                              │──────────────────────────────▶│
   │                              │◀──────────────────────────────│
   │                              │                               │
   │                              │  3. Subscribe Market Data     │
   │                              │──────────────────────────────▶│
   │                              │                               │
   │                              │  4. Receive Ticks/Candles     │
   │  (You do nothing)            │◀──────────────────────────────│
   │                              │                               │
   │                              │  5. Strategy Analyzes         │
   │                              │  6. Risk Validates            │
   │                              │  7. Place Order               │
   │                              │──────────────────────────────▶│
   │                              │                               │
   │  8. Check Results            │  9. Order Filled              │
   │◀──────────────────────────────◀──────────────────────────────│
```

### Automatic Features

| Feature | Description |
|---------|-------------|
| **Universe Selection** | Fetches top 20 stocks by trading value from Kiwoom |
| **Universe Refresh** | Updates every 30 minutes automatically |
| **Market Data** | Subscribes and builds candles (1m, 5m, 15m, 60m) |
| **Signal Generation** | Strategy analyzes data and emits BUY/SELL signals |
| **Risk Validation** | Checks position limits, exposure, daily loss |
| **Order Execution** | Places orders with idempotency protection |
| **Recovery** | Reconciles with broker state on restart |

### What You Must Do

1. **Setup**: Windows + Kiwoom Open API+ (one-time)
2. **Start**: Run `python main.py`
3. **Monitor**: Check logs and database

---

## Quick Start

### 1. Prerequisites

- **Windows 10+** (Kiwoom API uses COM)
- **Python 3.11+**
- **Kiwoom Securities Account** ([Open Account](https://www.kiwoom.com/))
- **Kiwoom Open API+** ([Download](https://www3.kiwoom.com/nkw.templateFrame498.do?m=m1408000000))

### 2. Installation

```bash
git clone <repository>
cd krader
pip install -e .
```

### 3. Run (Test Mode)

```bash
# Test with mock broker (no Kiwoom needed)
python main.py --mode=test

# Paper trading (requires Kiwoom)
python main.py --mode=paper --account=YOUR_ACCOUNT
```

### 4. Check Output

**Test mode (mock broker):**
```
Starting Krader in test mode with mock broker...
{"level": "INFO", "message": "Database connected: krader.db"}
{"level": "INFO", "message": "Mock broker connected"}
{"level": "INFO", "message": "Using default universe: 20 symbols"}
{"level": "INFO", "message": "Krader started successfully (run_id=RUN-xxx)"}
```

**Paper mode (Kiwoom):**
```
Starting Krader in paper mode with kiwoom broker...
{"level": "INFO", "message": "Database connected: krader.db"}
{"level": "INFO", "message": "Kiwoom QAxWidget created"}
{"level": "INFO", "message": "Login popup opened, waiting for user..."}
{"level": "INFO", "message": "Kiwoom login successful"}
{"level": "INFO", "message": "Connected to Kiwoom [paper (모의투자)], account: XXXXXXXXXX"}
{"level": "INFO", "message": "Reconciliation complete: positions=0, orders_updated=0"}
{"level": "INFO", "message": "Fetched universe: 20 symbols"}
{"level": "INFO", "message": "Krader started successfully (run_id=RUN-xxx)"}
```

---

## Project Structure

```
krader/
├── main.py                 # Entry point
├── pyproject.toml          # Dependencies
├── krader/
│   ├── app.py              # Application orchestrator
│   ├── config.py           # Configuration (pydantic)
│   │
│   ├── broker/             # Broker adapters
│   │   ├── base.py         # Abstract interface
│   │   ├── kiwoom.py       # Kiwoom implementation
│   │   └── errors.py       # Error types
│   │
│   ├── universe/           # Dynamic stock selection
│   │   └── service.py      # Top 20 by trading value
│   │
│   ├── market/             # Market data processing
│   │   ├── types.py        # Tick, Candle
│   │   ├── candle.py       # Candle builder
│   │   └── service.py      # Subscription manager
│   │
│   ├── strategy/           # Trading strategies
│   │   ├── base.py         # Abstract interface
│   │   ├── signal.py       # Signal dataclass
│   │   └── pullback_v1.py  # Pullback continuation strategy
│   │
│   ├── risk/               # Risk management
│   │   ├── portfolio.py    # Position tracking
│   │   └── validator.py    # Signal validation
│   │
│   ├── execution/          # Order management
│   │   ├── order.py        # Order state machine
│   │   ├── oms.py          # Order lifecycle
│   │   └── idempotency.py  # Duplicate prevention
│   │
│   ├── persistence/        # Database
│   │   ├── database.py     # SQLite wrapper
│   │   ├── models.py       # Schema
│   │   └── repository.py   # Data access
│   │
│   ├── recovery/           # Startup recovery
│   │   └── reconciler.py   # Broker reconciliation
│   │
│   ├── events/             # Event system
│   │   ├── types.py        # Event dataclasses
│   │   └── bus.py          # Pub/sub
│   │
│   └── monitor/            # Control & logging
│       ├── logger.py       # Structured logging
│       └── control.py      # Kill switch
│
├── tests/                  # Test files
│   ├── test_strategy_simulation.py
│   ├── test_strategy_controlled.py
│   └── test_buy_signal.py
│
├── docs/                   # Documentation
│   ├── FLOW.md             # Execution flow
│   ├── ARCHITECTURE.md     # Technical details
│   └── STRATEGY_GUIDE.md   # Strategy development
│
└── logs/                   # Log files (generated)
    ├── app.log
    ├── trades.log
    └── errors.log
```

---

## Included Strategy: PullbackV1

Krader comes with a **Pullback Continuation** strategy that:

1. **Filters by Trend**: Only trades when HTF (60m) EMA50 > EMA200
2. **Waits for Pullback**: Price must pull back to EMA20-EMA50 zone
3. **Enters on Confirmation**: LTF (5m) RSI crosses above 40 + breaks swing high
4. **Exits on Weakness**: LTF RSI crosses below 50 or price < EMA20

### Entry Conditions (ALL must be true)

| Condition | Timeframe | Rule |
|-----------|-----------|------|
| Trend Filter | 60m | EMA50 > EMA200 |
| Trend Strength | 60m | RSI(14) >= 40 |
| Pullback Zone | 60m | Price between EMA20 and EMA50 |
| RSI Cross Up | 5m | Previous RSI < 40, Current >= 40 |
| Above EMA | 5m | Price > EMA20 |
| Break Swing | 5m | Price > recent swing high |

### Signal Output

```python
Signal(
    action="BUY",
    confidence=0.8,  # 0.6 base + bonuses
    reason="entry_trigger",
    metadata={
        "htf_ema50": 63347.76,
        "htf_ema200": 58410.23,
        "htf_rsi14": 55.35,
        "ltf_rsi14": 53.74,
        "swing_high": 49377.45,
        "rsi_cross_up": True,
        "above_ema": True,
        "break_swing": True,
    }
)
```

---

## Configuration

### Environment Variables

```bash
# Mode
KRADER_MODE=paper                    # live, paper, test

# Broker
KRADER_BROKER__TYPE=kiwoom           # kiwoom, mock
KRADER_BROKER__ACCOUNT_NUMBER=1234567890

# Risk Limits
KRADER_RISK__MAX_POSITION_SIZE=1000
KRADER_RISK__MAX_PORTFOLIO_EXPOSURE_PCT=0.8
KRADER_RISK__DAILY_LOSS_LIMIT=1000000

# Trading Hours (KST)
KRADER_RISK__TRADING_START_HOUR=9
KRADER_RISK__TRADING_END_HOUR=15
KRADER_RISK__TRADING_END_MINUTE=30

# Logging
KRADER_LOGGING__LEVEL=INFO
KRADER_LOGGING__LOG_DIR=logs
```

### CLI Arguments

```bash
python main.py --mode=paper --account=1234567890 --log-level=DEBUG
```

| Argument | Options | Description |
|----------|---------|-------------|
| `--mode` | live, paper, test | Trading mode |
| `--broker` | kiwoom, mock | Broker type |
| `--account` | string | Account number |
| `--log-level` | DEBUG, INFO, WARNING, ERROR | Verbosity |

---

## Testing

### Run Strategy Tests

```bash
# Test with precise conditions (triggers BUY signal)
PYTHONPATH=. python3 tests/test_buy_signal.py

# Full test suite
PYTHONPATH=. python3 tests/test_strategy_controlled.py

# Random market simulation
PYTHONPATH=. python3 tests/test_strategy_simulation.py
```

### Expected Output (BUY Signal Test)

```
======================================================================
PullbackV1 - BUY Signal Test
======================================================================

[HTF Analysis]
  EMA50 > EMA200: True
  Price in pullback zone: True

[LTF Analysis]
  RSI cross up through 40: True
  Price > EMA20: True
  Price > Swing: True

SIGNAL GENERATED:
  Action:     BUY
  Confidence: 0.8
  Reason:     entry_trigger

======================================================================
✅ SUCCESS: BUY SIGNAL TRIGGERED!
======================================================================
```

---

## Database

SQLite database (`krader.db`) with 7 tables:

| Table | Purpose |
|-------|---------|
| `candles` | OHLCV market data |
| `signals` | Trading signals |
| `orders` | Order lifecycle |
| `fills` | Executions |
| `positions` | Current holdings |
| `bot_runs` | Run history |
| `errors` | Error log |

---

## Documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | This file - overview and quick start |
| [docs/FLOW.md](docs/FLOW.md) | Detailed execution flow |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Technical architecture |
| [docs/STRATEGY_GUIDE.md](docs/STRATEGY_GUIDE.md) | How to create strategies |

---

## Important Notes

### Windows Required

Kiwoom Open API+ uses Windows COM via PyQt5's QAxWidget. It will NOT work on:
- macOS
- Linux
- WSL (Windows Subsystem for Linux)

**Dependencies:** PyQt5, pywin32, aiosqlite, pydantic

### No API Key

Kiwoom uses your HTS login credentials, not API keys. You must:
1. Install Kiwoom Open API+
2. Log into HTS at least once to register
3. Enable API access in HTS settings

### Paper Trading Setup (Important!)

For paper trading (모의투자), you must register the account password **once**:

1. Run the script: `python main.py --mode=paper`
2. Login when prompted
3. **After login succeeds**, find the Kiwoom OpenAPI+ icon in the Windows system tray (bottom right, near clock)
4. **Right-click** the icon → Select **"계좌비밀번호 저장"**
5. In the password window:
   - Enter `0000` (paper trading password is always 0000)
   - Click **"전체계좌에 등록"** (Register to all accounts)
   - **Check the "AUTO" checkbox** ← Critical!
   - Close the window
6. Stop the script (Ctrl+C) and run again

This is a **one-time setup**. After registering, the password is saved permanently.

### Paper Trading First

Always test with 모의투자 (paper trading) before live trading:
```bash
python main.py --mode=paper
```

### Kill Switch

The system has automatic protection:
- Activates on 3+ errors in 5 minutes
- Cancels all open orders
- Blocks new trading

Manual activation:
```python
await app._control.activate_kill_switch("Manual stop")
```

### Troubleshooting

**Error: "계좌비밀번호 입력창을 통해..." (Password popup)**
- Password not registered. See "Paper Trading Setup" above.
- Register password via system tray with AUTO checkbox checked.

**Error: "TR request failed: Connection terminated"**
- Kiwoom disconnected due to password issue.
- Register password first, then restart.

**Error: "opt10030 request timeout" (Universe fetch)**
- API may be slow or not available in paper mode.
- System falls back to default KOSPI blue chips automatically.

**Process hangs after error**
- Fixed in current version. Uses `os._exit()` to force clean termination.

**No trading activity after startup**
- Check if Korean market is open (09:00-15:30 KST)
- Check if universe was fetched (look for "Fetched universe: X symbols")
- Paper trading accounts may have no positions/cash initially

---

## License

MIT License

## Disclaimer

This software is for educational purposes only. Trading involves risk. The authors are not responsible for any financial losses.
