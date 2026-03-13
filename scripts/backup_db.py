"""Daily database backup to backups/ directory.

Usage:
    python scripts/backup_db.py

Runs pg_dump inside the prasad-timescaledb Docker container, copies the
dump file out, and keeps only the 7 most recent backups (auto-rotation).

Intended to be run as a daily cron job on the VPS:
    0 3 * * * cd /root/prasad-algo && python scripts/backup_db.py >> logs/backup.log 2>&1
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKUP_DIR = Path("backups")
CONTAINER_NAME = "prasad-timescaledb"
DB_USER = "prasad"
DB_NAME = "trading"
KEEP_LAST = 7  # number of backups to retain


def main() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    filename = BACKUP_DIR / f"trading_{timestamp}.dump"

    print(f"[{timestamp}] Starting database backup...")

    # Step 1: pg_dump inside the Docker container
    result = subprocess.run(
        [
            "docker", "exec", CONTAINER_NAME,
            "pg_dump", "-U", DB_USER, "-d", DB_NAME,
            "-F", "c", "-f", "/tmp/backup.dump",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Backup FAILED: {result.stderr}")
        sys.exit(1)

    # Step 2: Copy the dump file out of the container
    cp_result = subprocess.run(
        [
            "docker", "cp",
            f"{CONTAINER_NAME}:/tmp/backup.dump",
            str(filename),
        ],
        capture_output=True,
        text=True,
    )

    if cp_result.returncode != 0:
        print(f"Copy FAILED: {cp_result.stderr}")
        sys.exit(1)

    size_mb = filename.stat().st_size / (1024 * 1024)
    print(f"Backup saved: {filename} ({size_mb:.1f} MB)")

    # Step 3: Rotate — keep only the last N backups
    dumps = sorted(BACKUP_DIR.glob("*.dump"))
    removed = 0
    for old in dumps[:-KEEP_LAST]:
        old.unlink()
        removed += 1
    if removed:
        print(f"Rotated: removed {removed} old backup(s), keeping last {KEEP_LAST}.")

    print("Done.")


if __name__ == "__main__":
    main()
