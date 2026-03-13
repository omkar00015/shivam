"""Initialize TimescaleDB schema."""
import asyncio
import os
import asyncpg
from dotenv import load_dotenv
load_dotenv()
async def init_schema():
    url = os.environ["DATABASE_URL"]
    print(f"Connecting to DB...")
    conn = await asyncpg.connect(url)

    print("Creating TimescaleDB extension...")
    await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")

    print("Creating system_state table...")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            id SERIAL,
            snapshot_at TIMESTAMPTZ NOT NULL,
            instrument VARCHAR(20) NOT NULL,
            component VARCHAR(50) NOT NULL,
            state_json JSONB NOT NULL,
            state_hash VARCHAR(64) NOT NULL
        );
    """)

    print("Creating trades table...")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id VARCHAR(16) PRIMARY KEY,
            instrument VARCHAR(20) NOT NULL,
            direction VARCHAR(5) NOT NULL,
            entry_price NUMERIC(20,8) NOT NULL,
            stop_price NUMERIC(20,8) NOT NULL,
            target_price NUMERIC(20,8) NOT NULL,
            position_size NUMERIC(20,8) NOT NULL,
            setup_type VARCHAR(30) NOT NULL,
            opened_at TIMESTAMPTZ NOT NULL,
            closed_at TIMESTAMPTZ,
            pnl NUMERIC(20,8),
            status VARCHAR(20) NOT NULL
        );
    """)

    print("Creating bars table...")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS bars (
            instrument VARCHAR(20) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            timestamp_start TIMESTAMPTZ NOT NULL,
            timestamp_end TIMESTAMPTZ,
            open NUMERIC(20,8) NOT NULL,
            high NUMERIC(20,8) NOT NULL,
            low NUMERIC(20,8) NOT NULL,
            close NUMERIC(20,8) NOT NULL,
            volume NUMERIC(20,8) NOT NULL,
            is_complete BOOLEAN NOT NULL DEFAULT TRUE,
            is_reliable BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (instrument, timeframe, timestamp_start)
        );
    """)

    print("Migrating bars table (adding columns if missing)...")
    await conn.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS timestamp_end TIMESTAMPTZ;")
    await conn.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS is_complete BOOLEAN NOT NULL DEFAULT TRUE;")
    await conn.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS is_reliable BOOLEAN NOT NULL DEFAULT TRUE;")

    print("Converting bars to hypertable...")
    try:
        await conn.execute(
            "SELECT create_hypertable('bars', 'timestamp_start', if_not_exists => TRUE);"
        )
        print("Hypertable created.")
    except Exception as e:
        print(f"Hypertable note: {e}")

    await conn.close()
    print("Schema initialized successfully.")
if __name__ == "__main__":
    asyncio.run(init_schema())
