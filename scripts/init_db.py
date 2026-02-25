"""Doc 2 §3 + Doc 9: Initialize TimescaleDB schema for bar storage.

Creates the bars hypertable and all required indexes.
Uses NUMERIC columns to preserve Decimal precision exactly.
"""

import os
import sys

import psycopg2


def get_connection():
    """Connect to TimescaleDB using env vars or defaults."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "trading"),
        user=os.getenv("DB_USER", "prasad"),
        password=os.getenv("DB_PASSWORD", "your_32_char_password"),
    )


CREATE_BARS_TABLE = """
CREATE TABLE IF NOT EXISTS bars (
    timestamp_start  TIMESTAMPTZ     NOT NULL,
    timestamp_end    TIMESTAMPTZ     NOT NULL,
    instrument       TEXT            NOT NULL,
    timeframe        TEXT            NOT NULL,
    open             NUMERIC(20, 8)  NOT NULL,
    high             NUMERIC(20, 8)  NOT NULL,
    low              NUMERIC(20, 8)  NOT NULL,
    close            NUMERIC(20, 8)  NOT NULL,
    volume           NUMERIC(30, 8)  NOT NULL,
    is_complete      BOOLEAN         NOT NULL DEFAULT FALSE,
    is_reliable      BOOLEAN         NOT NULL DEFAULT TRUE,
    PRIMARY KEY (instrument, timeframe, timestamp_start)
);
"""

CREATE_HYPERTABLE = """
SELECT create_hypertable(
    'bars',
    by_range('timestamp_start'),
    if_not_exists => TRUE,
    migrate_data  => TRUE
);
"""

INDEXES = [
    """CREATE INDEX IF NOT EXISTS idx_bars_instrument_tf_time
       ON bars (instrument, timeframe, timestamp_start DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_bars_complete
       ON bars (instrument, timeframe, is_complete)
       WHERE is_complete = TRUE;""",
    """CREATE INDEX IF NOT EXISTS idx_bars_end_time
       ON bars (timestamp_end DESC);""",
]


def init_db():
    """Create bars table, convert to hypertable, add indexes."""
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()

    # Enable TimescaleDB extension
    cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    print("[OK] TimescaleDB extension enabled")

    # Create table
    cur.execute(CREATE_BARS_TABLE)
    print("[OK] bars table created")

    # Convert to hypertable
    cur.execute(CREATE_HYPERTABLE)
    print("[OK] bars converted to hypertable")

    # Create indexes
    for idx_sql in INDEXES:
        cur.execute(idx_sql)
    print(f"[OK] {len(INDEXES)} indexes created")

    cur.close()
    conn.close()
    print("[DONE] Database initialization complete")


if __name__ == "__main__":
    try:
        init_db()
    except psycopg2.OperationalError as e:
        print(f"[FAIL] Cannot connect to database: {e}", file=sys.stderr)
        sys.exit(1)
