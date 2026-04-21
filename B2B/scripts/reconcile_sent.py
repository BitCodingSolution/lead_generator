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
    try:
        df = pd.read_excel(path)
    except Exception as e:
        print(f"[skip] {path.name}: read failed — {e}", file=sys.stderr)
        return 0
    if 'lead_id' not in df.columns or 'sent_at' not in df.columns:
        return 0
    fixed = 0
    for i, r in df.iterrows():
        sa = str(r.get('sent_at', '')).strip()
        if sa and sa.lower() != 'nan':
            continue
        lid = r['lead_id']
        row = con.execute(
            "SELECT sent_at FROM emails_sent "
            "WHERE lead_id=? AND sent_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (lid,),
        ).fetchone()
        if row and row[0]:
            if not dry_run:
                df.at[i, 'sent_at'] = row[0]
            fixed += 1
            print(f"  {path.name}  [{lid}]  <- {row[0]}")
    if fixed and not dry_run:
        with pd.ExcelWriter(
            path, engine='xlsxwriter',
            engine_kwargs={'options': {'strings_to_urls': False}},
        ) as w:
            df.to_excel(w, sheet_name='Batch', index=False)
    return fixed


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
