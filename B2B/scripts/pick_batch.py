"""
Pick N leads from a target industry where Status='New', for today's batch.

Usage:
    python pick_batch.py --industry "Health Care" --count 5
    python pick_batch.py --industry "Management Consulting" --count 20
    python pick_batch.py --tier 1 --count 10
    python pick_batch.py --dry-run ...        (preview only, no DB change)

Output:
  - Inserts a row into `daily_batches`
  - Updates `lead_status.status` = 'Picked' for selected leads
  - Writes audit file: 01_Daily_Batches/YYYY-MM-DD_<industry>.xlsx
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import pandas as pd

BASE = r'H:/Lead Generator/B2B/Database/Marcel Data'
DB = os.path.join(BASE, 'leads.db')
BATCHES_DIR = os.path.join(BASE, '01_Daily_Batches')
os.makedirs(BATCHES_DIR, exist_ok=True)


def pick(industry=None, tier=None, count=10, city=None, dry_run=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # Build query
    where = ["ls.status = 'New'", "l.is_owner = 1",
             "(l.email_valid IS NULL OR l.email_valid = 1)",
             "l.email NOT IN (SELECT email FROM do_not_contact)"]
    params = []
    if industry:
        where.append("l.industry = ?")
        params.append(industry)
    if tier:
        where.append("l.tier = ?")
        params.append(tier)
    if city:
        where.append("l.city = ?")
        params.append(city)

    sql = f"""
        SELECT l.*, ls.status
        FROM leads l
        JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE {' AND '.join(where)}
        ORDER BY l.lead_id
        LIMIT ?
    """
    params.append(count)
    rows = con.execute(sql, params).fetchall()

    if not rows:
        print("No matching leads found with status='New'.")
        con.close()
        return

    leads_df = pd.DataFrame([dict(r) for r in rows])
    print(f"\nPicked {len(leads_df)} leads:")
    print(leads_df[['lead_id', 'name', 'company', 'city', 'industry']].to_string(index=False))

    today = dt.date.today().isoformat()
    safe_ind = (industry or f"tier{tier}" or "mixed").replace('/', '_').replace(' ', '_')
    out_path = os.path.join(BATCHES_DIR, f'{today}_{safe_ind}.xlsx')

    if dry_run:
        print("\n[DRY RUN] No DB changes. No file written.")
        con.close()
        return

    # Add batch columns for downstream scripts
    leads_df['batch_date'] = today
    leads_df['draft_subject'] = ''
    leads_df['draft_body'] = ''
    leads_df['draft_language'] = ''
    leads_df['generated_at'] = ''
    leads_df['outlook_entry_id'] = ''
    leads_df['sent_at'] = ''
    leads_df['notes'] = ''

    # Write file FIRST so DB never goes ahead of the on-disk artifact.
    # If Excel write crashes, leads stay 'New' and pipeline can be retried.
    with pd.ExcelWriter(out_path, engine='xlsxwriter',
                        engine_kwargs={'options': {'strings_to_urls': False}}) as w:
        leads_df.to_excel(w, sheet_name='Batch', index=False)

    # Now commit DB state in one transaction
    lead_ids = leads_df['lead_id'].tolist()
    placeholders = ','.join(['?'] * len(lead_ids))
    con.execute(
        f"UPDATE lead_status SET status='Picked', updated_at=CURRENT_TIMESTAMP "
        f"WHERE lead_id IN ({placeholders})",
        lead_ids,
    )
    con.execute(
        "INSERT OR REPLACE INTO daily_batches "
        "(batch_date, leads_picked, drafts_generated, sent_count, replies_count, notes) "
        "VALUES (?, ?, 0, 0, 0, ?)",
        (today, len(leads_df), f"industry={industry}, tier={tier}, city={city}"),
    )
    con.commit()
    con.close()

    print(f"\n[OK] Batch file: {out_path}")
    print(f"     Status updated: {len(leads_df)} leads -> 'Picked'")
    print(f"     Next: run generate_drafts.py --file '{out_path}'")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--industry', type=str, default=None)
    ap.add_argument('--tier', type=int, default=None, choices=[1, 2, 3, 4])
    ap.add_argument('--city', type=str, default=None)
    ap.add_argument('--count', type=int, default=10)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not args.industry and not args.tier:
        print("Error: provide --industry or --tier", file=sys.stderr)
        sys.exit(1)

    pick(industry=args.industry, tier=args.tier, count=args.count,
         city=args.city, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
