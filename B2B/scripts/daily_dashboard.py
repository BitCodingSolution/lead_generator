"""
Daily dashboard — print status summary from leads.db.

Run: python daily_dashboard.py
"""
import datetime as dt
import sqlite3
import sys

DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'


def main():
    # Force stdout UTF-8 (Windows cp1252 breaks on German)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    today = dt.date.today().isoformat()

    def one(sql, *p):
        r = con.execute(sql, p).fetchone()
        return r[0] if r else 0

    total_leads = one("SELECT COUNT(*) FROM leads")
    total_new = one("SELECT COUNT(*) FROM lead_status WHERE status='New'")
    total_picked = one("SELECT COUNT(*) FROM lead_status WHERE status='Picked'")
    total_drafted = one("SELECT COUNT(*) FROM lead_status WHERE status IN ('Drafted','DraftedInOutlook')")
    total_sent = one("SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NOT NULL")
    total_replies = one("SELECT COUNT(*) FROM replies")
    positive = one("SELECT COUNT(*) FROM replies WHERE sentiment='Positive'")
    negative = one("SELECT COUNT(*) FROM replies WHERE sentiment='Negative'")
    objection = one("SELECT COUNT(*) FROM replies WHERE sentiment='Objection'")
    neutral = one("SELECT COUNT(*) FROM replies WHERE sentiment='Neutral'")
    ooo = one("SELECT COUNT(*) FROM replies WHERE sentiment='OOO'")
    bounced = one("SELECT COUNT(*) FROM replies WHERE sentiment='Bounce'")
    pending_reply = one(
        "SELECT COUNT(*) FROM replies WHERE handled=0 AND sentiment IN ('Positive','Objection')"
    )

    sent_today = one("SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today)
    replies_today = one("SELECT COUNT(*) FROM replies WHERE DATE(reply_at)=?", today)

    reply_rate = (total_replies / total_sent * 100) if total_sent else 0
    positive_rate = (positive / total_sent * 100) if total_sent else 0

    print("=" * 60)
    print(f"  BitCoding B2B Outreach Dashboard      {today}")
    print("=" * 60)
    print()
    print("PIPELINE")
    print(f"  Total qualified leads:    {total_leads:>7,}")
    print(f"  Status 'New' (not picked):{total_new:>7,}")
    print(f"  Picked (awaiting draft):  {total_picked:>7,}")
    print(f"  Drafted (not yet sent):   {total_drafted:>7,}")
    print(f"  Emails sent (cumulative): {total_sent:>7,}")
    print()
    print("REPLIES")
    print(f"  Total replies:            {total_replies:>7,}"
          f"  ({reply_rate:.1f}% of sent)")
    print(f"    Positive:               {positive:>7,}"
          f"  ({positive_rate:.1f}% of sent)")
    print(f"    Objection:              {objection:>7,}")
    print(f"    Neutral:                {neutral:>7,}")
    print(f"    Negative:               {negative:>7,}")
    print(f"    Out-of-office:          {ooo:>7,}")
    print(f"    Bounced:                {bounced:>7,}")
    print(f"  Pending manual response:  {pending_reply:>7,}  <- ACTION NEEDED")
    print()
    print("TODAY")
    print(f"  Sent today:               {sent_today:>7,}")
    print(f"  Replies today:            {replies_today:>7,}")
    print()

    # Hot leads list
    print("HOT LEADS (Positive or Objection, unhandled):")
    rows = con.execute("""
        SELECT r.lead_id, l.name, l.company, r.sentiment, r.reply_at, r.snippet
        FROM replies r JOIN leads l ON r.lead_id = l.lead_id
        WHERE r.handled = 0 AND r.sentiment IN ('Positive','Objection')
        ORDER BY r.reply_at DESC LIMIT 10
    """).fetchall()
    if rows:
        for r in rows:
            print(f"  [{r['sentiment']:10s}] {r['lead_id']}  {r['name']}  ({r['company'][:30]})")
            print(f"       {r['snippet'][:120]}...")
    else:
        print("  (none)")
    print()

    # Industry breakdown for sent
    print("SENT BY INDUSTRY:")
    rows = con.execute("""
        SELECT l.industry, COUNT(*) as n,
               SUM(CASE WHEN r.sentiment='Positive' THEN 1 ELSE 0 END) as pos
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        LEFT JOIN replies r ON r.lead_id = e.lead_id
        WHERE e.sent_at IS NOT NULL
        GROUP BY l.industry
        ORDER BY n DESC
    """).fetchall()
    if rows:
        for r in rows:
            print(f"  {r['n']:>4} sent  |  {r['pos']:>2} positive  |  {r['industry']}")
    else:
        print("  (no sent emails yet)")
    print()

    # Remaining pool
    remaining = total_leads - (total_sent + total_drafted + total_picked)
    print(f"REMAINING POOL: {remaining:,} leads untouched (Status = 'New')")
    print("=" * 60)

    con.close()


if __name__ == '__main__':
    main()
