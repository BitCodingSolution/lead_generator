"""
reconcile_sent.py — fix Marcel batch Excel files whose `sent_at` column is
out of sync with the DB.

This happens when send_drafts.py commits a row to the DB but crashes/stops
before the Excel writeback. The DB has the truth; the Excel file is merely a
mirror. This script re-syncs the mirror.

Usage:
    python scripts/reconcile_sent.py                    # all Marcel batches
    python scripts/reconcile_sent.py --file <path.xlsx> # one file
    python scripts/reconcile_sent.py --dry-run          # report only, no writes
"""
from __future__ import annotations

import argparse
import glob
import sqlite3
import sys
from pathlib import Path

import pandas as pd

DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'
DEFAULT_DIR = Path(r'H:\Lead Generator\B2B\Database\Marcel Data\01_Daily_Batches')


def reconcile(path: Path, con: sqlite3.Connection, dry_run: bool) -> int:
    """Match rows by `outlook_entry_id` (unique per draft/per file) so we
    don't wrongly mark a row as sent when the same lead was re-picked into
    a later batch and sent from there. A row without an outlook_entry_id
    in this file was never drafted from it — leave it untouched."""
    try:
        df = pd.read_excel(path)
    except Exception as e:
        print(f"[skip] {path.name}: read failed — {e}", file=sys.stderr)
        return 0
    needed = {'outlook_entry_id', 'sent_at'}
    if not needed.issubset(df.columns):
        return 0
    fixed = 0
    ghosts = 0
    for i, r in df.iterrows():
        sa = str(r.get('sent_at', '')).strip()
        oe = str(r.get('outlook_entry_id', '')).strip()
        has_sa = sa and sa.lower() != 'nan'
        has_oe = oe and oe.lower() != 'nan'

        # Clean up any ghost sent_at values that were set but have no matching
        # outlook_entry_id in this batch — those were contaminated by an
        # older lead_id-based reconcile. Clear them.
        if has_sa and not has_oe:
            if not dry_run:
                df.at[i, 'sent_at'] = None
            ghosts += 1
            print(f"  {path.name}  GHOST cleared [{r.get('lead_id','?')}]  (sent_at={sa})")
            continue

        if has_sa or not has_oe:
            continue  # already correct, or no draft in this file

        row = con.execute(
            "SELECT sent_at FROM emails_sent "
            "WHERE outlook_entry_id=? AND sent_at IS NOT NULL LIMIT 1",
            (oe,),
        ).fetchone()
        if row and row[0]:
            if not dry_run:
                df.at[i, 'sent_at'] = row[0]
            fixed += 1
            print(f"  {path.name}  [{r.get('lead_id','?')}]  <- {row[0]}")

    if (fixed or ghosts) and not dry_run:
        with pd.ExcelWriter(
            path, engine='xlsxwriter',
            engine_kwargs={'options': {'strings_to_urls': False}},
        ) as w:
            df.to_excel(w, sheet_name='Batch', index=False)
    return fixed + ghosts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', help='single .xlsx to reconcile')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    files = (
        [Path(args.file)]
        if args.file
        else [Path(p) for p in sorted(glob.glob(str(DEFAULT_DIR / '*.xlsx')))]
    )
    con = sqlite3.connect(DB)
    total = 0
    for f in files:
        total += reconcile(f, con, args.dry_run)
    con.close()
    print(f"\n{'[DRY] ' if args.dry_run else ''}Reconciled {total} rows across "
          f"{len(files)} file(s).")


if __name__ == '__main__':
    main()
