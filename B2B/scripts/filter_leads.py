"""
Filter master_cleaned.csv into 4 tiers + Qualified set + Garbage.
Output: H:/Lead Generator/B2B/Database/Marcel Data/
"""
import csv, re, os, sys
from collections import defaultdict, Counter
import pandas as pd

SRC = r'F:/Marcel (Germany)/Scripting/Drive Downloads/Cleaned/master_cleaned.csv'
OUT = r'H:/Lead Generator/B2B/Database/Marcel Data'
os.makedirs(OUT, exist_ok=True)

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
# Everything else (Construction, Services, Travel, Education, Entertainment,
# Research, Legal, blank, corrupted) -> Tier 3

OWNER_RE = re.compile(
    r'(Gesch.{1,3}ftsf.{1,3}hr|Inhaber|Unternehmer|Unternehmensinhab|'
    r'Founder|Gr.{1,3}nder|^CEO|Gesellschafter|Selbst.{1,3}ndig)',
    re.IGNORECASE,
)

def normalize_title(t):
    if not t:
        return t
    # Fix common mojibake for German umlauts (ä ö ü ß)
    t = re.sub(r'Gesch.ftsf.hrerin', 'Geschäftsführerin', t)
    t = re.sub(r'Gesch.ftsf.hrender', 'Geschäftsführender', t)
    t = re.sub(r'Gesch.ftsf.hrende', 'Geschäftsführende', t)
    t = re.sub(r'Gesch.ftsf.hrer', 'Geschäftsführer', t)
    t = re.sub(r'Gesch.ftsf.hrung', 'Geschäftsführung', t)
    t = re.sub(r'Gr.nder', 'Gründer', t)
    t = re.sub(r'Selbstst.ndig', 'Selbstständig', t)
    t = t.replace('Inhaberin', 'Inhaberin')
    return t

def is_owner(title):
    return bool(OWNER_RE.search(title or ''))

def is_garbage(row):
    title = (row.get('Title') or '').strip()
    ind = (row.get('Industry') or '').strip()
    email = (row.get('Email') or '').strip()
    # numeric-only title, missing email, industry is clearly broken
    if not email or '@' not in email:
        return True
    if title.isdigit():
        return True
    if ind in {'0'} or '.' in ind and ' ' not in ind and len(ind) < 40:
        # industry looks like a domain name (corrupted row)
        return True
    return False

tiers = defaultdict(list)
garbage = []
qualified = []
ind_counts = Counter()

with open(SRC, encoding='utf-8', errors='replace') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if is_garbage(row):
            garbage.append(row)
            continue
        row['Title'] = normalize_title(row.get('Title', ''))
        ind = (row.get('Industry') or '').strip()

        if ind in TIER1:
            tier = 1
        elif ind in TIER2:
            tier = 2
        elif ind in TIER4:
            tier = 4
        else:
            tier = 3

        row['_Tier'] = tier
        row['_IsOwner'] = 'Yes' if is_owner(row['Title']) else 'No'
        tiers[tier].append(row)
        ind_counts[(tier, ind)] += 1

        if tier in (1, 2) and row['_IsOwner'] == 'Yes':
            qualified.append(row)

print(f"Tier 1: {len(tiers[1]):>7}")
print(f"Tier 2: {len(tiers[2]):>7}")
print(f"Tier 3: {len(tiers[3]):>7}")
print(f"Tier 4: {len(tiers[4]):>7}")
print(f"Garbage:{len(garbage):>7}")
print(f"QUALIFIED (T1+T2 + owners): {len(qualified)}")

COLUMNS = ['Name','Salutation','Title','Company','Email','Phone','Xing','LinkedIn',
           'Industry','Sub_Industry','Domain','Website','City','Dealfront_Link',
           'Source_File','_Tier','_IsOwner']

def write_tier(tier_num, rows, label):
    if not rows:
        return
    path = os.path.join(OUT, f'Tier{tier_num}_{label}.xlsx')
    df = pd.DataFrame(rows)[COLUMNS[:-2] + (['_Tier','_IsOwner'] if '_Tier' in rows[0] else [])]
    # Split by Industry into sheets
    with pd.ExcelWriter(path, engine='xlsxwriter') as w:
        df.to_excel(w, sheet_name='All', index=False)
        for ind, grp in df.groupby('Industry'):
            sheet = (ind[:28] or 'Unknown').replace('/', '_').replace('\\','_').replace('*','').replace('?','').replace('[','').replace(']','')
            grp.to_excel(w, sheet_name=sheet or 'Unknown', index=False)
    print(f"  wrote {path}  ({len(rows)} rows)")

print("\nWriting Excel files...")
write_tier(1, tiers[1], 'Perfect')
write_tier(2, tiers[2], 'Medium')
write_tier(3, tiers[3], 'Skippable')
write_tier(4, tiers[4], 'Useless')

# Qualified set (most important — start outreach from here)
qpath = os.path.join(OUT, 'QUALIFIED_Start_Here.xlsx')
qdf = pd.DataFrame(qualified)[COLUMNS]
with pd.ExcelWriter(qpath, engine='xlsxwriter') as w:
    qdf.to_excel(w, sheet_name='All_Qualified', index=False)
    for ind, grp in qdf.groupby('Industry'):
        sheet = (ind[:28] or 'Unknown').replace('/', '_')
        grp.to_excel(w, sheet_name=sheet, index=False)
print(f"  wrote {qpath}  ({len(qualified)} rows)")

# Garbage
if garbage:
    gpath = os.path.join(OUT, 'Garbage_ManualCheck.xlsx')
    gdf = pd.DataFrame(garbage)
    with pd.ExcelWriter(gpath, engine='xlsxwriter') as w:
        gdf.to_excel(w, sheet_name='Garbage', index=False)
    print(f"  wrote {gpath}  ({len(garbage)} rows)")

# Summary report
report_path = os.path.join(OUT, 'SUMMARY.txt')
with open(report_path, 'w', encoding='utf-8') as f:
    f.write(f"Source: {SRC}\n\n")
    f.write(f"TIER COUNTS\n")
    f.write(f"  Tier 1 (Perfect):   {len(tiers[1]):>7}\n")
    f.write(f"  Tier 2 (Medium):    {len(tiers[2]):>7}\n")
    f.write(f"  Tier 3 (Skippable): {len(tiers[3]):>7}\n")
    f.write(f"  Tier 4 (Useless):   {len(tiers[4]):>7}\n")
    f.write(f"  Garbage:            {len(garbage):>7}\n")
    f.write(f"  QUALIFIED (T1+T2 owners): {len(qualified):>7}\n\n")
    f.write("INDUSTRY x TIER BREAKDOWN\n")
    for (t, ind), c in sorted(ind_counts.items()):
        f.write(f"  T{t}  {c:>7}  {ind}\n")
print(f"  wrote {report_path}")
print("\nDONE.")
