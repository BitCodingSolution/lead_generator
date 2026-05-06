"""LinkedIn — analytics routes.

Carved from `app.linkedin.extras`. Routes are byte-identical to
the original; the wildcard import below inherits every helper
and module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.extras import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin-extras"])


@router.get("/analytics")
def linkedin_analytics(days: int = 30):
    """Day-by-day counts of: drafted, sent, replied, bounced. Returns the
    last N days (default 30), oldest-first for chart rendering."""
    if days < 1 or days > 180:
        raise HTTPException(400, "days must be 1..180")

    end = dt.date.today()
    start = end - dt.timedelta(days=days - 1)

    # Bucket by day across leads table (sent_at, replied_at, bounced_at) and
    # events table (kind='draft').
    with connect() as con:
        def per_day(column: str, where_extra: str = "") -> dict[str, int]:
            rows = con.execute(
                f"SELECT DATE({column}) AS d, COUNT(*) AS n FROM ln_leads "
                f"WHERE {column} IS NOT NULL AND DATE({column}) >= ? "
                f"      {('AND ' + where_extra) if where_extra else ''} "
                f"GROUP BY DATE({column})",
                (start.isoformat(),),
            ).fetchall()
            return {r["d"]: int(r["n"]) for r in rows}

        sent_map = per_day("sent_at")
        replied_map = per_day("replied_at")
        bounced_map = per_day("bounced_at")

        drafted_rows = con.execute(
            "SELECT DATE(at) AS d, COUNT(*) AS n FROM ln_events "
            "WHERE kind = 'draft' AND DATE(at) >= ? "
            "GROUP BY DATE(at)",
            (start.isoformat(),),
        ).fetchall()
        drafted_map = {r["d"]: int(r["n"]) for r in drafted_rows}

        totals = {
            "total_leads": con.execute("SELECT COUNT(*) FROM ln_leads").fetchone()[0],
            "sent": con.execute(
                "SELECT COUNT(*) FROM ln_leads WHERE sent_at IS NOT NULL"
            ).fetchone()[0],
            "replied": con.execute(
                "SELECT COUNT(*) FROM ln_leads WHERE replied_at IS NOT NULL"
            ).fetchone()[0],
            "bounced": con.execute(
                "SELECT COUNT(*) FROM ln_leads WHERE bounced_at IS NOT NULL"
            ).fetchone()[0],
            "recyclebin": con.execute("SELECT COUNT(*) FROM ln_recyclebin").fetchone()[0],
        }

    series: list[dict] = []
    for i in range(days):
        d = (start + dt.timedelta(days=i)).isoformat()
        series.append({
            "day": d,
            "drafted": drafted_map.get(d, 0),
            "sent":    sent_map.get(d, 0),
            "replied": replied_map.get(d, 0),
            "bounced": bounced_map.get(d, 0),
        })

    reply_rate = (
        round(totals["replied"] / totals["sent"] * 100, 1)
        if totals["sent"]
        else 0.0
    )
    bounce_rate = (
        round(totals["bounced"] / totals["sent"] * 100, 1)
        if totals["sent"]
        else 0.0
    )

    return {
        "days": days,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "series": series,
        "totals": totals,
        "reply_rate_pct": reply_rate,
        "bounce_rate_pct": bounce_rate,
    }


@router.get("/dns/check")
def dns_check(domain: str):
    """Best-effort SPF / DKIM / DMARC health check for a sending domain.
    Lightweight — no auth because the data is read-only and public.
    Returns per-record: present bool, value string (truncated), and a
    simple verdict (ok / missing / soft). DKIM lookup is a shallow probe
    of common selectors since the real selector depends on the provider
    (Microsoft uses 'selector1' / 'selector2', Google uses 'google')."""
    import re as _re
    domain = (domain or "").strip().lower().strip(".")
    if not _re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        raise HTTPException(400, "Invalid domain")
    try:
        import dns.resolver  # type: ignore
    except Exception:
        raise HTTPException(500, "dnspython not installed on server")

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4.0
    resolver.timeout = 2.0

    def _txt(name: str) -> list[str]:
        try:
            ans = resolver.resolve(name, "TXT")
            out: list[str] = []
            for rr in ans:
                # Each TXT rdata is a tuple of byte chunks. Join them.
                chunks = [
                    c.decode("utf-8", "replace") if isinstance(c, (bytes, bytearray)) else str(c)
                    for c in getattr(rr, "strings", [])
                ]
                out.append("".join(chunks) if chunks else str(rr).strip('"'))
            return out
        except Exception:
            return []

    def _first(records: list[str], prefix: str) -> str | None:
        for r in records:
            if r.lower().startswith(prefix):
                return r
        return None

    root = _txt(domain)
    spf_val = _first(root, "v=spf1")
    spf_verdict = (
        "ok" if spf_val and (" -all" in spf_val or " ~all" in spf_val) else
        "soft" if spf_val else "missing"
    )

    dmarc = _first(_txt(f"_dmarc.{domain}"), "v=dmarc1")
    dmarc_verdict = (
        "ok" if dmarc and "p=reject" in dmarc.lower() else
        "soft" if dmarc and "p=quarantine" in dmarc.lower() else
        "soft" if dmarc else "missing"
    )

    # Probe common DKIM selectors. Stop at the first hit; report it.
    dkim_selector = None
    dkim_val = None
    for sel in ("selector1", "selector2", "google", "default", "s1", "s2", "k1"):
        vals = _txt(f"{sel}._domainkey.{domain}")
        if vals:
            dkim_selector = sel
            dkim_val = vals[0]
            break
    dkim_verdict = "ok" if dkim_val else "missing"

    def _trim(v: str | None) -> str | None:
        if not v:
            return v
        return v if len(v) <= 220 else v[:217] + "..."

    return {
        "domain": domain,
        "spf":   {"verdict": spf_verdict,   "value": _trim(spf_val)},
        "dkim":  {"verdict": dkim_verdict,  "value": _trim(dkim_val),
                  "selector": dkim_selector},
        "dmarc": {"verdict": dmarc_verdict, "value": _trim(dmarc)},
    }


@router.get("/outreach-stats")
def outreach_stats():
    """Reply-rate breakdown by style signals, to answer the 'which
    approaches get replies?' question. Groups sent leads by:
      - cv_cluster (which CV / pitch specialty)
      - body_length_bucket (<60 / 60-120 / 120+ words)
      - subject_prefix (first word of gen_subject, lowercased)
      - weekday (Mon-Sun of sent_at)

    For each bucket returns sent / replied / positive counts plus
    percentages. Small table, recomputed on-demand — no caching. Use
    this to spot which buckets outperform the average."""
    def bucket_len(body: str | None) -> str:
        if not body:
            return "unknown"
        words = len(body.split())
        if words < 60:
            return "short (<60w)"
        if words < 120:
            return "medium (60-120w)"
        return "long (120+w)"

    def subject_prefix(subj: str | None) -> str:
        if not subj:
            return "(none)"
        first = subj.strip().split(" ", 1)[0].lower().strip(",.:;?!")
        return first[:20] if first else "(empty)"

    def weekday(iso: str | None) -> str:
        if not iso:
            return "unknown"
        try:
            return dt.datetime.fromisoformat(iso).strftime("%a")
        except ValueError:
            return "unknown"

    with connect() as con:
        rows = con.execute(
            "SELECT l.id, l.gen_subject, l.gen_body, l.cv_cluster, l.sent_at, "
            "       l.replied_at, "
            "       (SELECT sentiment FROM ln_replies WHERE lead_id = l.id "
            "        ORDER BY id DESC LIMIT 1) AS sentiment "
            "FROM ln_leads l WHERE l.sent_at IS NOT NULL"
        ).fetchall()

    def _bucket() -> dict:
        return {"sent": 0, "replied": 0, "positive": 0}

    groups = {
        "cv_cluster":    {},
        "body_length":   {},
        "subject_first": {},
        "weekday":       {},
    }

    for r in rows:
        replied = bool(r["replied_at"])
        positive = replied and (r["sentiment"] or "").lower() == "positive"

        keys = {
            "cv_cluster":    (r["cv_cluster"] or "(none)"),
            "body_length":   bucket_len(r["gen_body"]),
            "subject_first": subject_prefix(r["gen_subject"]),
            "weekday":       weekday(r["sent_at"]),
        }
        for group, key in keys.items():
            b = groups[group].setdefault(key, _bucket())
            b["sent"] += 1
            if replied:
                b["replied"] += 1
            if positive:
                b["positive"] += 1

    def pct(n: int, d: int) -> float:
        return round(n / d * 100, 1) if d else 0.0

    def serialise(by: dict) -> list[dict]:
        # Sort so UI can show best-performing first but small buckets
        # don't dominate. Require >=3 sent to rank by reply rate.
        items = []
        for k, v in by.items():
            items.append({
                "key": k,
                "sent": v["sent"],
                "replied": v["replied"],
                "positive": v["positive"],
                "reply_rate_pct": pct(v["replied"], v["sent"]),
                "positive_rate_pct": pct(v["positive"], v["sent"]),
            })
        items.sort(
            key=lambda x: (x["sent"] >= 3, x["reply_rate_pct"], x["sent"]),
            reverse=True,
        )
        return items

    total_sent = len(rows)
    total_replied = sum(1 for r in rows if r["replied_at"])
    total_positive = sum(
        1 for r in rows
        if r["replied_at"] and (r["sentiment"] or "").lower() == "positive"
    )

    return {
        "totals": {
            "sent": total_sent,
            "replied": total_replied,
            "positive": total_positive,
            "reply_rate_pct": pct(total_replied, total_sent),
            "positive_rate_pct": pct(total_positive, total_sent),
        },
        "by_cv_cluster":    serialise(groups["cv_cluster"]),
        "by_body_length":   serialise(groups["body_length"]),
        "by_subject_first": serialise(groups["subject_first"]),
        "by_weekday":       serialise(groups["weekday"]),
    }


@router.get("/leads/{lead_id:int}/events")
def lead_events(lead_id: int, limit: int = 100):
    with connect() as con:
        rows = con.execute(
            "SELECT id, at, kind, meta_json FROM ln_events "
            "WHERE lead_id = ? ORDER BY at DESC LIMIT ?",
            (lead_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "at": r["at"],
                "kind": r["kind"],
                "meta": json.loads(r["meta_json"]) if r["meta_json"] else None,
            })
        return {"rows": out}


@router.get("/leads/export")
def export_leads():
    cols = [
        "id", "post_url", "posted_by", "company", "role", "tech_stack",
        "location", "email", "phone", "status", "email_mode", "cv_cluster",
        "gen_subject", "jaydip_note", "first_seen_at", "sent_at",
        "replied_at", "bounced_at",
    ]
    with connect() as con:
        rows = con.execute(
            f"SELECT {', '.join(cols)} FROM ln_leads ORDER BY first_seen_at DESC"
        ).fetchall()
    return _csv_response(
        f"linkedin_leads_{dt.date.today().isoformat()}.csv",
        cols,
        [[r[c] for c in cols] for r in rows],
    )
