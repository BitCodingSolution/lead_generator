"""
Send every pending draft (sent_at IS NULL AND outlook_entry_id NOT NULL) directly
from DB, independent of any batch xlsx file. Handles drafts that span multiple
batches. Uses human jitter by default.

Exits non-zero if any send fails, so the orchestrator surfaces the error.

Usage:
    python send_pending.py                    # send all pending, with jitter
    python send_pending.py --limit 5          # cap at N sends
    python send_pending.py --no-jitter        # back-to-back (risky)
    python send_pending.py --dry-run          # list only
"""
import argparse
import datetime as dt
import random
import sqlite3
import sys
import time

import win32com.client

DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'
FROM_EMAIL = 'pradip@bitcodingsolutions.com'
MIN_JITTER_SECONDS = 25
MAX_JITTER_SECONDS = 90


def find_account(outlook, email):
    for acc in outlook.Session.Accounts:
        try:
            if acc.SmtpAddress.lower() == email.lower():
                return acc
        except Exception:
            pass
    return None


def dismiss_reading_pane(outlook, account):
    """Outlook errors with 'inline response mail item' if a draft is being
    previewed in the reading pane. Switch the active explorer to Inbox so no
    draft is selected, which releases the inline lock."""
    try:
        exp = outlook.ActiveExplorer()
        if exp:
            inbox = account.DeliveryStore.GetDefaultFolder(6)  # Inbox
            exp.CurrentFolder = inbox
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--no-jitter', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    rows = con.execute("""
        SELECT e.id, e.lead_id, e.outlook_entry_id, e.subject, l.email, l.name
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        WHERE e.sent_at IS NULL AND e.outlook_entry_id IS NOT NULL
        ORDER BY e.id ASC
    """).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    print(f"Pending drafts to send: {len(rows)}")
    for r in rows:
        print(f"  [{r[1]}] {r[4]}  |  {(r[3] or '')[:55]}")
    if not rows:
        return 0
    if args.dry_run:
        print("[DRY RUN] No emails sent.")
        return 0

    outlook = win32com.client.Dispatch('Outlook.Application')
    account = find_account(outlook, FROM_EMAIL)
    if not account:
        print(f"ERROR: account {FROM_EMAIL} not in Outlook.", file=sys.stderr)
        return 2
    ns = outlook.GetNamespace('MAPI')
    dismiss_reading_pane(outlook, account)

    sent_count = 0
    errors = []
    for idx, (email_row_id, lead_id, eid, subject, email, name) in enumerate(rows):
        try:
            item = ns.GetItemFromID(eid)
            if getattr(item, 'Sent', False):
                # already sent earlier (stale draft state) — just reconcile DB
                print(f"  [{lead_id}] already sent in Outlook, reconciling DB")
            else:
                item.Send()
            sent_ts = dt.datetime.now().isoformat(timespec='seconds')
            con.execute(
                "UPDATE emails_sent SET sent_at=? WHERE id=?",
                (sent_ts, email_row_id),
            )
            con.execute(
                "UPDATE lead_status SET status='Sent', "
                "first_sent_at = COALESCE(first_sent_at, ?), "
                "last_touch_date=?, touch_count=touch_count+1, "
                "updated_at=CURRENT_TIMESTAMP WHERE lead_id=?",
                (sent_ts, dt.date.today().isoformat(), lead_id),
            )
            con.commit()
            sent_count += 1
            print(f"  [{sent_count}/{len(rows)}] SENT  {lead_id}  ->  {email}")

            if idx < len(rows) - 1 and not args.no_jitter:
                wait = random.randint(MIN_JITTER_SECONDS, MAX_JITTER_SECONDS)
                print(f"       waiting {wait}s before next send...")
                time.sleep(wait)
        except Exception as e:
            msg = f"[ERR] {lead_id}: {e}"
            print(msg, file=sys.stderr)
            errors.append(msg)

    # Update today's daily_batches.sent_count
    today = dt.date.today().isoformat()
    total_sent_today = con.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", (today,)
    ).fetchone()[0]
    con.execute(
        "UPDATE daily_batches SET sent_count=? WHERE batch_date=?",
        (total_sent_today, today),
    )
    con.commit()
    con.close()

    print(f"\n[OK] Sent: {sent_count}/{len(rows)}  Errors: {len(errors)}")
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
