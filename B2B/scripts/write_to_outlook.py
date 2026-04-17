"""
Write generated drafts into Outlook Desktop Drafts folder (NOT sent automatically).

For each lead in the batch file with a draft_subject/draft_body:
  - Create an Outlook MailItem
  - Set From account to pradip@bitcodingsolutions.com (must exist in Outlook)
  - Save to Drafts folder
  - Record outlook_entry_id in DB emails_sent row
  - Update lead_status to 'DraftedInOutlook'

Usage:
    python write_to_outlook.py --file "<batch.xlsx>"
    python write_to_outlook.py --file "<batch.xlsx>" --limit 1
    python write_to_outlook.py --file "<batch.xlsx>" --dry-run  (no Outlook)

Prereqs:
    - Outlook Desktop installed + pradip@bitcodingsolutions.com added
    - pywin32 installed (already verified)
"""
import argparse
import os
import sqlite3
import sys
import pandas as pd

FROM_EMAIL = "pradip@bitcodingsolutions.com"
SIGNATURE_HTML = """
<br><br>
<p style="font-family:Arial,sans-serif; font-size:10pt; color:#333;">
Mit freundlichen Grüßen<br>
<strong>Pradip Kachhadiya</strong><br>
Business Development — BitCoding Solutions<br>
30+ Entwickler · 150+ Projekte seit 2018<br>
AI · Web · Mobile für KMUs<br>
<a href="https://bitcodingsolutions.com">bitcodingsolutions.com</a> ·
<a href="https://www.linkedin.com/company/bitcodingsolutions/">LinkedIn</a>
</p>
""".strip()

DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'


def find_account(outlook_app, email):
    """Find Outlook account whose SMTP address matches `email`."""
    for acc in outlook_app.Session.Accounts:
        try:
            if acc.SmtpAddress.lower() == email.lower():
                return acc
        except Exception:
            pass
    return None


def body_to_html(body):
    """Convert plain-text body with \\n\\n to simple HTML."""
    paragraphs = [p.strip() for p in body.split('\n\n') if p.strip()]
    html = ''.join(
        f'<p style="font-family:Arial,sans-serif; font-size:11pt;">{p.replace(chr(10), "<br>")}</p>'
        for p in paragraphs
    )
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_excel(args.file)
    if args.limit:
        df = df.head(args.limit)

    # Filter to rows with a draft and no Outlook entry yet
    has_draft = df['draft_subject'].notna() & (df['draft_subject'].astype(str).str.strip() != '')
    if 'outlook_entry_id' in df.columns:
        no_entry = df['outlook_entry_id'].isna() | \
                   (df['outlook_entry_id'].astype(str).str.strip().isin(['', 'nan']))
    else:
        no_entry = pd.Series([True] * len(df))
    todo = df[has_draft & no_entry]
    print(f"Leads with drafts and no Outlook entry: {len(todo)}")

    if len(todo) == 0:
        print("Nothing to do.")
        return

    if args.dry_run:
        print("\n[DRY RUN] Would write these to Outlook Drafts folder:")
        for _, r in todo.iterrows():
            print(f"  {r['lead_id']}  ->  {r['email']}   [{r['draft_subject']}]")
        return

    import win32com.client
    outlook = win32com.client.Dispatch('Outlook.Application')
    account = find_account(outlook, FROM_EMAIL)
    if account is None:
        avail = [a.SmtpAddress for a in outlook.Session.Accounts]
        print(f"ERROR: account '{FROM_EMAIL}' not found in Outlook Desktop.")
        print(f"Available accounts: {avail}")
        sys.exit(2)

    print(f"Using From: {account.SmtpAddress}  (DisplayName: {account.DisplayName})")

    # 16 = olFolderDrafts of the account
    drafts_folder = account.DeliveryStore.GetDefaultFolder(16)
    print(f"Drafts folder: {drafts_folder.FolderPath}")

    con = sqlite3.connect(DB)

    def _s(v):
        return '' if pd.isna(v) else str(v)

    for i, row in todo.iterrows():
        lead_id = _s(row['lead_id'])
        email = _s(row['email'])
        subject = _s(row['draft_subject'])
        body = _s(row['draft_body'])
        name = _s(row.get('name', ''))

        try:
            mail = outlook.CreateItem(0)  # 0 = olMailItem
            mail.To = email
            mail.Subject = subject
            mail.BodyFormat = 2  # olFormatHTML
            mail.HTMLBody = body_to_html(body) + SIGNATURE_HTML
            mail.Save()  # saves to default Drafts (single pradip@ account)
            entry_id = mail.EntryID

            df.at[i, 'outlook_entry_id'] = entry_id
            df.at[i, 'notes'] = _s(row.get('notes')) + '| Outlook draft created'

            # Update DB: set outlook_entry_id on most recent emails_sent row
            con.execute(
                "UPDATE emails_sent SET outlook_entry_id = ? "
                "WHERE id = (SELECT MAX(id) FROM emails_sent WHERE lead_id = ?)",
                (entry_id, lead_id),
            )
            con.execute(
                "UPDATE lead_status SET status='DraftedInOutlook', "
                "updated_at=CURRENT_TIMESTAMP WHERE lead_id = ?",
                (lead_id,),
            )
            con.commit()
            print(f"  [{lead_id}] OK  ->  {email}  |  {subject[:50]}")
        except Exception as e:
            print(f"  [{lead_id}] FAILED: {e}")
            df.at[i, 'notes'] = f"OUTLOOK ERROR: {e}"

    con.close()

    # Write Excel back (preserve full df)
    full_df = pd.read_excel(args.file)
    for col in ['outlook_entry_id', 'notes']:
        if col in df.columns:
            full_df.loc[df.index, col] = df[col]
    with pd.ExcelWriter(args.file, engine='xlsxwriter',
                        engine_kwargs={'options': {'strings_to_urls': False}}) as w:
        full_df.to_excel(w, sheet_name='Batch', index=False)

    print(f"\n[OK] Drafts created in Outlook under {drafts_folder.FolderPath}")
    print("     Open Outlook Desktop, review each draft, click Send when ready.")


if __name__ == '__main__':
    main()
