"""Marcel-side leads / industries / hot-leads / recent-sent endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from app.db import q_all, q_one

router = APIRouter(prefix="/api", tags=["leads"])


@router.get("/industries")
def industries() -> list[dict]:
    return q_all("""
        SELECT l.industry, COUNT(*) as total,
          SUM(CASE WHEN ls.status='New'
                    AND (l.email_valid IS NULL OR l.email_valid=1)
                    AND l.email NOT IN (SELECT email FROM do_not_contact)
               THEN 1 ELSE 0 END) as available,
          SUM(CASE WHEN e.sent_at IS NOT NULL THEN 1 ELSE 0 END) as sent,
          l.tier as tier
        FROM leads l
        JOIN lead_status ls ON l.lead_id = ls.lead_id
        LEFT JOIN emails_sent e ON e.lead_id = l.lead_id
        WHERE l.tier IN (1, 2)
        GROUP BY l.industry, l.tier
        ORDER BY available DESC
    """)


@router.get("/hot-leads")
def hot_leads(limit: int = 20) -> list[dict]:
    return q_all("""
        SELECT r.id, r.lead_id, l.name, l.company, l.industry, l.city,
               r.sentiment, r.reply_at, r.snippet, r.handled
        FROM replies r JOIN leads l ON r.lead_id = l.lead_id
        WHERE r.handled = 0 AND r.sentiment IN ('Positive','Objection')
        ORDER BY r.reply_at DESC
        LIMIT ?
    """, limit)


@router.get("/recent-sent")
def recent_sent(limit: int = 25) -> list[dict]:
    return q_all("""
        SELECT e.sent_at, e.lead_id, l.name, l.company, l.industry, l.city,
               e.subject, ls.status
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        JOIN lead_status ls ON ls.lead_id = l.lead_id
        WHERE e.sent_at IS NOT NULL
        ORDER BY e.sent_at DESC
        LIMIT ?
    """, limit)


@router.get("/leads")
def leads(
    status: Optional[str] = None,
    industry: Optional[str] = None,
    tier: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    where = ["1=1"]
    params: list = []
    if status:
        where.append("ls.status = ?"); params.append(status)
    if industry:
        where.append("l.industry = ?"); params.append(industry)
    if tier:
        where.append("l.tier = ?"); params.append(tier)
    if search:
        where.append("(l.name LIKE ? OR l.company LIKE ? OR l.email LIKE ?)")
        term = f"%{search}%"
        params += [term, term, term]
    sql = f"""
        SELECT l.lead_id, l.name, l.title, l.company, l.email, l.industry, l.sub_industry,
               l.city, l.tier, ls.status, ls.touch_count, ls.last_touch_date
        FROM leads l JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE {' AND '.join(where)}
        ORDER BY l.lead_id
        LIMIT ? OFFSET ?
    """
    params_with_pagination = [*params, limit, offset]
    items = q_all(sql, *params_with_pagination)
    total = q_one(f"""
        SELECT COUNT(*) FROM leads l JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE {' AND '.join(where)}
    """, *params)
    return {"items": items, "total": total}


@router.get("/lead/{lead_id}")
def lead_detail(lead_id: str) -> dict:
    lead = q_all("""
        SELECT l.*, ls.status, ls.touch_count, ls.last_touch_date, ls.assigned_to
        FROM leads l JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE l.lead_id = ?
    """, lead_id)
    if not lead:
        raise HTTPException(404)
    emails = q_all("SELECT * FROM emails_sent WHERE lead_id=? ORDER BY id DESC", lead_id)
    replies = q_all("SELECT * FROM replies WHERE lead_id=? ORDER BY id DESC", lead_id)
    return {"lead": lead[0], "emails": emails, "replies": replies}
