"""Database schema definitions."""

SCHEMA = [
    # Market data - candles
    """
    CREATE TABLE IF NOT EXISTS candles (
        id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        open_time INTEGER NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume INTEGER NOT NULL,
        UNIQUE(symbol, timeframe, open_time)
    )
    """,
    # Index for candle queries
    """
    CREATE INDEX IF NOT EXISTS idx_candles_symbol_timeframe
    ON candles(symbol, timeframe, open_time DESC)
    """,
    # Trading signals
    """
    CREATE TABLE IF NOT EXISTS signals (
        signal_id TEXT PRIMARY KEY,
        strategy_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        confidence REAL NOT NULL,
        reason TEXT,
        suggested_quantity INTEGER,
        metadata TEXT,
        created_at INTEGER NOT NULL
    )
    """,
    # Orders
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        broker_order_id TEXT,
        signal_id TEXT REFERENCES signals(signal_id),
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        order_type TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        filled_quantity INTEGER DEFAULT 0,
        price REAL,
        status TEXT NOT NULL,
        reject_reason TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    # Index for order queries
    """
    CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_orders_symbol
    ON orders(symbol, created_at DESC)
    """,
    # Fills
    """
    CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,
        order_id TEXT REFERENCES orders(order_id),
        broker_fill_id TEXT,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        commission REAL,
        filled_at INTEGER NOT NULL
    )
    """,
    # Index for fill queries
    """
    CREATE INDEX IF NOT EXISTS idx_fills_order
    ON fills(order_id)
    """,
    # Positions (derived, but cached for fast access)
    """
    CREATE TABLE IF NOT EXISTS positions (
        symbol TEXT PRIMARY KEY,
        quantity INTEGER NOT NULL,
        avg_price REAL NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    # System state - bot runs
    """
    CREATE TABLE IF NOT EXISTS bot_runs (
        run_id TEXT PRIMARY KEY,
        started_at INTEGER NOT NULL,
        ended_at INTEGER,
        status TEXT NOT NULL,
        error_message TEXT
    )
    """,
    # Error log
    """
    CREATE TABLE IF NOT EXISTS errors (
        id INTEGER PRIMARY KEY,
        run_id TEXT REFERENCES bot_runs(run_id),
        error_type TEXT NOT NULL,
        message TEXT NOT NULL,
        context TEXT,
        occurred_at INTEGER NOT NULL
    )
    """,
    # Index for error queries
    """
    CREATE INDEX IF NOT EXISTS idx_errors_run
    ON errors(run_id, occurred_at DESC)
    """,
]
