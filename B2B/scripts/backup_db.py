"""
Nightly SQLite backup for leads.db.

Writes a timestamped copy into Database/Marcel Data/backups/ using SQLite's
online backup API (safe while the app is running), then prunes files older
than RETENTION_DAYS so the folder doesn't grow unbounded.

Usage:
    python scripts/backup_db.py
    python scripts/backup_db.py --retention-days 14

Schedule:
    Register in Windows Task Scheduler to run daily, or invoke via a
    cron-like loop. Exit code is non-zero only when the backup itself
    fails; prune errors are logged but non-fatal.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

DB = Path(r"H:/Lead Generator/B2B/Database/Marcel Data/leads.db")
BACKUP_DIR = DB.parent / "backups"
RETENTION_DAYS = 7


def backup(src: Path, dest: Path) -> None:
    """Use sqlite3's online backup so we don't race with live writes."""
    with sqlite3.connect(str(src)) as src_con, sqlite3.connect(str(dest)) as dst_con:
        src_con.backup(dst_con)


def prune(directory: Path, retention_days: int) -> int:
    """Delete backup files older than cutoff. Returns count removed."""
    cutoff = dt.datetime.now() - dt.timedelta(days=retention_days)
    removed = 0
    for p in directory.glob("leads_*.db"):
        try:
            mtime = dt.datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                p.unlink()
                removed += 1
        except Exception as e:
            print(f"[warn] could not prune {p.name}: {e}", file=sys.stderr)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    args = ap.parse_args()

    if not DB.exists():
        print(f"[err] source DB not found: {DB}", file=sys.stderr)
        return 1

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"leads_{stamp}.db"

    try:
        backup(DB, dest)
    except Exception as e:
        print(f"[err] backup failed: {e}", file=sys.stderr)
        if dest.exists():
            try:
                dest.unlink()
            except Exception:
                pass
        return 2

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"[OK] backup written: {dest.name}  ({size_mb:.1f} MB)")

    removed = prune(BACKUP_DIR, args.retention_days)
    if removed:
        print(f"[OK] pruned {removed} backup(s) older than {args.retention_days}d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
