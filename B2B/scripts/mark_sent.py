"""
Scan Outlook Sent Items for our drafts that have been sent.
Updates DB: emails_sent.sent_at + lead_status='Sent' + touch_count++.

Run: python mark_sent.py
Run: python mark_sent.py --days 7        (only look at last 7 days of Sent)
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import win32com.client

DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'
FROM_EMAIL = 'pradip@bitcodingsolutions.com'


def find_account(outlook, email):
    for acc in outlook.Session.Accounts:
        try:
            if acc.SmtpAddress.lower() == email.lower():
                return acc
        except Exception:
            pass
    return None


def to_python_dt(com_time):
    """Outlook SentOn is a pywintypes.datetime; convert to python datetime."""
    try:
        return dt.datetime(
            com_time.year, com_time.month, com_time.day,
            com_time.hour, com_time.minute, com_time.second,
        )
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30,
                    help='Only scan Sent items from the last N days (default 30)')
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # Pull pending drafts (have outlook_entry_id but no sent_at)
    pending = con.execute("""
        SELECT e.id, e.lead_id, e.outlook_entry_id, e.subject, l.email as to_email
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        WHERE e.sent_at IS NULL AND e.outlook_entry_id IS NOT NULL
    """).fetchall()

    if not pending:
        print("No drafts awaiting sent-status check.")
        return

    print(f"{len(pending)} drafts awaiting sent-status check.")
    pending_by_entry = {r['outlook_entry_id']: r for r in pending}

    outlook = win32com.client.Dispatch('Outlook.Application')
    acc = find_account(outlook, FROM_EMAIL)
    if not acc:
        print(f"ERROR: account {FROM_EMAIL} not in Outlook.", file=sys.stderr)
        sys.exit(2)

    # 5 = olFolderSentMail
    sent_folder = acc.DeliveryStore.GetDefaultFolder(5)
    print(f"Scanning: {sent_folder.FolderPath} ({sent_folder.Items.Count} items)")

    cutoff = dt.datetime.now() - dt.timedelta(days=args.days)
    updated = 0
    matched_entry_ids = set()

    # First pass: match by EntryID (draft became sent with same EntryID in Exchange)
    for item in list(sent_folder.Items):
        sent_dt = to_python_dt(item.SentOn) if hasattr(item, 'SentOn') else None
        if sent_dt and sent_dt < cutoff:
            continue
        eid = item.EntryID
        if eid in pending_by_entry:
            row = pending_by_entry[eid]
            con.execute(
                "UPDATE emails_sent SET sent_at = ? WHERE id = ?",
                (sent_dt.isoformat(timespec='seconds') if sent_dt else dt.datetime.now().isoformat(timespec='seconds'), row['id']),
            )
            con.execute(
                "UPDATE lead_status SET status='Sent', "
                "first_sent_at = COALESCE(first_sent_at, ?), "
                "last_touch_date = ?, touch_count = touch_count + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE lead_id = ?",
                (sent_dt.isoformat() if sent_dt else dt.datetime.now().isoformat(),
                 dt.date.today().isoformat(), row['lead_id']),
            )
            matched_entry_ids.add(eid)
            updated += 1
            print(f"  SENT  {row['lead_id']}  ->  {row['to_email']}  |  {row['subject'][:55]}")

    # Second pass: for any still-pending drafts, try matching by subject + recipient
    still_pending = [p for p in pending if p['outlook_entry_id'] not in matched_entry_ids]
    if still_pending:
        sub_to_row = {(p['subject'], p['to_email'].lower()): p for p in still_pending}
        for item in list(sent_folder.Items):
            try:
                sent_dt = to_python_dt(item.SentOn) if hasattr(item, 'SentOn') else None
                if sent_dt and sent_dt < cutoff:
                    continue
                to_raw = (item.To or '').lower().strip().strip("'").strip('"')
                key = (item.Subject, to_raw)
                if key in sub_to_row:
                    row = sub_to_row[key]
                    con.execute(
                        "UPDATE emails_sent SET sent_at=?, outlook_entry_id=? WHERE id=?",
                        (sent_dt.isoformat(timespec='seconds') if sent_dt else dt.datetime.now().isoformat(timespec='seconds'),
                         item.EntryID, row['id']),
                    )
                    con.execute(
                        "UPDATE lead_status SET status='Sent', "
                        "first_sent_at = COALESCE(first_sent_at, ?), "
                        "last_touch_date=?, touch_count=touch_count+1, "
                        "updated_at=CURRENT_TIMESTAMP WHERE lead_id=?",
                        (sent_dt.isoformat() if sent_dt else dt.datetime.now().isoformat(),
                         dt.date.today().isoformat(), row['lead_id']),
                    )
                    updated += 1
                    print(f"  SENT(fallback)  {row['lead_id']}  ->  {row['to_email']}  |  {row['subject'][:55]}")
            except Exception:
                pass

    # Update daily_batches.sent_count
    today = dt.date.today().isoformat()
    sent_today = con.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", (today,)
    ).fetchone()[0]
    con.execute(
        "UPDATE daily_batches SET sent_count=? WHERE batch_date=?",
        (sent_today, today),
    )
    con.commit()
    con.close()

    print(f"\n[OK] Updated {updated} emails as Sent.")
    print(f"     Still pending (not yet sent): {len(pending) - updated}")


if __name__ == '__main__':
    main()
