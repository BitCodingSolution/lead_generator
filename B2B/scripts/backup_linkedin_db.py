"""
Daily snapshot of the LinkedIn dashboard SQLite DB.

Uses SQLite's online backup API (sqlite3.Connection.backup) so the snapshot
is consistent even while the backend is mid-write. Output lands in
`Database/Backups/leads-YYYY-MM-DD.db`. Keeps the last 14 days; older
files are pruned in the same run.

Wired up as a Scheduled Task by install-autostart.ps1 (daily at 03:15 local).
Manual run:
    python scripts/backup_linkedin_db.py
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

# Mirror the path layout from dashboard/backend/linkedin_db.py without
# importing it — the backup script must work standalone (no PYTHONPATH
# tricks from the Scheduled Task).
BASE = Path(r"H:/Lead Generator/B2B")
SRC = BASE / "Database" / "LinkedIn Data" / "leads.db"
DEST_DIR = BASE / "Database" / "Backups"
KEEP_DAYS = 14


def _backup_to(dest: Path) -> None:
    """Online .backup — copies pages while another process can still hold
    a write lock. Safer than a plain file copy for a live SQLite file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(SRC))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _prune_old(dest_dir: Path, keep_days: int) -> int:
    """Drop snapshots older than `keep_days` based on filename date.
    Returns count removed. Filename-based instead of mtime so a manual
    re-run today doesn't accidentally rescue yesterday's file."""
    cutoff = dt.date.today() - dt.timedelta(days=keep_days)
    removed = 0
    for p in dest_dir.glob("leads-*.db"):
        stem = p.stem  # "leads-2026-05-02"
        try:
            d = dt.date.fromisoformat(stem.split("leads-", 1)[1])
        except (IndexError, ValueError):
            continue
        if d < cutoff:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def main() -> int:
    if not SRC.exists():
        print(f"[backup] source missing: {SRC}", file=sys.stderr)
        return 2
    today = dt.date.today().isoformat()
    dest = DEST_DIR / f"leads-{today}.db"
    _backup_to(dest)
    size_mb = dest.stat().st_size / (1024 * 1024)
    pruned = _prune_old(DEST_DIR, KEEP_DAYS)
    print(f"[backup] wrote {dest} ({size_mb:.1f} MB), pruned {pruned} old")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
