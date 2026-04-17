"""
Step 1: Build Master Tracker + folder structure.

Imports 119k qualified leads, assigns Lead_ID, initializes all tracking columns
with Status='New'. This becomes the single source of truth.
"""
import os
import pandas as pd

BASE = r'H:/Lead Generator/B2B/Database/Marcel Data'
QUALIFIED = os.path.join(BASE, 'QUALIFIED_Start_Here.xlsx')
MASTER = os.path.join(BASE, '00_MASTER_TRACKER.xlsx')

# Folder structure
folders = [
    '01_Daily_Batches',
    '02_Responses',
    '03_Original',
    '04_Reports',
]
for f in folders:
    os.makedirs(os.path.join(BASE, f), exist_ok=True)
print("Folders created.")

# Move original files into 03_Original (if not already moved)
originals = [
    'QUALIFIED_Start_Here.xlsx', 'Tier1_Perfect.xlsx', 'Tier2_Medium.xlsx',
    'Tier3_Skippable.xlsx', 'Tier4_Useless.xlsx', 'Garbage_ManualCheck.xlsx',
    'SUMMARY.txt',
]
import shutil
for fn in originals:
    src = os.path.join(BASE, fn)
    dst = os.path.join(BASE, '03_Original', fn)
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copy2(src, dst)  # copy (don't delete source yet — safer)
print("Originals copied to 03_Original/")

# Load qualified leads — use 03_Original copy as source going forward
src_path = os.path.join(BASE, '03_Original', 'QUALIFIED_Start_Here.xlsx')
if not os.path.exists(src_path):
    src_path = QUALIFIED
print(f"Reading {src_path}...")
df = pd.read_excel(src_path, sheet_name='All_Qualified')
print(f"Loaded {len(df):,} rows")

# Assign Lead_ID
df = df.reset_index(drop=True)
df.insert(0, 'Lead_ID', ['L' + str(i+1).zfill(6) for i in range(len(df))])

# Add tracking columns (all empty / New initially)
tracking_cols = {
    'Status': 'New',
    'Touch_Count': 0,
    'Last_Touch_Date': '',
    'Next_Action_Date': '',
    'Next_Action': '',
    'First_Sent_At': '',
    'Reply_At': '',
    'Reply_Sentiment': '',
    'Reply_Snippet': '',
    'Decision_Notes': '',
    'Meeting_Date': '',
    'Deal_Value_EUR': '',
    'Outcome': '',
    'Tags': '',
    'Source_Batch': '',
    'Assigned_To': 'Pradip',
    'Last_Updated': '',
}
for col, default in tracking_cols.items():
    df[col] = default

# Drop the _Tier and _IsOwner helper cols if present (they were filter helpers)
for c in ['_Tier', '_IsOwner']:
    if c in df.columns:
        df = df.drop(columns=[c])

# Reorder: Lead_ID first, tracking cols at end
lead_cols = ['Lead_ID', 'Name', 'Salutation', 'Title', 'Company', 'Email', 'Phone',
             'Xing', 'LinkedIn', 'Industry', 'Sub_Industry', 'Domain', 'Website',
             'City', 'Dealfront_Link', 'Source_File']
tracking = list(tracking_cols.keys())
df = df[[c for c in lead_cols if c in df.columns] + tracking]

print(f"Writing master tracker to {MASTER}...")
# xlsxwriter with strings_to_urls disabled to avoid 65k URL limit warnings
with pd.ExcelWriter(MASTER, engine='xlsxwriter',
                    engine_kwargs={'options': {'strings_to_urls': False}}) as w:
    df.to_excel(w, sheet_name='All_Leads', index=False)
    # Freeze header row
    wb = w.book
    ws = w.sheets['All_Leads']
    ws.freeze_panes(1, 1)
    # Column widths
    widths = {'A': 10, 'B': 22, 'D': 24, 'E': 30, 'F': 32, 'J': 22}
    for col, width in widths.items():
        ws.set_column(f'{col}:{col}', width)
print(f"  [OK] {len(df):,} rows written")

# Summary
print("\n=== MASTER TRACKER READY ===")
print(f"File:           {MASTER}")
print(f"Total leads:    {len(df):,}")
print(f"Lead IDs:       L000001 — L{str(len(df)).zfill(6)}")
print(f"Status (all):   New")
print(f"\nStatus distribution:")
print(df['Status'].value_counts().to_string())
print(f"\nTop 10 Industries:")
print(df['Industry'].value_counts().head(10).to_string())
