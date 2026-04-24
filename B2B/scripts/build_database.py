"""
Step 1b: Build SQLite database from Master Tracker.

Creates leads.db with full schema + imports 119k leads and seeds lead_status.
"""
import os
import re
import sqlite3
import pandas as pd

BASE = r'H:/Lead Generator/B2B/Database/Marcel Data'
DB_PATH = os.path.join(BASE, 'leads.db')
MASTER_XLSX = os.path.join(BASE, '00_MASTER_TRACKER.xlsx')

TIER1 = {
    'Management Consulting', 'Commerce', 'Manufacturing of Producer Goods',
    'Business Services', 'Finance', 'IT & Internet Services',
}
TIER2 = {
    'Media & Communications', 'Real Estate', 'Health Care',
    'Manufacturing of Consumer Goods', 'Transportation & Logistics',
    'Energy & Mining', 'Manufacturing of Other Goods', 'Telecommunications',
}
TIER4 = {
    'Social Organizations & Nonprofit', 'Public Administration & Safety',
    'Arts & Design', 'Agriculture', 'Shipping',
}

OWNER_RE = re.compile(
    r'(Gesch.{1,3}ftsf.{1,3}hr|Inhaber|Unternehmer|Unternehmensinhab|'
    r'Founder|Gr.{1,3}nder|^CEO|Gesellschafter|Selbst.{1,3}ndig)',
    re.IGNORECASE,
)

def tier_of(ind):
    ind = (ind or '').strip()
    if ind in TIER1: return 1
    if ind in TIER2: return 2
    if ind in TIER4: return 4
    return 3

def is_owner(title):
    return 1 if OWNER_RE.search(title or '') else 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    lead_id         TEXT PRIMARY KEY,
    name            TEXT,
    salutation      TEXT,
    title           TEXT,
    company         TEXT,
    email           TEXT UNIQUE NOT NULL,
    phone           TEXT,
    xing            TEXT,
    linkedin        TEXT,
    industry        TEXT,
    sub_industry    TEXT,
    domain          TEXT,
    website         TEXT,
    city            TEXT,
    dealfront_link  TEXT,
    source_file     TEXT,
    tier            INTEGER,
    is_owner        INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lead_status (
    lead_id          TEXT PRIMARY KEY,
    status           TEXT DEFAULT 'New',
    touch_count      INTEGER DEFAULT 0,
    last_touch_date  DATE,
    next_action      TEXT,
    next_action_date DATE,
    first_sent_at    TIMESTAMP,
    assigned_to      TEXT DEFAULT 'Pradip',
    tags             TEXT,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(lead_id)
);

CREATE TABLE IF NOT EXISTS emails_sent (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id          TEXT NOT NULL,
    batch_date       DATE,
    touch_number     INTEGER,
    subject          TEXT,
    body             TEXT,
    from_email       TEXT,
    sent_at          TIMESTAMP,
    outlook_entry_id TEXT,
    opened           INTEGER DEFAULT 0,
    bounced          INTEGER DEFAULT 0,
    bounce_reason    TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(lead_id)
);

CREATE TABLE IF NOT EXISTS replies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      TEXT NOT NULL,
    reply_at     TIMESTAMP,
    subject      TEXT,
    body         TEXT,
    sentiment    TEXT,
    snippet      TEXT,
    handled      INTEGER DEFAULT 0,
    handled_at   TIMESTAMP,
    my_response  TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(lead_id)
);

CREATE TABLE IF NOT EXISTS meetings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      TEXT NOT NULL,
    scheduled_at TIMESTAMP,
    duration_min INTEGER,
    outcome      TEXT,
    notes        TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(lead_id)
);

CREATE TABLE IF NOT EXISTS deals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     TEXT NOT NULL,
    stage       TEXT,
    value_eur   REAL,
    signed_at   DATE,
    lost_reason TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(lead_id)
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    TEXT NOT NULL,
    note       TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(lead_id)
);

CREATE TABLE IF NOT EXISTS daily_batches (
    batch_date       DATE PRIMARY KEY,
    leads_picked     INTEGER,
    drafts_generated INTEGER,
    sent_count       INTEGER,
    replies_count    INTEGER,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS do_not_contact (
    email    TEXT PRIMARY KEY,
    reason   TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_leads_industry ON leads(industry);
CREATE INDEX IF NOT EXISTS idx_leads_city ON leads(city);
CREATE INDEX IF NOT EXISTS idx_leads_tier ON leads(tier);
CREATE INDEX IF NOT EXISTS idx_leads_isowner ON leads(is_owner);
CREATE INDEX IF NOT EXISTS idx_status_current ON lead_status(status);
CREATE INDEX IF NOT EXISTS idx_status_next_date ON lead_status(next_action_date);
CREATE INDEX IF NOT EXISTS idx_emails_lead ON emails_sent(lead_id);
CREATE INDEX IF NOT EXISTS idx_replies_lead ON replies(lead_id);
CREATE INDEX IF NOT EXISTS idx_replies_pending ON replies(handled);
"""

def main():
    if os.path.exists(DB_PATH):
        bak = DB_PATH + '.bak'
        if os.path.exists(bak):
            os.remove(bak)
        os.rename(DB_PATH, bak)
        print(f"Existing DB backed up to {bak}")

    print("Creating database schema...")
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.commit()

    print(f"Reading {MASTER_XLSX}...")
    df = pd.read_excel(MASTER_XLSX, sheet_name='All_Leads')
    print(f"Loaded {len(df):,} rows")

    # Derive tier + is_owner
    df['tier'] = df['Industry'].apply(tier_of)
    df['is_owner'] = df['Title'].apply(is_owner)

    # Prepare leads df
    leads_df = df.rename(columns={
        'Lead_ID': 'lead_id', 'Name': 'name', 'Salutation': 'salutation',
        'Title': 'title', 'Company': 'company', 'Email': 'email',
        'Phone': 'phone', 'Xing': 'xing', 'LinkedIn': 'linkedin',
        'Industry': 'industry', 'Sub_Industry': 'sub_industry',
        'Domain': 'domain', 'Website': 'website', 'City': 'city',
        'Dealfront_Link': 'dealfront_link', 'Source_File': 'source_file',
    })[['lead_id','name','salutation','title','company','email','phone',
        'xing','linkedin','industry','sub_industry','domain','website',
        'city','dealfront_link','source_file','tier','is_owner']]

    # Deduplicate emails (SQLite UNIQUE constraint)
    before = len(leads_df)
    leads_df = leads_df.drop_duplicates(subset=['email'], keep='first')
    after = len(leads_df)
    if before != after:
        print(f"  Dropped {before - after} duplicate emails")

    print("Inserting leads...")
    leads_df.to_sql('leads', con, if_exists='append', index=False, chunksize=5000)

    print("Seeding lead_status (all = 'New')...")
    status_df = pd.DataFrame({
        'lead_id': leads_df['lead_id'],
        'status': 'New',
        'touch_count': 0,
        'assigned_to': 'Pradip',
    })
    status_df.to_sql('lead_status', con, if_exists='append', index=False, chunksize=5000)

    con.commit()

    # Verify
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM leads")
    print(f"\nleads:        {cur.fetchone()[0]:,}")
    cur.execute("SELECT COUNT(*) FROM lead_status")
    print(f"lead_status:  {cur.fetchone()[0]:,}")

    print("\nTier distribution:")
    for row in cur.execute("SELECT tier, COUNT(*) FROM leads GROUP BY tier ORDER BY tier"):
        print(f"  Tier {row[0]}: {row[1]:,}")

    print("\nOwner distribution:")
    for row in cur.execute("SELECT is_owner, COUNT(*) FROM leads GROUP BY is_owner"):
        label = 'Owner' if row[0] else 'Non-owner'
        print(f"  {label}: {row[1]:,}")

    print("\nTop 8 industries:")
    for row in cur.execute(
        "SELECT industry, COUNT(*) FROM leads GROUP BY industry "
        "ORDER BY COUNT(*) DESC LIMIT 8"
    ):
        print(f"  {row[1]:>6,}  {row[0]}")

    print("\nStatus counts:")
    for row in cur.execute("SELECT status, COUNT(*) FROM lead_status GROUP BY status"):
        print(f"  {row[0]}: {row[1]:,}")

    con.close()
    print(f"\n[OK] Database ready: {DB_PATH}")
    size_mb = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"     Size: {size_mb:.1f} MB")

if __name__ == '__main__':
    main()
