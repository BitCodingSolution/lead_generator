"""
Pick leads for follow-up: sent X days ago, no reply yet, not bounced/DNC.
Writes a new daily batch file ready for generate_followup_drafts.py.

Touch numbers:
  1 = first email (already sent)
  2 = Day-4 gentle follow-up
  3 = Day-8 breakup

Usage:
    python queue_followups.py --touch 2 --days 4 --count 20
    python queue_followups.py --touch 3 --days 8 --count 20
    python queue_followups.py --touch 2 --days 4 --count 20 --dry-run
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--touch', type=int, required=True, choices=[2, 3],
                    help='Which follow-up: 2=gentle, 3=breakup')
    ap.add_argument('--days', type=int, required=True,
                    help='Only pick leads first-sent at least N days ago')
    ap.add_argument('--count', type=int, default=20)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # Pick: status='Sent' (or Replied_OOO), touch_count == (touch-1), first_sent >= N days ago
    touch = args.touch
    cutoff = (dt.datetime.now() - dt.timedelta(days=args.days)).isoformat()

    sql = """
        SELECT l.*, ls.status, ls.touch_count, ls.first_sent_at
        FROM leads l
        JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE ls.status IN ('Sent','Replied_OOO')
          AND ls.touch_count = ?
          AND ls.first_sent_at IS NOT NULL
          AND ls.first_sent_at <= ?
        ORDER BY ls.first_sent_at
        LIMIT ?
    """
    rows = con.execute(sql, (touch - 1, cutoff, args.count)).fetchall()

    if not rows:
        print("No eligible leads for this follow-up.")
        con.close()
        return

    df = pd.DataFrame([dict(r) for r in rows])
    print(f"Picked {len(df)} leads for Touch {touch} (Day-{args.days} follow-up):")
    print(df[['lead_id', 'name', 'company', 'industry', 'first_sent_at']].to_string(index=False))

    today = dt.date.today().isoformat()
    label = 'followup2' if touch == 2 else 'breakup3'
    out_path = os.path.join(BATCHES_DIR, f'{today}_{label}.xlsx')

    if args.dry_run:
        print("\n[DRY RUN] No DB or file changes.")
        con.close()
        return

    # Fetch prior subject lines so generator can avoid reusing them
    prior_subjects = {}
    for _, r in df.iterrows():
        s = con.execute(
            "SELECT subject FROM emails_sent WHERE lead_id=? ORDER BY touch_number DESC",
            (r['lead_id'],),
        ).fetchall()
        prior_subjects[r['lead_id']] = '|'.join(x[0] for x in s if x[0])

    df['batch_date'] = today
    df['touch_number'] = touch
    df['prior_subjects'] = df['lead_id'].map(prior_subjects)
    df['draft_subject'] = ''
    df['draft_body'] = ''
    df['draft_language'] = ''
    df['generated_at'] = ''
    df['outlook_entry_id'] = ''
    df['sent_at'] = ''
    df['notes'] = ''

    with pd.ExcelWriter(out_path, engine='xlsxwriter',
                        engine_kwargs={'options': {'strings_to_urls': False}}) as w:
        df.to_excel(w, sheet_name='Batch', index=False)

    con.execute(
        "INSERT OR REPLACE INTO daily_batches "
        "(batch_date, leads_picked, drafts_generated, sent_count, replies_count, notes) "
        "VALUES (?, ?, 0, 0, 0, ?)",
        (today, len(df), f'Follow-up touch={touch}, days={args.days}'),
    )
    con.commit()
    con.close()

    print(f"\n[OK] Batch file: {out_path}")
    print(f"     Next: python scripts/generate_drafts.py --file '{out_path}'")


if __name__ == '__main__':
    main()
