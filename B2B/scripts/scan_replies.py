"""
Scan Outlook Inbox for replies to our outreach emails.
Classifies sentiment via Bridge and updates DB replies + lead_status.

Run: python scan_replies.py
Run: python scan_replies.py --days 14
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import os

import requests
import win32com.client

DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'
FROM_EMAIL = 'pradip@bitcodingsolutions.com'
# Env-overridable so a port move doesn't need a source edit. Default
# matches the dashboard backend (linkedin_claude.py).
BRIDGE_URL = os.environ.get('BRIDGE_URL', 'http://127.0.0.1:8766/generate-reply')


SENTIMENT_PROMPT = """Du klassifizierst deutsche B2B Email-Antworten aus Cold-Outreach.

Antworte mit NUR EINEM Wort aus dieser Liste:
- Positive        (Interesse, möchte Gespräch/Meeting, fragt Details)
- Objection       (Hat Bedenken, fragt kritisch, aber nicht abweisend)
- Negative        (Kein Interesse, bitte nicht mehr kontaktieren, wütend)
- OOO             (Out-of-Office / Abwesenheitsnotiz / Vertretungshinweis)
- Neutral         (Neutral, unspezifisch, weder ja noch nein)
- Bounce          (Mailer-daemon, Unzustellbar, Zustellproblem)

Kein weiterer Text. Nur ein Wort."""


def to_py_dt(com_time):
    try:
        return dt.datetime(
            com_time.year, com_time.month, com_time.day,
            com_time.hour, com_time.minute, com_time.second,
        )
    except Exception:
        return None


def classify(subject, body):
    sample = f"Subject: {subject}\n\n{body[:1200]}"
    try:
        r = requests.post(
            BRIDGE_URL,
            json={'system_prompt': SENTIMENT_PROMPT, 'user_message': sample},
            timeout=60,
        )
        r.raise_for_status()
        reply = (r.json().get('reply') or '').strip()
        # Pick first of recognised tokens found in reply
        for label in ['Positive', 'Objection', 'Negative', 'OOO', 'Bounce', 'Neutral']:
            if label.lower() in reply.lower():
                return label
        return 'Neutral'
    except Exception as e:
        print(f"  [classify error] {e}")
        return 'Neutral'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    sent = con.execute("""
        SELECT e.lead_id, e.subject, l.email as to_email
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        WHERE e.sent_at IS NOT NULL
    """).fetchall()

    if not sent:
        print("No sent emails to check replies for.")
        return

    email_map = {r['to_email'].lower(): r for r in sent}
    # Also match by normalized subject (strip "Re:", "AW:", and common bounce-NDR prefixes).
    # Without bounce-prefix stripping, an NDR like "Unzustellbar: Kurze Frage" never
    # matches its original "Kurze Frage" via subj_map, so the bounce slips through
    # and the email stays in the sending pool.
    _PREFIX_RE = re.compile(
        r'^(re|aw|fwd|wg|undeliverable|unzustellbar|nicht zugestellt|returned mail|delivery status notification|mail delivery failed|automatic reply):\s*',
        flags=re.I,
    )
    def norm_subj(s):
        s = (s or '').strip()
        # Strip repeatedly so "Unzustellbar: Re: Hello" collapses to "hello"
        for _ in range(3):
            m = _PREFIX_RE.match(s)
            if not m:
                break
            s = s[m.end():]
        return s.lower()

    subj_map = {}
    for r in sent:
        subj_map.setdefault(norm_subj(r['subject']), []).append(r)

    outlook = win32com.client.Dispatch('Outlook.Application')
    acc = None
    for a in outlook.Session.Accounts:
        if a.SmtpAddress.lower() == FROM_EMAIL.lower():
            acc = a
            break
    if not acc:
        print(f"ERROR: account {FROM_EMAIL} not in Outlook.", file=sys.stderr)
        sys.exit(2)

    # 6 = olFolderInbox
    inbox = acc.DeliveryStore.GetDefaultFolder(6)
    print(f"Scanning inbox: {inbox.FolderPath}")

    cutoff = dt.datetime.now() - dt.timedelta(days=args.days)

    # Pre-load already-logged replies to avoid duplicates
    seen = set(
        row[0] for row in con.execute(
            "SELECT lead_id || '|' || IFNULL(reply_at,'') FROM replies"
        ).fetchall()
    )

    new_replies = 0
    for item in list(inbox.Items):
        try:
            if item.Class != 43:  # olMail
                continue
            received = to_py_dt(item.ReceivedTime)
            if received and received < cutoff:
                continue
            sender = (item.SenderEmailAddress or '').lower()
            if not sender or sender == FROM_EMAIL.lower():
                continue

            # Try match by sender email first
            match = email_map.get(sender)
            if not match:
                # Fallback: match by subject
                candidates = subj_map.get(norm_subj(item.Subject), [])
                if candidates:
                    match = candidates[0]
            if not match:
                continue

            key = f"{match['lead_id']}|{received.isoformat() if received else ''}"
            if key in seen:
                continue

            body = item.Body or ''
            snippet = body[:200].replace('\n', ' ')

            # Deterministic bounce detection — cheaper + more reliable than a
            # Claude call. Catches both daemon-sourced NDRs and bounces that
            # arrive from the target MX with a normal-looking From header.
            subj_lower = (item.Subject or '').lower()
            body_lower = body.lower()
            sent_auto = (
                any(x in sender for x in ('mailer-daemon', 'postmaster', 'mail-daemon'))
                or any(x in subj_lower for x in (
                    'undeliverable', 'unzustellbar', 'nicht zugestellt',
                    'delivery status notification', 'mail delivery failed',
                    'returned mail',
                ))
                or any(x in body_lower for x in (
                    'address not found', 'user unknown',
                    'mailbox does not exist', 'no such user',
                    'recipient address rejected',
                ))
            )
            sentiment = 'Bounce' if sent_auto else classify(item.Subject, body)

            con.execute(
                "INSERT INTO replies (lead_id, reply_at, subject, body, sentiment, snippet, handled) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (match['lead_id'],
                 received.isoformat() if received else None,
                 item.Subject, body, sentiment, snippet),
            )
            new_status = {
                'Positive': 'Replied_Positive',
                'Objection': 'Replied_Objection',
                'Negative': 'Replied_Negative',
                'OOO': 'Replied_OOO',
                'Bounce': 'Bounced',
                'Neutral': 'Replied_Neutral',
            }[sentiment]
            con.execute(
                "UPDATE lead_status SET status=?, updated_at=CURRENT_TIMESTAMP WHERE lead_id=?",
                (new_status, match['lead_id']),
            )
            # Auto-DNC on bounce or explicit negative
            if sentiment in ('Bounce', 'Negative'):
                to_dnc = match['to_email'].lower().strip().strip("'").strip('"')
                con.execute(
                    "INSERT OR IGNORE INTO do_not_contact (email, reason) VALUES (?, ?)",
                    (to_dnc, f'auto:{sentiment}'),
                )
            # Mark email as invalid if bounced (so pool filter skips it)
            if sentiment == 'Bounce':
                con.execute(
                    "UPDATE leads SET email_valid=0, email_invalid_reason='bounced', "
                    "email_verified_at=CURRENT_TIMESTAMP WHERE lead_id=?",
                    (match['lead_id'],),
                )
            con.commit()
            seen.add(key)
            new_replies += 1
            print(f"  [{sentiment:9s}] {match['lead_id']}  <-  {sender}  |  {item.Subject[:55]}")
        except Exception as e:
            print(f"  [err] {e}")

    print(f"\n[OK] New replies logged: {new_replies}")
    # Update daily_batches counter
    today = dt.date.today().isoformat()
    today_count = con.execute(
        "SELECT COUNT(*) FROM replies WHERE DATE(reply_at)=?", (today,)
    ).fetchone()[0]
    con.execute(
        "UPDATE daily_batches SET replies_count=? WHERE batch_date=?",
        (today_count, today),
    )
    con.commit()
    con.close()


if __name__ == '__main__':
    main()
