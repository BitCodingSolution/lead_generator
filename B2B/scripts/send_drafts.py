"""
Send N drafts from Outlook with human-ish jitter between sends.

Picks drafts in the specified batch file that are 'DraftedInOutlook'
but not yet sent, sends up to --count with random 30-90s delays to mimic
human sending pace, and records sent_at in DB.

Usage:
    python send_drafts.py --file "<batch.xlsx>" --count 15
    python send_drafts.py --file "<batch.xlsx>" --count 1 --dry-run
    python send_drafts.py --file "<batch.xlsx>" --count 15 --no-jitter
"""
import argparse
import datetime as dt
import os
import random
import sqlite3
import sys
import time
import pandas as pd
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
    """Switch active explorer off any draft so Send() is not blocked by
    'inline response mail item'."""
    try:
        exp = outlook.ActiveExplorer()
        if exp:
            inbox = account.DeliveryStore.GetDefaultFolder(6)
            exp.CurrentFolder = inbox
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--count', type=int, required=True)
    ap.add_argument('--no-jitter', action='store_true', help='Send back-to-back (risky)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_excel(args.file)

    # Filter: has outlook_entry_id but no sent_at
    has_entry = df['outlook_entry_id'].notna() & (df['outlook_entry_id'].astype(str).str.strip() != '')
    no_sent = df['sent_at'].isna() | (df['sent_at'].astype(str).str.strip().isin(['', 'nan']))
    todo = df[has_entry & no_sent].head(args.count)

    print(f"Will send {len(todo)} drafts (requested: {args.count}).")
    for _, r in todo.iterrows():
        print(f"  {r['lead_id']}  ->  {r['email']}   [{str(r['draft_subject'])[:55]}]")

    if args.dry_run:
        print("\n[DRY RUN] No emails sent.")
        return
    if not len(todo):
        return

    outlook = win32com.client.Dispatch('Outlook.Application')
    acc = find_account(outlook, FROM_EMAIL)
    if not acc:
        print(f"ERROR: account {FROM_EMAIL} not in Outlook.", file=sys.stderr)
        sys.exit(2)

    ns = outlook.GetNamespace('MAPI')
    dismiss_reading_pane(outlook, acc)
    con = sqlite3.connect(DB)

    sent_count = 0
    errors = []
    for idx, (i, row) in enumerate(todo.iterrows()):
        eid = str(row['outlook_entry_id']).strip()
        lead_id = row['lead_id']
        try:
            item = ns.GetItemFromID(eid)
            if getattr(item, 'Sent', False):
                # Already sent earlier — DB was not reconciled. Just backfill sent_at.
                print(f"  [{lead_id}] already sent in Outlook, reconciling DB")
            else:
                # Re-normalise BCC (commas -> semicolons) and resolve before Send()
                # to avoid "Outlook does not recognize one or more names".
                try:
                    if getattr(item, "BCC", None):
                        bcc = str(item.BCC)
                        if "," in bcc:
                            item.BCC = ";".join(
                                p.strip() for p in bcc.replace(",", ";").split(";") if p.strip()
                            )
                    item.Recipients.ResolveAll()
                except Exception as _e:
                    print(f"  [{lead_id}] recipient resolve warning: {_e}")
                item.Send()
            sent_ts = dt.datetime.now().isoformat(timespec='seconds')
            con.execute(
                "UPDATE emails_sent SET sent_at=? WHERE outlook_entry_id=?",
                (sent_ts, eid),
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
            df.at[i, 'sent_at'] = sent_ts
            print(f"  [{sent_count}/{len(todo)}] SENT  {lead_id}  ->  {row['email']}")

            if idx < len(todo) - 1 and not args.no_jitter:
                wait = random.randint(MIN_JITTER_SECONDS, MAX_JITTER_SECONDS)
                print(f"       waiting {wait}s before next send...")
                time.sleep(wait)
        except Exception as e:
            msg = f"[ERR] {lead_id}: {e}"
            print(msg, file=sys.stderr)
            errors.append(msg)
            df.at[i, 'notes'] = (str(row.get('notes') or '') + f'|SEND_ERR:{e}')[:500]

    # Save Excel back
    with pd.ExcelWriter(args.file, engine='xlsxwriter',
                        engine_kwargs={'options': {'strings_to_urls': False}}) as w:
        df.to_excel(w, sheet_name='Batch', index=False)

    # Update daily_batches
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

    print(f"\n[OK] Sent: {sent_count}/{len(todo)}  Errors: {len(errors)}")
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(3)


if __name__ == '__main__':
    main()
