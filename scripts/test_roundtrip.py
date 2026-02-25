"""Doc 1 §4 + Doc 2 §3: Verify Decimal precision survives DB round-trip.

Inserts one bar with known Decimal values, reads it back,
and asserts every numeric field matches exactly.
"""

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import psycopg2
import psycopg2.extras


def get_connection():
    """Connect to TimescaleDB using env vars or defaults."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "trading"),
        user=os.getenv("DB_USER", "prasad"),
        password=os.getenv("DB_PASSWORD", "your_32_char_password"),
    )


# Test bar with deliberately precise Decimal values
TEST_BAR = {
    "timestamp_start": datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    "timestamp_end": datetime(2025, 1, 15, 12, 15, 0, tzinfo=timezone.utc),
    "instrument": "BTC_USDT",
    "timeframe": "M15",
    "open": Decimal("97234.56789012"),
    "high": Decimal("97456.78901234"),
    "low": Decimal("97100.12345678"),
    "close": Decimal("97350.00000001"),
    "volume": Decimal("1234.56780000"),
    "is_complete": True,
    "is_reliable": True,
}

INSERT_SQL = """
INSERT INTO bars (
    timestamp_start, timestamp_end, instrument, timeframe,
    open, high, low, close, volume, is_complete, is_reliable
) VALUES (
    %(timestamp_start)s, %(timestamp_end)s, %(instrument)s, %(timeframe)s,
    %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s,
    %(is_complete)s, %(is_reliable)s
)
ON CONFLICT (instrument, timeframe, timestamp_start) DO NOTHING;
"""

SELECT_SQL = """
SELECT timestamp_start, timestamp_end, instrument, timeframe,
       open, high, low, close, volume, is_complete, is_reliable
FROM bars
WHERE instrument = %(instrument)s
  AND timeframe = %(timeframe)s
  AND timestamp_start = %(timestamp_start)s;
"""

CLEANUP_SQL = """
DELETE FROM bars
WHERE instrument = %(instrument)s
  AND timeframe = %(timeframe)s
  AND timestamp_start = %(timestamp_start)s;
"""


def test_roundtrip():
    """Insert a bar, read it back, assert exact Decimal match."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # Insert
        cur.execute(INSERT_SQL, TEST_BAR)
        conn.commit()
        print("[OK] Bar inserted")

        # Read back
        cur.execute(SELECT_SQL, TEST_BAR)
        row = cur.fetchone()

        if row is None:
            print("[FAIL] No row returned after insert", file=sys.stderr)
            sys.exit(1)

        print("[OK] Bar retrieved")

        # Assert each field
        failures = []

        # Timestamps (compare as UTC)
        for ts_field in ("timestamp_start", "timestamp_end"):
            expected = TEST_BAR[ts_field]
            actual = row[ts_field]
            # psycopg2 returns timezone-aware datetimes
            if actual.tzinfo is None:
                actual = actual.replace(tzinfo=timezone.utc)
            if expected != actual:
                failures.append(f"  {ts_field}: expected {expected!r}, got {actual!r}")

        # Text fields
        for text_field in ("instrument", "timeframe"):
            if TEST_BAR[text_field] != row[text_field]:
                failures.append(
                    f"  {text_field}: expected {TEST_BAR[text_field]!r}, "
                    f"got {row[text_field]!r}"
                )

        # Decimal fields — exact match required (Doc 1 §4)
        for dec_field in ("open", "high", "low", "close", "volume"):
            expected = TEST_BAR[dec_field]
            actual = row[dec_field]
            if not isinstance(actual, Decimal):
                failures.append(
                    f"  {dec_field}: expected Decimal, got {type(actual).__name__}"
                )
            elif expected != actual:
                failures.append(
                    f"  {dec_field}: expected {expected}, got {actual}"
                )

        # Boolean fields
        for bool_field in ("is_complete", "is_reliable"):
            if TEST_BAR[bool_field] != row[bool_field]:
                failures.append(
                    f"  {bool_field}: expected {TEST_BAR[bool_field]}, "
                    f"got {row[bool_field]}"
                )

        if failures:
            print("[FAIL] Round-trip mismatches:", file=sys.stderr)
            for f in failures:
                print(f, file=sys.stderr)
            sys.exit(1)

        print("[OK] All fields match exactly")
        print(f"     open   = {row['open']} (type: {type(row['open']).__name__})")
        print(f"     high   = {row['high']} (type: {type(row['high']).__name__})")
        print(f"     low    = {row['low']} (type: {type(row['low']).__name__})")
        print(f"     close  = {row['close']} (type: {type(row['close']).__name__})")
        print(f"     volume = {row['volume']} (type: {type(row['volume']).__name__})")
        print("[PASS] Decimal round-trip verified")

    finally:
        # Clean up test data
        cur.execute(CLEANUP_SQL, TEST_BAR)
        conn.commit()
        print("[OK] Test data cleaned up")
        cur.close()
        conn.close()


if __name__ == "__main__":
    try:
        test_roundtrip()
    except psycopg2.OperationalError as e:
        print(f"[FAIL] Cannot connect to database: {e}", file=sys.stderr)
        sys.exit(1)
