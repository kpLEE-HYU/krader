# Configuration Guide

Comprehensive guide to configuring Krader's trading parameters, risk limits, and strategy selection.

## Table of Contents

- [Configuration Methods](#configuration-methods)
- [Environment Variables](#environment-variables)
- [CLI Arguments](#cli-arguments)
- [Risk Configuration](#risk-configuration)
- [Strategy Selection](#strategy-selection)
- [Adding New Strategies](#adding-new-strategies)
- [Daily Trades Tracking](#daily-trades-tracking)

---

## Configuration Methods

Krader supports three configuration methods (in order of precedence):

1. **CLI Arguments** (highest priority)
2. **Environment Variables**
3. **Default Values** (lowest priority)

```bash
# CLI overrides environment variable
KRADER_STRATEGY=pullback_v1 python main.py --strategy=my_strategy
# Result: my_strategy is used
```

---

## Environment Variables

All environment variables use the `KRADER_` prefix. Nested config uses `__` delimiter.

### Complete .env Example

```bash
# Mode: live, paper, test
KRADER_MODE=paper

# Broker settings
KRADER_BROKER__TYPE=kiwoom
KRADER_BROKER__ACCOUNT_NUMBER=1234567890

# Strategy selection (must match registered strategy name)
KRADER_STRATEGY=pullback_v1

# Risk limits
KRADER_RISK__MAX_POSITION_SIZE=1000
KRADER_RISK__MAX_PORTFOLIO_EXPOSURE_PCT=0.8
KRADER_RISK__DAILY_LOSS_LIMIT=1000000

# Transaction cost (0.00015 = 0.015%)
KRADER_RISK__TRANSACTION_COST_RATE=0.00015

# Max trades per day
KRADER_RISK__MAX_TRADES_PER_DAY=50

# Trading hours (KST)
KRADER_RISK__TRADING_START_HOUR=9
KRADER_RISK__TRADING_START_MINUTE=0
KRADER_RISK__TRADING_END_HOUR=15
KRADER_RISK__TRADING_END_MINUTE=30

# Logging
KRADER_LOGGING__LEVEL=INFO
KRADER_LOGGING__LOG_DIR=logs

# Database
KRADER_DATABASE__PATH=krader.db
```

### Key Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `KRADER_MODE` | string | `paper` | Trading mode: `live`, `paper`, `test` (Note: Kiwoom auto-detects paper/live from server) |
| `KRADER_STRATEGY` | string | `pullback_v1` | Strategy name (see registry) |
| `KRADER_RISK__POSITION_SIZE_PCT` | float | `0.05` | Position size as % of equity (5%) |
| `KRADER_RISK__TRANSACTION_COST_RATE` | float | `0.00015` | Fee as % of notional (0.015%) |
| `KRADER_RISK__MAX_TRADES_PER_DAY` | int | `50` | Max orders per day |

---

## CLI Arguments

```bash
python main.py [OPTIONS]
```

| Argument | Description |
|----------|-------------|
| `--mode` | Trading mode: `live`, `paper`, `test` |
| `--broker` | Broker type: `kiwoom`, `mock` |
| `--account` | Account number |
| `--strategy` | Strategy name to run |
| `--log-level` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--db` | Database file path |
| `--list-strategies` | List available strategies and exit |

### Examples

```bash
# List available strategies
python main.py --list-strategies

# Run with specific strategy
python main.py --strategy=pullback_v1

# Test mode with mock broker
python main.py --mode=test --strategy=pullback_v1

# Paper trading with custom log level
python main.py --mode=paper --log-level=DEBUG
```

---

## Risk Configuration

### Transaction Cost Rate

**What it is:** A flat percentage fee applied to each trade's notional value.

```
estimated_fee = price × quantity × transaction_cost_rate
```

**Configuration:**
```bash
# 0.015% (typical Korean stock commission)
KRADER_RISK__TRANSACTION_COST_RATE=0.00015
```

**Where it's applied:**
1. **BUY orders:** Cash check includes fee (`cash >= notional + fee`)
2. **Max buyable quantity:** Reduced to account for fees
3. **Logging:** Estimated fee logged with each approved signal

**Valid range:** 0.0 to 0.02 (0% to 2%)

**Limitations:**
- This is an approximation - actual broker fees may vary
- Does not model slippage
- Does not account for exchange fees, taxes, or other costs

**Example:**
```
Price: 50,000 KRW
Quantity: 100 shares
Rate: 0.00015 (0.015%)

Notional: 50,000 × 100 = 5,000,000 KRW
Fee: 5,000,000 × 0.00015 = 750 KRW
Total: 5,000,750 KRW
```

### Position Size Per Trade

**What it is:** The percentage of total equity used for each trade.

**Configuration:**
```bash
# 5% of equity per trade
KRADER_RISK__POSITION_SIZE_PCT=0.05
```

**Valid range:** 0.01 to 0.5 (1% to 50%)

**How it works:**
```
quantity = (total_equity × position_size_pct) / current_price
```

**Example:**
```
Total equity: 10,000,000 KRW
Position size: 5%
Stock price: 50,000 KRW

Target value: 10,000,000 × 0.05 = 500,000 KRW
Quantity: 500,000 / 50,000 = 10 shares
```

**Additional limits applied:**
- Cannot exceed `max_position_size` (max shares per symbol)
- Cannot exceed available cash (with transaction costs)
- Cannot exceed `max_portfolio_exposure_pct`

**When strategy specifies quantity:**
If the strategy provides `suggested_quantity`, that value is used instead (subject to the same limits).

---

### Max Trades Per Day

**What it is:** A hard limit on the number of orders submitted per trading day.

**Configuration:**
```bash
KRADER_RISK__MAX_TRADES_PER_DAY=50
```

**Valid range:** 1 to 1000

**Behavior:**
- Counts all orders created today (regardless of status)
- Rejects new signals when limit is reached
- Resets at midnight (local time)
- Persists across restarts (loaded from database)

**Rejection message:**
```
Signal rejected: abc123 - Max trades per day reached (50/50)
```

---

## Strategy Selection

### How It Works

Krader runs exactly **ONE** strategy per execution. The strategy is selected via:

1. CLI: `--strategy=name`
2. Environment: `KRADER_STRATEGY=name`
3. Default: `pullback_v1`

### Available Strategies

```bash
# List strategies
python main.py --list-strategies

# Output:
Available strategies:
  - pullback_v1
```

### Strategy Registry

Strategies are registered in `krader/strategy/registry.py`:

```python
STRATEGY_REGISTRY = {
    "pullback_v1": PullbackV1,
    # Add more strategies here
}
```

### Running a Strategy

```bash
# Via environment
export KRADER_STRATEGY=pullback_v1
python main.py

# Via CLI (overrides environment)
python main.py --strategy=pullback_v1

# Startup output shows selected strategy:
Starting Krader in paper mode with kiwoom broker...
Strategy: pullback_v1
Max trades/day: 50
Transaction cost rate: 0.0150%
```

---

## Adding New Strategies

### Step 1: Create Strategy File

Create `krader/strategy/my_strategy.py`:

```python
"""My custom strategy."""

from datetime import datetime
from uuid import uuid4

from krader.strategy.base import BaseStrategy, MarketSnapshot, StrategyContext
from krader.strategy.signal import Signal


class MyStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "my_strategy"

    @property
    def symbols(self) -> list[str]:
        return []  # Empty = use dynamic universe

    async def on_market_data(
        self,
        snapshot: MarketSnapshot,
        context: StrategyContext,
    ) -> list[Signal]:
        # Your strategy logic here
        return []
```

### Step 2: Register Strategy

Edit `krader/strategy/registry.py`:

```python
def _lazy_load_strategies() -> None:
    global STRATEGY_REGISTRY

    if STRATEGY_REGISTRY:
        return

    from krader.strategy.pullback_v1 import PullbackV1
    from krader.strategy.my_strategy import MyStrategy  # Add import

    STRATEGY_REGISTRY.update({
        "pullback_v1": PullbackV1,
        "my_strategy": MyStrategy,  # Add registration
    })
```

### Step 3: Run Your Strategy

```bash
python main.py --strategy=my_strategy
```

---

## Daily Trades Tracking

### How Trade Count Works

1. **Counted event:** Order submission (when `process_approved_signal` succeeds)
2. **Storage:** Database `orders` table
3. **Query:** `COUNT(*) FROM orders WHERE created_at >= today_midnight`
4. **Reset:** Automatic at midnight (local time)

### Startup Behavior

On startup, Krader loads the current day's trade count from the database:

```
INFO: Daily trades count at startup: 12
```

This ensures the limit is preserved across restarts.

### Monitoring

```bash
# Check trade count in logs
grep "Daily trades count" logs/app.log

# Query database directly
sqlite3 krader.db "SELECT COUNT(*) FROM orders WHERE created_at >= strftime('%s', 'now', 'start of day')"
```

---

## Configuration Validation

### Pydantic Validation

All config values are validated using pydantic:

| Field | Constraint |
|-------|------------|
| `transaction_cost_rate` | 0.0 ≤ value ≤ 0.02 |
| `max_trades_per_day` | 1 ≤ value ≤ 1000 |
| `strategy` | Non-empty string |

### Invalid Configuration Errors

```python
# Invalid transaction cost rate
KRADER_RISK__TRANSACTION_COST_RATE=0.05  # > 0.02
# Error: Input should be less than or equal to 0.02

# Invalid max trades
KRADER_RISK__MAX_TRADES_PER_DAY=0
# Error: Input should be greater than or equal to 1

# Unknown strategy
python main.py --strategy=unknown
# Error: Strategy 'unknown' not found. Available strategies: ['pullback_v1']
```

---

## Quick Reference

```bash
# Minimal production setup
export KRADER_MODE=paper
export KRADER_BROKER__ACCOUNT_NUMBER=1234567890
export KRADER_STRATEGY=pullback_v1
export KRADER_RISK__MAX_TRADES_PER_DAY=20
export KRADER_RISK__TRANSACTION_COST_RATE=0.00015
python main.py

# Test run
python main.py --mode=test --strategy=pullback_v1

# Debug mode
python main.py --mode=test --log-level=DEBUG
```
