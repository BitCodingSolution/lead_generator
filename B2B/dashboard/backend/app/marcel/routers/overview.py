"""Cross-source overview, funnel, daily-activity, stats, health.

These are the endpoints that drive the main dashboard cards / charts.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter

from app.config import settings
from app.marcel.db import q_all, q_one
from app.marcel.services.sources import all_sources

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "time": dt.datetime.now().isoformat(),
    }


@router.get("/stats")
def stats() -> dict:
    today = dt.date.today().isoformat()
    total_sent = q_one("SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NOT NULL")
    total_replies = q_one("SELECT COUNT(*) FROM replies")
    positive = q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Positive'")
    reply_rate = (total_replies / total_sent * 100) if total_sent else 0
    positive_rate = (positive / total_sent * 100) if total_sent else 0
    return {
        "total_leads": q_one("SELECT COUNT(*) FROM leads"),
        "tier1": q_one("SELECT COUNT(*) FROM leads WHERE tier=1"),
        "tier2": q_one("SELECT COUNT(*) FROM leads WHERE tier=2"),
        "new_leads": q_one("""
            SELECT COUNT(*) FROM lead_status ls
            JOIN leads l ON l.lead_id = ls.lead_id
            WHERE ls.status='New'
              AND (l.email_valid IS NULL OR l.email_valid=1)
              AND l.email NOT IN (SELECT email FROM do_not_contact)
        """),
        "invalid_emails": q_one("SELECT COUNT(*) FROM leads WHERE email_valid=0"),
        "dnc_count": q_one("SELECT COUNT(*) FROM do_not_contact"),
        "picked": q_one("SELECT COUNT(*) FROM lead_status WHERE status='Picked'"),
        "drafted": q_one(
            "SELECT COUNT(*) FROM lead_status WHERE status IN ('Drafted','DraftedInOutlook')"
        ),
        "total_sent": total_sent,
        "sent_today": q_one(
            "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
        ),
        "total_replies": total_replies,
        "replies_today": q_one(
            "SELECT COUNT(*) FROM replies WHERE DATE(reply_at)=?", today
        ),
        "positive": positive,
        "objection": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Objection'"),
        "neutral": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Neutral'"),
        "negative": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Negative'"),
        "ooo": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='OOO'"),
        "bounce": q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Bounce'"),
        "hot_pending": q_one(
            "SELECT COUNT(*) FROM replies WHERE handled=0 AND sentiment IN ('Positive','Objection')"
        ),
        "reply_rate_pct": round(reply_rate, 2),
        "positive_rate_pct": round(positive_rate, 2),
        "daily_quota": settings.daily_quota,
        "remaining_today": max(0, settings.daily_quota - q_one(
            "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
        )),
    }


@router.get("/overview")
def overview() -> dict:
    """Cross-source aggregate combining Marcel DB stats + grab-source
    batch-file counts."""
    today = dt.date.today().isoformat()

    marcel_total_sent = q_one("SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NOT NULL")
    marcel_sent_today = q_one(
        "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
    )
    marcel_drafted = q_one(
        "SELECT COUNT(*) FROM lead_status "
        "WHERE status IN ('Drafted','DraftedInOutlook')"
    )
    marcel_leads = q_one("SELECT COUNT(*) FROM leads")
    total_replies = q_one("SELECT COUNT(*) FROM replies")
    positive = q_one("SELECT COUNT(*) FROM replies WHERE sentiment='Positive'")
    hot_pending = q_one(
        "SELECT COUNT(*) FROM replies WHERE handled=0 "
        "AND sentiment IN ('Positive','Objection')"
    )

    import pandas as pd

    grab_leads = 0
    grab_drafted = 0
    grab_sent_today = 0
    grab_total_sent = 0
    leads_by_source: dict[str, int] = {"marcel": marcel_leads}
    from app.linkedin.db import connect as _pg_connect  # local: avoid cycle
    for sid, src in all_sources().items():
        if src.type != "grab":
            continue
        per_source = 0
        if src.leads_table:
            try:
                with _pg_connect() as con:
                    per_source = con.execute(
                        f"SELECT COUNT(*) FROM {src.leads_table}"
                    ).fetchone()[0]
            except Exception:
                pass
        leads_by_source[sid] = per_source
        grab_leads += per_source
        for f in settings.grab_batches_dir.glob(f"*_{sid}_*.xlsx"):
            try:
                df = pd.read_excel(f)
                if "draft_subject" in df.columns:
                    s = df["draft_subject"].astype(str).str.strip().str.lower()
                    grab_drafted += int(((s != "") & (s != "nan") & (s != "none")).sum())
                if "sent_at" in df.columns:
                    sent_series = pd.to_datetime(df["sent_at"], errors="coerce")
                    grab_total_sent += int(sent_series.notna().sum())
                    grab_sent_today += int(
                        (sent_series.dt.date.astype(str) == today).sum()
                    )
            except Exception:
                pass

    total_sent = marcel_total_sent + grab_total_sent
    sent_today = marcel_sent_today + grab_sent_today
    reply_rate = (total_replies / total_sent * 100) if total_sent else 0
    positive_rate = (positive / total_sent * 100) if total_sent else 0

    return {
        "total_leads": marcel_leads + grab_leads,
        "leads_by_source": leads_by_source,
        "drafted": marcel_drafted + grab_drafted,
        "total_sent": total_sent,
        "sent_today": sent_today,
        "total_replies": total_replies,
        "hot_pending": hot_pending,
        "reply_rate_pct": round(reply_rate, 2),
        "positive_rate_pct": round(positive_rate, 2),
        "daily_quota": settings.daily_quota,
        "remaining_today": max(0, settings.daily_quota - sent_today),
        "has_replies": total_replies > 0,
    }


@router.get("/funnel")
def funnel() -> list[dict]:
    rows = q_all("SELECT status, COUNT(*) as n FROM lead_status GROUP BY status")
    by = {r["status"]: r["n"] for r in rows}
    return [
        {"stage": "New", "count": by.get("New", 0)},
        {"stage": "Picked", "count": by.get("Picked", 0)},
        {"stage": "Drafted", "count": by.get("Drafted", 0) + by.get("DraftedInOutlook", 0)},
        {"stage": "Sent", "count": by.get("Sent", 0) + sum(v for k, v in by.items() if k.startswith("Replied_"))},
        {"stage": "Replied", "count": sum(v for k, v in by.items() if k.startswith("Replied_"))},
        {"stage": "Positive", "count": by.get("Replied_Positive", 0)},
    ]


@router.get("/daily-activity")
def daily_activity(days: int = 30) -> list[dict]:
    sent = q_all(f"""
        SELECT DATE(sent_at) as day, COUNT(*) as sent
        FROM emails_sent
        WHERE sent_at IS NOT NULL
          AND DATE(sent_at) >= DATE('now', '-{days} days')
        GROUP BY DATE(sent_at)
    """)
    repl = q_all(f"""
        SELECT DATE(reply_at) as day, COUNT(*) as replies
        FROM replies
        WHERE DATE(reply_at) >= DATE('now', '-{days} days')
        GROUP BY DATE(reply_at)
    """)
    by: dict[str, dict] = {}
    for r in sent:
        by.setdefault(r["day"], {"day": r["day"], "sent": 0, "replies": 0})["sent"] = r["sent"]
    for r in repl:
        by.setdefault(r["day"], {"day": r["day"], "sent": 0, "replies": 0})["replies"] = r["replies"]
    return sorted(by.values(), key=lambda x: x["day"])
