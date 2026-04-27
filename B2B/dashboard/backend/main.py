"""
FastAPI backend for the B2B Outreach dashboard.

Run:
    python -m uvicorn dashboard.backend.main:app --reload --port 8900
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

BASE = Path(r"H:/Lead Generator/B2B")
DB = str(BASE / "Database" / "Marcel Data" / "leads.db")
SCRIPTS = BASE / "scripts"
BATCHES_DIR = BASE / "Database" / "Marcel Data" / "01_Daily_Batches"
PY = sys.executable

DAILY_QUOTA = 25
JOB_RETENTION_SECONDS = 3600  # evict finished jobs older than 1h

# ---- Security config ----
# API_KEY is loaded from env; if unset we auto-generate one at startup and
# persist it to BASE/.api_key so the same key survives restarts. Anyone with
# filesystem access already owns the app, so this is a convenience, not a
# secret-handling crypto boundary. Disable auth entirely by setting
# DASHBOARD_REQUIRE_AUTH=0 (not recommended).
_API_KEY_FILE = BASE / ".api_key"
API_KEY = os.environ.get("DASHBOARD_API_KEY", "").strip()
if not API_KEY:
    if _API_KEY_FILE.exists():
        API_KEY = _API_KEY_FILE.read_text(encoding="utf-8").strip()
    if not API_KEY:
        API_KEY = secrets.token_urlsafe(32)
        try:
            _API_KEY_FILE.write_text(API_KEY, encoding="utf-8")
        except Exception:
            pass
REQUIRE_AUTH = os.environ.get("DASHBOARD_REQUIRE_AUTH", "1") not in ("0", "false", "False")

# Only same-origin dev + prod hosts. Anything else (arbitrary websites a
# user might visit) can't cross-origin-POST into our API.
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_extra_origins = os.environ.get("DASHBOARD_EXTRA_ORIGINS", "").strip()
if _extra_origins:
    ALLOWED_ORIGINS.extend(
        o.strip().rstrip("/") for o in _extra_origins.split(",") if o.strip()
    )

RATE_LIMIT_PER_MIN = int(os.environ.get("DASHBOARD_RATE_LIMIT", "120"))

# Expose docs/openapi only in dev. Flip DASHBOARD_DOCS=0 for "prod".
_DOCS_ENABLED = os.environ.get("DASHBOARD_DOCS", "1") not in ("0", "false", "False")

app = FastAPI(
    title="BitCoding B2B Outreach API",
    version="1.0",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)


# ---- Per-IP token-bucket rate limiter ----
# Deque of timestamps per client IP; trim entries older than 60s, reject
# when the window exceeds RATE_LIMIT_PER_MIN. Minimal, in-memory, no deps.
_RATE_BUCKETS: dict[str, collections.deque] = {}
_RATE_LOCK = threading.Lock()


def _rate_limit_check(ip: str) -> tuple[bool, int]:
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.setdefault(ip, collections.deque())
        while bucket and now - bucket[0] > 60.0:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            retry_after = max(1, int(60 - (now - bucket[0])))
            return False, retry_after
        bucket.append(now)
        return True, 0


# ---- Auth scope: write endpoints require X-API-Key ----
# Reads (stats, overview, leads, replies, etc.) are safe and don't need
# auth so the dashboard can render something even if the key got out of
# sync. Actions (pipeline, send, clear, backup, DB writes) require it.
PUBLIC_PATHS = {
    "/",
    "/docs", "/redoc", "/openapi.json",
    "/api/health",
    "/api/bridge-health",
    "/api/_bootstrap",  # loopback-gated inside the handler
}

# Endpoints that enforce their own X-Ext-Key auth (Chrome extension ingest
# path). The extension can't hold the dashboard API key because it runs on
# the user's browser with no filesystem access — it uses a per-key token
# issued via /linkedin/settings. Bypass the X-API-Key check here; the
# endpoint handlers still call _require_ext_key() internally.
EXT_KEY_PATHS = {
    "/api/linkedin/ingest",
    "/api/linkedin/account-warning",
}


def _path_requires_auth(path: str, method: str) -> bool:
    if not REQUIRE_AUTH:
        return False
    if path in PUBLIC_PATHS:
        return False
    if path in EXT_KEY_PATHS:
        return False  # protected by its own X-Ext-Key scheme
    # Writes always need auth
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return True
    # Specific GETs that mutate or run jobs
    if path.startswith("/api/actions/"):
        return True
    return False


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CORS preflight — let the CORSMiddleware handle it without auth.
    if request.method == "OPTIONS":
        return await call_next(request)

    # Rate limit by source IP
    client_ip = request.client.host if request.client else "unknown"
    ok, retry_after = _rate_limit_check(client_ip)
    if not ok:
        return JSONResponse(
            {"detail": f"Rate limit exceeded ({RATE_LIMIT_PER_MIN}/min). Retry in {retry_after}s."},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    # Auth: constant-time compare on X-API-Key for protected paths
    if _path_requires_auth(request.url.path, request.method):
        supplied = request.headers.get("x-api-key", "")
        if not supplied or not secrets.compare_digest(supplied, API_KEY):
            return JSONResponse(
                {"detail": "Missing or invalid X-API-Key header."},
                status_code=401,
            )

    return await call_next(request)


# ---- Multi-source registry (Marcel + Grab Leads sources) ----
from sources_api import router as sources_router, register_source, Source  # noqa: E402

_GRAB_ROOT = BASE / "grab_leads"

register_source(Source(
    id="marcel",
    label="Marcel Data",
    db_path=Path(DB),
    type="outreach",
    icon="Mail",
    description="Primary outreach dataset — already in the B2B pipeline.",
))

register_source(Source(
    id="ycombinator",
    label="Y Combinator",
    db_path=_GRAB_ROOT / "sources" / "ycombinator" / "data.db",
    type="grab",
    schema_path=_GRAB_ROOT / "sources" / "ycombinator" / "schema.json",
    icon="Rocket",
    description="YC portfolio companies — funded, US-heavy, actively hiring.",
))

app.include_router(sources_router)

# ---- Verbose validation-error logging so we can debug extension payloads.
from fastapi import Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402


@app.exception_handler(RequestValidationError)
async def _validation_logger(request: Request, exc: RequestValidationError):
    try:
        body_preview = (await request.body())[:800]
    except Exception:
        body_preview = b""
    print(
        f"[422] {request.method} {request.url.path} errors={exc.errors()}\n"
        f"      body={body_preview!r}"
    )
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


# ---- LinkedIn source (separate section — not part of /sources registry) ----
from linkedin_api import router as linkedin_router  # noqa: E402
from linkedin_extras import router as linkedin_extras_router, reset_orphans as _reset_orphans  # noqa: E402

app.include_router(linkedin_router)
app.include_router(linkedin_extras_router)


@app.on_event("startup")
def _linkedin_startup_cleanup():
    """Any leads stuck mid-send before last shutdown — revert to Drafted.
    Also reconcile per-account Gmail counters from today's lead rows so the
    UI reflects actual usage after migrations or mid-day restarts."""
    try:
        _reset_orphans()
    except Exception as e:
        print(f"[linkedin] startup orphan reset failed: {e}")
    try:
        from linkedin_gmail import reconcile_today_counts
        info = reconcile_today_counts()
        print(f"[linkedin] gmail account counters reconciled: {info}")
    except Exception as e:
        print(f"[linkedin] account counter reconcile failed: {e}")


# ---- Source action endpoints (scrape / enrich / export-batch) ----
class SourceActionReq(BaseModel):
    args: dict[str, object] | None = None   # e.g. {"limit": 200, "hiring_only": True, "industry": "B2B"}


def _schema_flag_args(schema: dict, args: dict | None) -> list[str]:
    """Convert a dict of user-supplied args into CLI flags using the schema's
    option_args descriptor, then prepend default_args."""
    scraper = (schema.get("scraper") or {})
    defaults = list(scraper.get("default_args") or [])
    opts = scraper.get("option_args") or []
    # Build a lookup: logical key (normalized) -> flag metadata
    by_key = {}
    for o in opts:
        flag = o.get("flag", "")
        key = flag.lstrip("-").replace("-", "_")
        by_key[key] = o

    chosen = list(defaults)
    for key, val in (args or {}).items():
        meta = by_key.get(key)
        if not meta:
            continue
        flag = meta["flag"]
        t = meta.get("type", "string")
        if t == "bool":
            if val and flag not in chosen:
                chosen.append(flag)
            elif not val and flag in chosen:
                chosen.remove(flag)
        elif t == "int":
            if val is not None and str(val).strip() != "":
                chosen += [flag, str(int(val))]
        else:  # string
            if val:
                chosen += [flag, str(val)]
    return chosen


@app.post("/api/sources/{source_id}/scrape")
def source_scrape(source_id: str, req: SourceActionReq):
    from sources_api import get_source
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Scrape is only available for grab-type sources")
    schema = s.load_schema()
    scraper_rel = (schema.get("scraper") or {}).get("path")
    if not scraper_rel:
        raise HTTPException(400, f"Source '{source_id}' has no scraper declared in schema.json")
    script = str(_GRAB_ROOT / scraper_rel)
    argv = [PY, script, *_schema_flag_args(schema, req.args or {})]
    label = f"Scrape: {source_id}"
    job_id = start_job(argv, label=label)
    LAST_RUNS[source_id] = {
        "kind": "scrape", "argv": argv, "label": label,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
    }
    return {"job_id": job_id, "argv": argv}


@app.post("/api/sources/{source_id}/enrich")
def source_enrich(source_id: str, req: SourceActionReq):
    from sources_api import get_source
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Enrich is only available for grab-type sources")
    schema = s.load_schema()
    enricher = (schema.get("enricher") or {})
    path = enricher.get("path") or "common/enrich.py"
    default_args = list(enricher.get("default_args") or ["--source", source_id])
    extra: list[str] = []
    limit = (req.args or {}).get("limit")
    if limit:
        extra += ["--limit", str(int(limit))]
    argv = [PY, str(_GRAB_ROOT / path), *default_args, *extra]
    label = f"Enrich: {source_id}"
    job_id = start_job(argv, label=label)
    LAST_RUNS[source_id] = {
        "kind": "enrich", "argv": argv, "label": label,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
    }
    return {"job_id": job_id, "argv": argv}


@app.post("/api/sources/{source_id}/collect")
def source_collect(source_id: str, req: SourceActionReq):
    """Single server-side pipeline: scrape -> enrich. One job_id, survives
    browser refresh / tab close. Frontend just polls the one id."""
    from sources_api import get_source
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Collect is only for grab sources")
    schema = s.load_schema()
    scraper_rel = (schema.get("scraper") or {}).get("path")
    if not scraper_rel:
        raise HTTPException(400, f"Source '{source_id}' has no scraper")
    enricher = (schema.get("enricher") or {})
    enricher_rel = enricher.get("path") or "common/enrich.py"
    enricher_default = list(enricher.get("default_args") or ["--source", source_id])

    scrape_argv = [PY, str(_GRAB_ROOT / scraper_rel), *_schema_flag_args(schema, req.args or {})]
    enrich_argv = [PY, str(_GRAB_ROOT / enricher_rel), *enricher_default]
    limit = (req.args or {}).get("limit")
    if limit:
        enrich_argv += ["--limit", str(int(limit))]

    steps = [
        {"label": "Scrape companies", "argv": scrape_argv},
        {"label": "Enrich founders", "argv": enrich_argv},
    ]
    label = f"Collect: {source_id}"
    job_id = start_chain_job(steps, label)
    LAST_RUNS[source_id] = {
        "kind": "collect", "chain": steps, "label": label,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
    }
    return {"job_id": job_id, "steps": [s["label"] for s in steps]}


class CampaignReq(BaseModel):
    lead_ids: list[int]
    max: int | None = None
    industry_tag: str = "YC Portfolio"
    tier: int = 1
    group_by_company: bool = True


@app.post("/api/sources/{source_id}/campaign")
def source_campaign(source_id: str, req: CampaignReq):
    """Single server-side pipeline: export Excel -> generate drafts (Claude via
    Bridge) -> write to Outlook drafts. Returns one job_id."""
    if not req.lead_ids:
        raise HTTPException(400, "lead_ids is required")
    max_rows = req.max or len(req.lead_ids)

    # Step 1: export is a Python callable (no subprocess needed, no Excel
    # hand-off risk). Result is memo'd for later steps.
    export_result: dict = {}

    def do_export():
        res = _export_batch_core(
            source_id=source_id,
            lead_ids=req.lead_ids,
            industry_tag=req.industry_tag,
            tier=req.tier,
            max_rows=max_rows,
            group_by_company=req.group_by_company,
        )
        export_result.update(res)
        return f"wrote {res['rows']} rows to {res['file_name']}"

    # Steps 2 & 3 need the filename from step 1 — use closures that read
    # export_result at call time.
    drafter = _GRAB_ROOT / "mailer" / "generate_drafts_en.py"
    write_outlook = SCRIPTS / "write_to_outlook.py"

    def _run_tracked(argv: list[str], step_name: str) -> int:
        """Spawn a subprocess and register it into JOBS[...]['proc'] so the
        Stop endpoint can kill it. Streams stdout into the job's log buffer."""
        jid = current_job_id[0]
        proc = subprocess.Popen(
            argv, cwd=str(BASE), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1,
        )
        JOBS[jid]["proc"] = proc
        try:
            for line in proc.stdout:
                JOBS[jid]["logs"].append(line.rstrip())
                if len(JOBS[jid]["logs"]) > 3000:
                    JOBS[jid]["logs"] = JOBS[jid]["logs"][-2500:]
            return proc.wait()
        finally:
            JOBS[jid].pop("proc", None)

    # Because run_chain_job starts subprocesses with the argv as-is at the
    # time of construction, we wrap the draft/outlook steps in callables that
    # Popen themselves once the filename is known.
    def do_drafts():
        path = export_result.get("file")
        if not path:
            raise RuntimeError("Export produced no file")
        rc = _run_tracked([PY, str(drafter), "--file", path], "drafts")
        if JOBS[current_job_id[0]].get("stop_requested"):
            raise RuntimeError("stopped")
        if rc != 0:
            raise RuntimeError(f"Drafter exited with code {rc}")
        return "drafts generated"

    def do_outlook():
        path = export_result.get("file")
        if not path:
            raise RuntimeError("Export produced no file")
        rc = _run_tracked([PY, str(write_outlook), "--file", path], "outlook")
        if JOBS[current_job_id[0]].get("stop_requested"):
            raise RuntimeError("stopped")
        if rc != 0:
            raise RuntimeError(f"write_to_outlook exited with code {rc}")
        return f"{export_result.get('rows', 0)} drafts placed in Outlook"

    current_job_id: list[str] = [""]  # filled after start_chain_job below
    steps = [
        {"label": "Export batch", "callable": do_export},
        {"label": "Write drafts (Claude)", "callable": do_drafts},
        {"label": "Place in Outlook", "callable": do_outlook},
    ]
    label = f"Campaign: {source_id}"
    job_id = start_chain_job(steps, label)
    current_job_id[0] = job_id
    LAST_RUNS[source_id] = {
        "kind": "campaign", "label": label,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
    }
    return {"job_id": job_id, "steps": [s["label"] for s in steps]}


class ExportBatchReq(BaseModel):
    lead_ids: list[int] | None = None       # if None, export all leads with verified email
    industry_tag: str = "YC Portfolio"      # what to write in the 'industry' column
    tier: int = 1
    max: int = 100
    group_by_company: bool = True           # merge co-founders into BCC


def _title_priority(title: str) -> int:
    """Lower number = higher priority recipient. CEO/Founder > CTO > other."""
    t = (title or "").lower()
    if "ceo" in t and "founder" in t: return 0
    if "ceo" in t: return 1
    if "founder" in t and "board" not in t: return 2
    if "coo" in t: return 3
    if "cto" in t: return 4
    if "chief" in t or "chair" in t: return 5
    if "vp" in t or "head" in t: return 6
    return 9


def _export_batch_core(
    source_id: str,
    lead_ids: list[int] | None = None,
    industry_tag: str = "YC Portfolio",
    tier: int = 1,
    max_rows: int = 100,
    group_by_company: bool = True,
) -> dict:
    """Callable version of export-batch — used by both the HTTP endpoint and
    the server-side campaign pipeline. Raises RuntimeError on bad state.

    `group_by_company=True` collapses multiple founders at the same company
    into ONE email row: primary recipient by title priority (CEO > Founder >
    CTO), co-founders placed in BCC. Reduces 30 emails to 10 for better UX
    and deliverability."""
    from sources_api import get_source
    import pandas as pd

    s = get_source(source_id)
    if s.type != "grab":
        raise RuntimeError("Export is only for grab sources")
    if not s.db_path.exists():
        raise RuntimeError("Source DB does not exist yet")

    c = sqlite3.connect(str(s.db_path))
    c.row_factory = sqlite3.Row
    try:
        where = ["f.email_status='ok'"]
        params: list = []
        if lead_ids:
            placeholders = ",".join("?" * len(lead_ids))
            where.append(f"l.id IN ({placeholders})")
            params += lead_ids
        sql = f"""
            SELECT l.id as company_id, f.id as founder_id,
                   l.company_name, l.company_domain, l.location, l.extra_data,
                   f.full_name, f.title, f.email, f.linkedin_url
            FROM leads l
            JOIN founders f ON f.lead_id = l.id
            WHERE {' AND '.join(where)}
            ORDER BY l.id, f.id
            LIMIT ?
        """
        rows = c.execute(sql, [*params, int(max_rows)]).fetchall()
    finally:
        c.close()

    if not rows:
        raise RuntimeError("No leads with verified emails match the selection")

    # Optional: group by company_id and keep only the highest-priority founder
    # as primary recipient; others become BCC addresses on the same row.
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["company_id"], []).append(dict(r))
    if group_by_company:
        chosen: list[tuple[dict, list[dict]]] = []
        for cid, members in grouped.items():
            members.sort(key=lambda m: _title_priority(m.get("title") or ""))
            primary, others = members[0], members[1:]
            chosen.append((primary, others))
        rows_to_write = chosen
    else:
        rows_to_write = [(dict(r), []) for r in rows]

    today = dt.date.today().isoformat()
    records = []
    all_exported_members: list[tuple[int, int]] = []  # (company_id, founder_id) for exported_leads table
    for primary, cofounders in rows_to_write:
        r = primary
        extra = json.loads(r["extra_data"] or "{}")
        industry = extra.get("industry") or industry_tag
        # Personalization context for the LLM drafter — keep small and signal-heavy.
        meta = extra.get("company_meta") or {}
        personalization = {
            "one_liner": extra.get("one_liner"),
            "long_description": (extra.get("long_description") or "")[:800],
            "batch": extra.get("batch"),
            "stage": extra.get("stage"),
            "team_size": extra.get("team_size"),
            "tags": extra.get("tags"),
            "is_hiring": extra.get("is_hiring"),
            "top_company": extra.get("top_company"),
            "year_founded": meta.get("year_founded"),
            "source": source_id,
            # Socials the LLM might reference or recruiters might click later
            "company_linkedin": meta.get("linkedin_url"),
            "company_twitter": meta.get("twitter_url"),
            "company_github": meta.get("github_url"),
            "company_crunchbase": meta.get("crunchbase_url"),
            "company_facebook": meta.get("facebook_url"),
            "person_linkedin": r["linkedin_url"],
        }
        records.append({
            "lead_id": f"{source_id[:2].upper()}{r['founder_id']:06d}",
            "name": r["full_name"],
            "salutation": "",
            "title": r["title"] or "",
            "company": r["company_name"],
            "email": r["email"],
            "phone": "",
            "xing": "",
            "linkedin": r["linkedin_url"] or "",
            "industry": industry,
            "sub_industry": extra.get("subindustry") or "",
            "domain": r["company_domain"] or "",
            "website": extra.get("website") or "",
            "city": r["location"] or "",
            "dealfront_link": "",
            "source_file": f"{source_id}:db",
            "tier": int(tier),
            "is_owner": 1,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "email_valid": 1,
            "email_invalid_reason": "ok",
            "email_verified_at": dt.datetime.now().isoformat(timespec="seconds"),
            "status": "New",
            "batch_date": today,
            "draft_subject": "",
            "draft_body": "",
            "draft_language": "en",
            "generated_at": "",
            "outlook_entry_id": "",
            "sent_at": "",
            "notes": f"Imported from source={source_id}",
            "personalization": json.dumps(personalization, ensure_ascii=False),
            "bcc": ", ".join(cf["email"] for cf in cofounders if cf.get("email")),
            "bcc_names": ", ".join(cf["full_name"] for cf in cofounders if cf.get("full_name")),
        })
        all_exported_members.append((r["company_id"], r["founder_id"]))
        for cf in cofounders:
            all_exported_members.append((cf["company_id"], cf["founder_id"]))

    out_dir = _GRAB_ROOT / "mailer" / "batches"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today}_{source_id}_{len(records)}.xlsx"
    df = pd.DataFrame(records)
    with pd.ExcelWriter(
        str(out_path),
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_urls": False}},
    ) as w:
        df.to_excel(w, sheet_name="Batch", index=False)

    # Record exported leads so the UI can grey them out / exclude
    c2 = sqlite3.connect(str(s.db_path))
    try:
        c2.executescript("""
        CREATE TABLE IF NOT EXISTS exported_leads (
            lead_id INTEGER NOT NULL,
            founder_id INTEGER,
            batch_file TEXT,
            exported_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            PRIMARY KEY (lead_id, founder_id)
        );
        """)
        c2.executemany(
            "INSERT OR IGNORE INTO exported_leads (lead_id, founder_id, batch_file) VALUES (?,?,?)",
            [(cid, fid, out_path.name) for (cid, fid) in all_exported_members],
        )
        # Clear the attention flag on exported companies — user has acted on them.
        exported_company_ids = {cid for (cid, _fid) in all_exported_members}
        if exported_company_ids:
            ph = ",".join("?" * len(exported_company_ids))
            c2.execute(
                f"UPDATE leads SET needs_attention=0 WHERE id IN ({ph})",
                list(exported_company_ids),
            )
        c2.commit()
    finally:
        c2.close()

    return {
        "ok": True,
        "rows": len(records),
        "file": str(out_path),
        "file_name": out_path.name,
    }


@app.post("/api/sources/{source_id}/export-batch")
def source_export_batch(source_id: str, req: ExportBatchReq):
    """HTTP wrapper around _export_batch_core."""
    try:
        res = _export_batch_core(
            source_id=source_id,
            lead_ids=req.lead_ids,
            industry_tag=req.industry_tag,
            tier=req.tier,
            max_rows=req.max,
            group_by_company=req.group_by_company,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    res["next_step"] = (
        "Run generate_drafts.py on this file to fill draft_subject/draft_body, "
        "then write_to_outlook.py to push to Outlook Drafts."
    )
    return res


# ---- Campaign batches per source (ready-to-run state + actions) ----
def _grab_batches_dir() -> Path:
    d = _GRAB_ROOT / "mailer" / "batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _batch_status(path: Path) -> dict:
    """Read the Excel once and summarise its progress state. On read failure
    still returns numeric counters (zeros) so the UI stepper doesn't render
    NaN/NaN; the `error` field exposes the cause."""
    import pandas as pd
    try:
        df = pd.read_excel(path)
    except Exception as e:
        return {
            "total": 0, "drafted": 0, "in_outlook": 0, "sent": 0,
            "state": "fresh", "error": f"read failed: {e}",
        }
    total = len(df)

    def _filled(col: str) -> int:
        if col not in df.columns:
            return 0
        s = df[col].astype(str).str.strip().str.lower()
        mask = (s != "") & (s != "nan") & (s != "none")
        return int(mask.sum())

    drafted = _filled("draft_subject")
    in_outlook = _filled("outlook_entry_id")
    sent = _filled("sent_at")

    if sent == total and total > 0:
        state = "sent"
    elif in_outlook == total and total > 0:
        state = "in_outlook"
    elif drafted == total and total > 0:
        state = "drafted"
    elif sent or in_outlook or drafted:
        state = "partial"
    else:
        state = "fresh"

    return {
        "total": total,
        "drafted": drafted,
        "in_outlook": in_outlook,
        "sent": sent,
        "state": state,
    }


@app.get("/api/campaigns/batches")
def all_campaign_batches():
    """Cross-source aggregator: lists every batch file from every registered
    source. Powers the central Campaigns tab."""
    from sources_api import _SOURCES
    known = set(_SOURCES.keys())
    out = []

    # --- Grab-source batches: grab_leads/mailer/batches/<date>_<source>_<n>.xlsx ---
    d = _grab_batches_dir()
    for f in d.glob("*.xlsx"):
        stem = f.stem
        parts = stem.split("_")
        source_id = None
        if len(parts) >= 3:
            for sid in known:
                prefix = f"{parts[0]}_{sid}_"
                if stem.startswith(prefix):
                    source_id = sid
                    break
        if source_id is None:
            continue
        stat = f.stat()
        out.append({
            "name": f.name, "path": str(f), "source": source_id,
            "size_kb": round(stat.st_size / 1024),
            "created_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            **_batch_status(f),
        })

    # --- Marcel daily batches: Database/Marcel Data/01_Daily_Batches/<date>_<industry>.xlsx ---
    # Marcel has a different pipeline (DB-picked per-run, not re-runnable per
    # file) — tag them source="marcel" so the UI hides per-batch action
    # buttons but still surfaces them as history.
    marcel_dir = BASE / "Database" / "Marcel Data" / "01_Daily_Batches"
    if marcel_dir.exists():
        for f in marcel_dir.glob("*.xlsx"):
            stat = f.stat()
            out.append({
                "name": f.name, "path": str(f), "source": "marcel",
                "size_kb": round(stat.st_size / 1024),
                "created_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                **_batch_status(f),
            })

    out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"batches": out, "count": len(out)}


@app.get("/api/sources/{source_id}/batches")
def source_batches(source_id: str):
    """List batches produced from this source. Scans `Grab Leads/mailer/batches/`
    for files whose name starts with the date prefix + source_id."""
    from sources_api import get_source
    get_source(source_id)  # validates registration
    d = _grab_batches_dir()
    out = []
    for f in sorted(d.glob(f"*_{source_id}_*.xlsx"), reverse=True):
        stat = f.stat()
        status = _batch_status(f)
        out.append({
            "name": f.name,
            "path": str(f),
            "size_kb": round(stat.st_size / 1024),
            "created_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            **status,
        })
    return {"source": source_id, "batches": out, "count": len(out)}


def _resolve_grab_batch(source_id: str, name: str) -> Path:
    from sources_api import get_source
    get_source(source_id)
    d = _grab_batches_dir()
    p = (d / name).resolve()
    if not str(p).startswith(str(d.resolve())) or not p.exists():
        raise HTTPException(404, f"Batch file not found: {name}")
    return p


@app.post("/api/sources/{source_id}/batches/{name}/generate-drafts")
def batch_generate_drafts(source_id: str, name: str):
    p = _resolve_grab_batch(source_id, name)
    # Grab sources use the English template drafter (signal-aware).
    drafter = _GRAB_ROOT / "mailer" / "generate_drafts_en.py"
    argv = [PY, str(drafter), "--file", str(p)]
    job_id = start_job(argv, f"Generate drafts: {name}")
    return {"job_id": job_id}


@app.post("/api/sources/{source_id}/batches/{name}/write-outlook")
def batch_write_outlook(source_id: str, name: str):
    p = _resolve_grab_batch(source_id, name)
    argv = [PY, str(SCRIPTS / "write_to_outlook.py"), "--file", str(p)]
    job_id = start_job(argv, f"Write Outlook drafts: {name}")
    return {"job_id": job_id}


class SendBatchReq(BaseModel):
    count: int = 10


@app.post("/api/sources/{source_id}/batches/{name}/send")
def batch_send(source_id: str, name: str, req: SendBatchReq):
    p = _resolve_grab_batch(source_id, name)
    # Guard: nothing to send if the batch is already fully sent. Avoids
    # spawning a no-op Outlook COM process that can surface a confusing error.
    status = _batch_status(p)
    total = status.get("total") or 0
    sent = status.get("sent") or 0
    remaining = max(0, total - sent)
    if remaining == 0:
        raise HTTPException(400, f"Batch '{name}' is fully sent ({sent}/{total}).")
    count = max(1, min(int(req.count), remaining))
    argv = [PY, str(SCRIPTS / "send_drafts.py"), "--file", str(p), "--count", str(count)]
    job_id = start_job(argv, f"Send {count} drafts: {name}")
    return {"job_id": job_id, "count": count, "remaining_before": remaining}


@app.delete("/api/sources/{source_id}/batches/{name}")
def batch_delete(source_id: str, name: str):
    p = _resolve_grab_batch(source_id, name)
    p.unlink()
    return {"ok": True, "deleted": name}


@app.post("/api/sources/{source_id}/reset-all")
def source_reset_all(source_id: str):
    """Destructive: wipes this source's DB, raw dumps, logs, and any batch
    files whose name starts with this source_id. Use while iterating/testing."""
    from sources_api import get_source
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Reset is only for grab-type sources")

    removed = {"db_rows": 0, "raw_files": 0, "logs": 0, "batches": 0}

    # 1. Wipe DB contents (safer than deleting the file on Windows where
    # SQLite/antivirus may hold a transient lock). Keeps the file + schema.
    if s.db_path.exists():
        try:
            c = sqlite3.connect(str(s.db_path))
            try:
                tables = ("exported_leads", "founders", "leads")  # FK order
                total = 0
                for t in tables:
                    exists = c.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (t,),
                    ).fetchone()
                    if exists:
                        cur = c.execute(f"DELETE FROM {t}")
                        total += cur.rowcount or 0
                # Reset AUTOINCREMENT counters so next scrape starts fresh
                try:
                    c.execute("DELETE FROM sqlite_sequence")
                except sqlite3.OperationalError:
                    pass
                c.commit()
                removed["db_rows"] = total
            finally:
                c.close()
            # VACUUM to reclaim file space (optional, best-effort)
            try:
                c2 = sqlite3.connect(str(s.db_path))
                c2.execute("VACUUM")
                c2.close()
            except Exception:
                pass
        except Exception as e:
            raise HTTPException(500, f"Could not wipe DB: {e}")

    # 2. Raw dumps
    raw_dir = s.db_path.parent / "raw"
    if raw_dir.exists():
        for f in raw_dir.glob("*.json"):
            try:
                f.unlink()
                removed["raw_files"] += 1
            except Exception:
                pass

    # 3. Logs for this source
    logs_dir = _GRAB_ROOT / "logs"
    if logs_dir.exists():
        for f in logs_dir.glob(f"{source_id}_*.log"):
            try:
                f.unlink()
                removed["logs"] += 1
            except Exception:
                pass

    # 4. Batch files
    batches_dir = _grab_batches_dir()
    for f in batches_dir.glob(f"*_{source_id}_*.xlsx"):
        try:
            f.unlink()
            removed["batches"] += 1
        except Exception:
            pass

    return {"ok": True, "source": source_id, "removed": removed}


# ---- DB helpers ----
def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def q_one(sql: str, *params):
    c = conn()
    try:
        r = c.execute(sql, params).fetchone()
        return r[0] if r else 0
    finally:
        c.close()


def q_all(sql: str, *params):
    c = conn()
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


# ---- In-memory job tracker for long-running actions ----
JOBS: dict[str, dict] = {}


def run_script_job(job_id: str, argv: list[str]):
    """Run a Python script as subprocess, capture stdout live into JOBS[job_id]."""
    JOBS[job_id]["status"] = "running"
    JOBS[job_id]["logs"] = []
    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(BASE),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        JOBS[job_id]["pid"] = proc.pid
        JOBS[job_id]["proc"] = proc   # handle for stop
        for line in proc.stdout:
            JOBS[job_id]["logs"].append(line.rstrip())
            # cap logs
            if len(JOBS[job_id]["logs"]) > 2000:
                JOBS[job_id]["logs"] = JOBS[job_id]["logs"][-1500:]
        rc = proc.wait()
        if JOBS[job_id].get("stop_requested"):
            JOBS[job_id]["status"] = "stopped"
        else:
            JOBS[job_id]["status"] = "done" if rc == 0 else "error"
        JOBS[job_id]["returncode"] = rc
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
    finally:
        JOBS[job_id]["ended_at"] = dt.datetime.now().isoformat(timespec="seconds")
        JOBS[job_id].pop("proc", None)
        _evict_old_jobs()


# Per-source "last run" so Resume can replay the same argv+label
LAST_RUNS: dict[str, dict] = {}


@app.post("/api/jobs/{job_id}/stop")
def job_stop(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    if j.get("status") not in ("queued", "running"):
        return {"ok": False, "status": j.get("status"), "note": "job already finished"}
    j["stop_requested"] = True
    proc = j.get("proc")
    if proc is not None:
        try:
            # Windows: kill whole process tree to catch orphaned children
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                )
            else:
                proc.terminate()
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True, "job_id": job_id, "stopped": True}


import re as _re

_SCRAPE_PAGE = _re.compile(r"Fetching page (\d+)")
_SCRAPE_TOTAL = _re.compile(r"Total matches: (\d+) \(across (\d+) pages\)")
_ENRICH_TOTAL = _re.compile(r"Processing (\d+) companies")
_ENRICH_ROW = _re.compile(r"^\s*\[(\d+)\]")


def _parse_progress(kind: str, logs: list[str]) -> dict:
    """Best-effort progress extraction from streamed stdout."""
    total: int | None = None
    current: int | None = None
    last_line = (logs[-1] if logs else "")[:200]

    if kind == "scrape":
        for line in logs:
            m = _SCRAPE_TOTAL.search(line)
            if m:
                total = int(m.group(2))  # total pages
        pages_seen = [int(m.group(1)) for line in logs
                      if (m := _SCRAPE_PAGE.search(line))]
        if pages_seen:
            current = max(pages_seen) + 1
        unit = "pages"
    elif kind == "enrich":
        for line in logs:
            m = _ENRICH_TOTAL.search(line)
            if m:
                total = int(m.group(1))
        rows_seen = [int(m.group(1)) for line in logs
                     if (m := _ENRICH_ROW.search(line))]
        if rows_seen:
            current = max(rows_seen)
        unit = "companies"
    else:
        unit = ""

    percent = None
    if total and current is not None:
        percent = min(100, round((current / total) * 100))

    return {
        "current": current, "total": total, "percent": percent,
        "unit": unit, "last_line": last_line,
    }


@app.get("/api/sources/{source_id}/last-run")
def source_last_run(source_id: str):
    """Return the most recent scrape/enrich invocation for this source, if any."""
    info = LAST_RUNS.get(source_id)
    if not info:
        return {"exists": False}
    # Attach job state if still tracked
    job = JOBS.get(info.get("job_id") or "")
    progress = None
    if job and job.get("status") in ("queued", "running"):
        progress = _parse_progress(info.get("kind", ""), job.get("logs", []))
    return {
        "exists": True,
        "kind": info.get("kind"),
        "argv": info.get("argv"),
        "label": info.get("label"),
        "started_at": info.get("started_at"),
        "job_id": info.get("job_id"),
        "status": (job or {}).get("status"),
        "progress": progress,
    }


@app.post("/api/sources/{source_id}/resume-last")
def source_resume_last(source_id: str):
    """Re-run the last scrape/enrich argv for this source. Safe because both
    scraper and enricher dedupe via UNIQUE constraint / --only-missing."""
    info = LAST_RUNS.get(source_id)
    if not info or not info.get("argv"):
        raise HTTPException(400, "No previous run to resume")
    argv = list(info["argv"])
    label = f"Resume {info.get('kind','job')}: {source_id}"
    job_id = start_job(argv, label)
    LAST_RUNS[source_id] = {
        **info,
        "job_id": job_id,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    return {"job_id": job_id, "argv": argv}


def _evict_old_jobs():
    """Drop finished jobs older than JOB_RETENTION_SECONDS to bound memory."""
    now = dt.datetime.now()
    for jid in list(JOBS.keys()):
        j = JOBS[jid]
        if j.get("status") in ("done", "error"):
            ended = j.get("ended_at")
            if not ended:
                continue
            try:
                age = (now - dt.datetime.fromisoformat(ended)).total_seconds()
            except Exception:
                continue
            if age > JOB_RETENTION_SECONDS:
                JOBS.pop(jid, None)


def _pipeline_running() -> bool:
    return any(
        j.get("status") in ("queued", "running")
        and str(j.get("label", "")).startswith("Pipeline:")
        for j in JOBS.values()
    )


def start_job(argv: list[str], label: str) -> str:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "label": label,
        "argv": argv,
        "status": "queued",
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "logs": [],
    }
    t = threading.Thread(target=run_script_job, args=(job_id, argv), daemon=True)
    t.start()
    return job_id


# ---- Chain job: one job_id, N sequential steps ----
ChainStep = dict  # {"label": "scrape", "argv": [...] } OR {"label": "export", "callable": fn}


def run_chain_job(job_id: str, steps: list[ChainStep]):
    """Run steps sequentially under ONE job_id. A step can be an argv subprocess
    or an in-process Python callable. Any step failure aborts the chain."""
    JOBS[job_id]["status"] = "running"
    JOBS[job_id]["logs"] = []
    JOBS[job_id]["step_total"] = len(steps)
    JOBS[job_id]["step_index"] = 0
    JOBS[job_id]["step_label"] = ""

    def _log(s: str):
        JOBS[job_id]["logs"].append(s)
        if len(JOBS[job_id]["logs"]) > 3000:
            JOBS[job_id]["logs"] = JOBS[job_id]["logs"][-2500:]

    try:
        for idx, step in enumerate(steps, start=1):
            if JOBS[job_id].get("stop_requested"):
                JOBS[job_id]["status"] = "stopped"
                return
            JOBS[job_id]["step_index"] = idx
            JOBS[job_id]["step_label"] = step.get("label", f"step {idx}")
            _log(f"\n=== [{idx}/{len(steps)}] {JOBS[job_id]['step_label']} ===")

            argv = step.get("argv")
            fn = step.get("callable")
            if argv:
                try:
                    proc = subprocess.Popen(
                        argv, cwd=str(BASE), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                        errors="replace", bufsize=1,
                    )
                    JOBS[job_id]["proc"] = proc
                    for line in proc.stdout:
                        _log(line.rstrip())
                    rc = proc.wait()
                    if JOBS[job_id].get("stop_requested"):
                        JOBS[job_id]["status"] = "stopped"
                        return
                    if rc != 0:
                        JOBS[job_id]["status"] = "error"
                        JOBS[job_id]["returncode"] = rc
                        _log(f"Step failed with code {rc}")
                        return
                finally:
                    JOBS[job_id].pop("proc", None)
            elif fn:
                try:
                    result = fn()
                    JOBS[job_id].setdefault("step_results", {})[
                        JOBS[job_id]["step_label"]
                    ] = result
                    _log(f"OK: {result}")
                except Exception as e:
                    if JOBS[job_id].get("stop_requested") or str(e) == "stopped":
                        JOBS[job_id]["status"] = "stopped"
                        _log("[STOPPED] by user")
                        return
                    JOBS[job_id]["status"] = "error"
                    JOBS[job_id]["error"] = str(e)
                    _log(f"Callable failed: {e}")
                    return
            else:
                JOBS[job_id]["status"] = "error"
                _log(f"Step {idx} has no argv/callable")
                return

        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["returncode"] = 0
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
        _log(f"Chain exception: {e}")
    finally:
        JOBS[job_id]["ended_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _evict_old_jobs()


def start_chain_job(steps: list[ChainStep], label: str) -> str:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "id": job_id,
        "label": label,
        "status": "queued",
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "logs": [],
        "step_total": len(steps),
        "step_index": 0,
        "step_label": "",
    }
    t = threading.Thread(target=run_chain_job, args=(job_id, steps), daemon=True)
    t.start()
    return job_id


# ---- Daily auto-scrape scheduler ------------------------------------------
# Lightweight: one background thread checks every 60s whether any source's
# schedule window (hour:minute, local time) has been hit today and isn't yet
# fired. Schedules are persisted to schedules.json so they survive restart.

SCHEDULES_FILE = Path(__file__).parent / "schedules.json"


def _load_schedules() -> dict:
    if not SCHEDULES_FILE.exists():
        return {}
    try:
        return json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        # Preserve the corrupt file before returning an empty config so user
        # doesn't silently lose their schedule.
        print(f"[scheduler] schedules.json corrupt ({e}); renaming to .corrupt")
        try:
            SCHEDULES_FILE.rename(SCHEDULES_FILE.with_suffix(".json.corrupt"))
        except Exception:
            pass
        return {}


def _save_schedules(data: dict) -> None:
    SCHEDULES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _fire_auto_collect(source_id: str) -> str | None:
    """Kick off the standard collect (scrape -> enrich) chain for a source."""
    try:
        from sources_api import get_source
        s = get_source(source_id)
        if s.type != "grab":
            return None
        schema = s.load_schema()
        scraper_rel = (schema.get("scraper") or {}).get("path")
        if not scraper_rel:
            return None
        enricher = schema.get("enricher") or {}
        enricher_rel = enricher.get("path") or "common/enrich.py"
        enricher_default = list(enricher.get("default_args") or ["--source", source_id])

        scrape_argv = [PY, str(_GRAB_ROOT / scraper_rel), *_schema_flag_args(schema, {})]
        enrich_argv = [PY, str(_GRAB_ROOT / enricher_rel), *enricher_default]
        steps = [
            {"label": "Scrape companies", "argv": scrape_argv},
            {"label": "Enrich founders", "argv": enrich_argv},
        ]
        label = f"Auto-collect: {source_id}"
        job_id = start_chain_job(steps, label)
        LAST_RUNS[source_id] = {
            "kind": "collect", "chain": steps, "label": label,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "job_id": job_id,
        }
        return job_id
    except Exception as e:
        print(f"[scheduler] fire failed for {source_id}: {e}")
        return None


def _scheduler_loop():
    """Every 60s, check each enabled schedule. If hour:minute matches and we
    haven't already fired today, kick off the collect chain."""
    import time as _time
    while True:
        try:
            data = _load_schedules()
            now = dt.datetime.now()
            today = now.date().isoformat()
            dirty = False
            for source_id, cfg in list(data.items()):
                if not cfg.get("enabled"):
                    continue
                hh = int(cfg.get("hour", 2))
                mm = int(cfg.get("minute", 0))
                last = cfg.get("last_fired_date")
                if last == today:
                    continue  # already fired today
                if now.hour > hh or (now.hour == hh and now.minute >= mm):
                    job_id = _fire_auto_collect(source_id)
                    if job_id:
                        cfg["last_fired_date"] = today
                        cfg["last_fired_at"] = now.isoformat(timespec="seconds")
                        cfg["last_job_id"] = job_id
                        dirty = True
            if dirty:
                _save_schedules(data)
        except Exception as e:
            print(f"[scheduler] loop error: {e}")
        _time.sleep(60)


@app.on_event("startup")
def _start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()


def _linkedin_poll_loop():
    """Every 60s: (1) autopilot tick, (2) scheduled-send tick. Every 5th
    iteration (~5 min), poll Gmail INBOX for replies/bounces if Gmail is
    connected."""
    import time as _time
    from linkedin_api import (
        _poll_and_store,
        _autopilot_tick,
        _scheduler_tick,
        _stale_drafts_sweep,
        _digest_tick,
        _followups_tick,
    )
    from linkedin_gmail import get_credentials as _gmail_creds
    tick = 0
    while True:
        try:
            _autopilot_tick()
            _scheduler_tick()
            if tick % 5 == 0 and _gmail_creds() is not None:
                _poll_and_store()
            # Stale-draft sweep — once per hour is plenty since the
            # sweeper itself only does work on a once-a-day basis (it
            # bails when last_run_date == today).
            if tick % 60 == 0:
                _stale_drafts_sweep()
            # Digest — checked every minute so it fires within ~60s of
            # 9am sharp. The tick is cheap (one wall-clock comparison)
            # before bailing.
            _digest_tick()
            # Auto follow-ups — same minute-precision check, bails fast
            # when the toggle is off (default) or the hour hasn't hit.
            _followups_tick()
        except Exception as e:
            print(f"[linkedin-poll] {e}")
        tick += 1
        _time.sleep(60)


@app.on_event("startup")
def _start_linkedin_poll():
    t = threading.Thread(target=_linkedin_poll_loop, daemon=True)
    t.start()


class AutoRunReq(BaseModel):
    enabled: bool
    hour: int = 2       # local hour 0-23
    minute: int = 0     # 0-59


@app.get("/api/sources/{source_id}/auto-run")
def get_auto_run(source_id: str):
    from sources_api import get_source
    get_source(source_id)
    data = _load_schedules()
    cfg = data.get(source_id) or {"enabled": False, "hour": 2, "minute": 0}
    # Compute next-fire hint
    now = dt.datetime.now()
    next_fire = None
    if cfg.get("enabled"):
        today_slot = now.replace(hour=int(cfg.get("hour", 2)), minute=int(cfg.get("minute", 0)), second=0, microsecond=0)
        next_fire = (
            today_slot if today_slot > now and cfg.get("last_fired_date") != now.date().isoformat()
            else today_slot + dt.timedelta(days=1)
        ).isoformat(timespec="seconds")
    return {**cfg, "source": source_id, "next_fire": next_fire}


@app.post("/api/sources/{source_id}/auto-run")
def set_auto_run(source_id: str, req: AutoRunReq):
    from sources_api import get_source
    s = get_source(source_id)
    if s.type != "grab":
        raise HTTPException(400, "Auto-run is only for grab sources")
    if not (0 <= req.hour <= 23 and 0 <= req.minute <= 59):
        raise HTTPException(400, "hour/minute out of range")
    data = _load_schedules()
    prior = data.get(source_id, {})
    cfg = {
        **prior,
        "enabled": bool(req.enabled),
        "hour": int(req.hour),
        "minute": int(req.minute),
    }
    # If the configured time has already passed today, mark it as "fired today"
    # so the scheduler bumps the next fire to tomorrow instead of firing in the
    # next 60s. User's intent when enabling at 10am with hh=02 is tomorrow 2am.
    if cfg["enabled"]:
        now = dt.datetime.now()
        today_slot = now.replace(
            hour=cfg["hour"], minute=cfg["minute"], second=0, microsecond=0
        )
        if today_slot <= now:
            cfg["last_fired_date"] = now.date().isoformat()
            cfg.setdefault("last_fired_at", None)
    data[source_id] = cfg
    _save_schedules(data)
    return get_auto_run(source_id)


# ---- Read endpoints ----
@app.get("/api/stats")
def stats():
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
        "daily_quota": DAILY_QUOTA,
        "remaining_today": max(0, DAILY_QUOTA - q_one(
            "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
        )),
    }


@app.get("/api/overview")
def overview():
    """Cross-source aggregate for the main Overview page. Combines Marcel's
    DB stats with grab-source batch file counts so "sent today", "drafted",
    and total leads reflect every source in one view."""
    today = dt.date.today().isoformat()

    # --- Marcel side (DB-driven) ---
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

    # --- Grab sources (scan per-source DB + batch files) ---
    import pandas as pd
    from sources_api import _SOURCES

    grab_leads = 0
    grab_drafted = 0
    grab_sent_today = 0
    grab_total_sent = 0
    leads_by_source: dict[str, int] = {"marcel": marcel_leads}
    for sid, src in _SOURCES.items():
        if src.type != "grab":
            continue
        per_source = 0
        if src.db_path.exists():
            try:
                c = sqlite3.connect(str(src.db_path))
                per_source = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
                c.close()
            except Exception:
                pass
        leads_by_source[sid] = per_source
        grab_leads += per_source
        # Scan this source's batch files
        for f in _grab_batches_dir().glob(f"*_{sid}_*.xlsx"):
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
        "daily_quota": DAILY_QUOTA,
        "remaining_today": max(0, DAILY_QUOTA - sent_today),
        "has_replies": total_replies > 0,
    }


@app.get("/api/funnel")
def funnel():
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


@app.get("/api/daily-activity")
def daily_activity(days: int = 30):
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
    by = {}
    for r in sent:
        by.setdefault(r["day"], {"day": r["day"], "sent": 0, "replies": 0})["sent"] = r["sent"]
    for r in repl:
        by.setdefault(r["day"], {"day": r["day"], "sent": 0, "replies": 0})["replies"] = r["replies"]
    return sorted(by.values(), key=lambda x: x["day"])


@app.get("/api/industries")
def industries():
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


@app.get("/api/hot-leads")
def hot_leads(limit: int = 20):
    return q_all("""
        SELECT r.id, r.lead_id, l.name, l.company, l.industry, l.city,
               r.sentiment, r.reply_at, r.snippet, r.handled
        FROM replies r JOIN leads l ON r.lead_id = l.lead_id
        WHERE r.handled = 0 AND r.sentiment IN ('Positive','Objection')
        ORDER BY r.reply_at DESC
        LIMIT ?
    """, limit)


@app.get("/api/recent-sent")
def recent_sent(limit: int = 25):
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


@app.get("/api/leads")
def leads(
    status: Optional[str] = None,
    industry: Optional[str] = None,
    tier: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    where = ["1=1"]
    params = []
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
    params += [limit, offset]
    items = q_all(sql, *params)
    total = q_one(f"""
        SELECT COUNT(*) FROM leads l JOIN lead_status ls ON l.lead_id = ls.lead_id
        WHERE {' AND '.join(where)}
    """, *params[:-2])
    return {"items": items, "total": total}


@app.get("/api/lead/{lead_id}")
def lead_detail(lead_id: str):
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


@app.get("/api/batches")
def batches(limit: int = 20):
    return q_all(
        "SELECT * FROM daily_batches ORDER BY batch_date DESC LIMIT ?", limit
    )


# ---- Action endpoints ----
class PickBody(BaseModel):
    industry: str
    count: int = 10
    tier: Optional[int] = None
    city: Optional[str] = None


@app.post("/api/actions/pick-batch")
def pick_batch(body: PickBody):
    argv = [PY, str(SCRIPTS / "pick_batch.py"),
            "--industry", body.industry, "--count", str(body.count)]
    if body.tier:
        argv += ["--tier", str(body.tier)]
    if body.city:
        argv += ["--city", body.city]
    job_id = start_job(argv, f"Pick {body.count} from {body.industry}")
    return {"job_id": job_id}


class BatchFileBody(BaseModel):
    file: str  # filename only, resolved under BATCHES_DIR
    limit: Optional[int] = None


def resolve_batch(file: str) -> str:
    p = (BATCHES_DIR / file).resolve()
    if not p.exists() or not str(p).startswith(str(BATCHES_DIR)):
        raise HTTPException(400, f"Batch file not found: {file}")
    return str(p)


@app.post("/api/actions/generate-drafts")
def generate_drafts(body: BatchFileBody):
    path = resolve_batch(body.file)
    argv = [PY, str(SCRIPTS / "generate_drafts.py"), "--file", path]
    if body.limit:
        argv += ["--limit", str(body.limit)]
    return {"job_id": start_job(argv, f"Generate drafts: {body.file}")}


@app.post("/api/actions/write-outlook")
def write_outlook(body: BatchFileBody):
    path = resolve_batch(body.file)
    argv = [PY, str(SCRIPTS / "write_to_outlook.py"), "--file", path]
    return {"job_id": start_job(argv, f"Write to Outlook: {body.file}")}


class RunPipelineBody(BaseModel):
    industry: str
    count: int
    tier: Optional[int] = None
    send_mode: str = "schedule"  # "now" | "schedule" | "draft"
    no_jitter: bool = False


OUTLOOK_ACCOUNT = "pradip@bitcodingsolutions.com"


def _check_outlook() -> tuple[bool, bool, Optional[str]]:
    """Return (outlook_running, account_present, error).

    Uses a cheap COM dispatch; if Outlook isn't running it auto-starts, so we
    only call this once per preflight request. The account check is the real
    gate — without it write_to_outlook.py fails mid-pipeline.
    """
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application")
        accounts = [a.SmtpAddress.lower() for a in outlook.Session.Accounts]
        return True, OUTLOOK_ACCOUNT.lower() in accounts, None
    except Exception as e:
        return False, False, str(e)[:200]


@app.post("/api/actions/backup-db")
def backup_db():
    """Trigger a timestamped SQLite backup of leads.db.

    Delegates to scripts/backup_db.py so the same code path is used by
    Windows Task Scheduler + manual UI trigger.
    """
    argv = [PY, str(SCRIPTS / "backup_db.py")]
    return {"job_id": start_job(argv, "Backup leads.db")}


@app.get("/api/actions/preflight")
def preflight():
    """Verify all external dependencies before a pipeline run.

    Returns a structured report; the UI blocks Run Pipeline when any gate
    fails. Prevents half-consumed state (leads marked 'Picked' even though
    Outlook/Bridge are down).
    """
    checks: list[dict] = []

    # DB reachable
    db_ok = True
    try:
        q_one("SELECT 1")
    except Exception as e:
        db_ok = False
        checks.append({"key": "db", "ok": False, "error": str(e)[:200]})
    else:
        checks.append({"key": "db", "ok": True})

    # Bridge
    bridge_ok = _ping_bridge(timeout=1.0)
    checks.append({"key": "bridge", "ok": bridge_ok,
                   "error": None if bridge_ok else "Bridge not responding on :8765"})

    # Outlook + account
    outlook_ok, account_ok, err = _check_outlook()
    checks.append({"key": "outlook",
                   "ok": outlook_ok,
                   "error": None if outlook_ok else (err or "Outlook COM dispatch failed")})
    checks.append({
        "key": "outlook_account",
        "ok": account_ok,
        "error": None if account_ok else f"{OUTLOOK_ACCOUNT} not configured in Outlook Desktop",
    })

    all_ok = all(c["ok"] for c in checks)
    return {"ok": all_ok, "checks": checks}


@app.post("/api/actions/run-pipeline")
def run_pipeline(body: RunPipelineBody):
    """Orchestrate the whole flow in one job: pick -> generate -> Outlook -> (send/schedule/draft)."""
    if body.send_mode not in ("now", "schedule", "draft"):
        raise HTTPException(400, "send_mode must be now/schedule/draft")
    if body.count <= 0:
        raise HTTPException(400, "count must be > 0")
    # Concurrency guard: only one pipeline at a time
    if _pipeline_running():
        raise HTTPException(409, "Another pipeline is already running")
    # Pre-flight: block before leads get marked 'Picked' if dependencies are down.
    # 'draft' mode still needs Bridge (generate) + Outlook (push), skip Outlook
    # check only when send_mode is... actually all modes need Outlook for the
    # drafts stage, so check everything every time.
    pf = preflight()
    if not pf["ok"]:
        reasons = [c["error"] for c in pf["checks"] if not c["ok"] and c.get("error")]
        raise HTTPException(
            503, "Pre-flight failed: " + "; ".join(reasons) if reasons else "Pre-flight failed",
        )
    # Server-side quota enforcement for 'now' mode (schedule/draft don't send today)
    if body.send_mode == "now":
        today = dt.date.today().isoformat()
        sent_today = q_one(
            "SELECT COUNT(*) FROM emails_sent WHERE DATE(sent_at)=?", today
        )
        remaining = max(0, DAILY_QUOTA - sent_today)
        if body.count > remaining:
            raise HTTPException(
                400,
                f"Daily quota exceeded: {body.count} requested, {remaining} left today",
            )
    argv = [PY, str(SCRIPTS / "run_pipeline.py"),
            "--industry", body.industry,
            "--count", str(body.count),
            "--send-mode", body.send_mode]
    if body.tier:
        argv += ["--tier", str(body.tier)]
    if body.no_jitter:
        argv.append("--no-jitter")
    label = f"Pipeline: {body.industry} x {body.count} ({body.send_mode})"
    return {"job_id": start_job(argv, label)}


@app.post("/api/actions/generate-and-push")
def generate_and_push(body: BatchFileBody):
    """Run generate_drafts then write_to_outlook in one job (skip manual step 3)."""
    path = resolve_batch(body.file)
    argv = [PY, str(SCRIPTS / "generate_and_push.py"), "--file", path]
    if body.limit:
        argv += ["--limit", str(body.limit)]
    return {"job_id": start_job(argv, f"Generate+push: {body.file}")}


class SendBody(BaseModel):
    file: str
    count: int
    no_jitter: bool = False


@app.post("/api/actions/send-drafts")
def send_drafts(body: SendBody):
    path = resolve_batch(body.file)
    argv = [PY, str(SCRIPTS / "send_drafts.py"), "--file", path, "--count", str(body.count)]
    if body.no_jitter:
        argv.append("--no-jitter")
    return {"job_id": start_job(argv, f"Send {body.count} from {body.file}")}


class FollowupBody(BaseModel):
    touch: int  # 2 or 3
    days: int   # e.g. 4 or 8
    count: int = 20


@app.post("/api/actions/queue-followups")
def queue_followups(body: FollowupBody):
    if body.touch not in (2, 3):
        raise HTTPException(400, "touch must be 2 or 3")
    argv = [PY, str(SCRIPTS / "queue_followups.py"),
            "--touch", str(body.touch),
            "--days", str(body.days),
            "--count", str(body.count)]
    return {"job_id": start_job(argv, f"Queue touch-{body.touch} follow-ups (Day-{body.days})")}


@app.get("/api/pending-drafts")
def pending_drafts():
    """Drafts in Outlook that haven't been sent yet (DB view)."""
    rows = q_all("""
        SELECT e.id, e.lead_id, e.subject, l.name, l.company, l.email,
               l.industry, l.city, e.batch_date
        FROM emails_sent e
        JOIN leads l ON e.lead_id = l.lead_id
        WHERE e.sent_at IS NULL AND e.outlook_entry_id IS NOT NULL
        ORDER BY e.id DESC
    """)
    return {"count": len(rows), "items": rows}


class SendAllDraftsBody(BaseModel):
    mode: str = "schedule"  # "now" | "schedule"
    no_jitter: bool = False


@app.post("/api/actions/send-all-drafts")
def send_all_drafts(body: SendAllDraftsBody):
    """Send every pending draft in Outlook, regardless of source batch file.

    Uses DB as source of truth (send_pending.py) so drafts from any batch
    are covered. Schedule mode still gates via send_scheduler, which then
    delegates to send_pending with no --file.
    """
    total_pending = q_one(
        "SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NULL AND outlook_entry_id IS NOT NULL"
    )
    if not total_pending:
        raise HTTPException(400, "No pending drafts to send")
    if body.mode == "now":
        argv = [PY, str(SCRIPTS / "send_pending.py")]
        if body.no_jitter:
            argv.append("--no-jitter")
        label = f"Send all {total_pending} pending drafts (now)"
    else:
        argv = [PY, str(SCRIPTS / "send_scheduler.py"), "--wait-and-send-pending"]
        if body.no_jitter:
            argv.append("--no-jitter")
        label = f"Send all {total_pending} pending drafts (scheduled)"
    return {"job_id": start_job(argv, label), "count": total_pending}


@app.post("/api/actions/clear-drafts")
def clear_drafts():
    """Delete ONLY pipeline-owned pending drafts from Outlook + reset DB state.

    Previously this iterated the entire Drafts folder and deleted everything,
    which wiped unrelated personal/manual drafts in the same account. Now we
    look up each outlook_entry_id recorded by write_to_outlook.py and delete
    only those items. Anything not tracked by our DB stays put.
    """
    import win32com.client
    outlook = win32com.client.Dispatch("Outlook.Application")
    acc = None
    for a in outlook.Session.Accounts:
        if a.SmtpAddress.lower() == "pradip@bitcodingsolutions.com":
            acc = a; break
    if not acc:
        raise HTTPException(500, "pradip@ account not found in Outlook")

    # Collect entry IDs of pipeline drafts still pending
    pending = q_all(
        "SELECT id, lead_id, outlook_entry_id FROM emails_sent "
        "WHERE sent_at IS NULL AND outlook_entry_id IS NOT NULL AND outlook_entry_id != ''"
    )
    ns = outlook.GetNamespace("MAPI")

    deleted = 0
    missing = 0
    for row in pending:
        eid = row["outlook_entry_id"]
        try:
            item = ns.GetItemFromID(eid)
            # Extra safety: only delete if it's still an unsent MailItem draft
            if not getattr(item, "Sent", True):
                item.Delete()
                deleted += 1
            else:
                missing += 1  # already sent/moved — nothing to delete
        except Exception:
            missing += 1  # item no longer exists in Outlook (user may have deleted manually)

    c = conn()
    try:
        # Drop DB rows only for entry IDs we just handled, so we never orphan
        # rows that point at live drafts we chose not to touch.
        ids = [row["id"] for row in pending]
        if ids:
            ph = ",".join("?" * len(ids))
            reset_db = c.execute(
                f"DELETE FROM emails_sent WHERE id IN ({ph})", ids
            ).rowcount
        else:
            reset_db = 0
        # Reset lead_status only for leads that had a pending draft cleared
        lead_ids = [row["lead_id"] for row in pending]
        if lead_ids:
            ph = ",".join("?" * len(lead_ids))
            reset_status = c.execute(
                f"UPDATE lead_status SET status='New', touch_count=0, "
                f"first_sent_at=NULL, last_touch_date=NULL, "
                f"updated_at=CURRENT_TIMESTAMP "
                f"WHERE lead_id IN ({ph}) AND status IN "
                f"('Picked','Drafted','DraftedInOutlook')",
                lead_ids,
            ).rowcount
        else:
            reset_status = 0
        c.commit()
    finally:
        c.close()
    return {
        "deleted_outlook": deleted,
        "missing_in_outlook": missing,
        "reset_db_rows": reset_db,
        "reset_lead_status": reset_status,
    }


@app.get("/api/schedule")
def schedule_status():
    """Return current send-window status for Germany business hours."""
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Europe/Berlin')
    t = dt.datetime.now(TZ)
    allowed = {1, 2, 3}  # Tue/Wed/Thu
    in_window = (t.weekday() in allowed
                 and ((t.hour > 10) or (t.hour == 10 and t.minute >= 0))
                 and ((t.hour < 11) or (t.hour == 11 and t.minute < 30)))
    if in_window:
        end = t.replace(hour=11, minute=30, second=0, microsecond=0)
        return {
            "in_window": True,
            "now_local": t.isoformat(timespec='seconds'),
            "window_closes_at": end.isoformat(timespec='seconds'),
            "seconds_remaining": int((end - t).total_seconds()),
        }
    # next window
    cand = t.replace(hour=10, minute=0, second=0, microsecond=0)
    if t >= cand:
        cand += dt.timedelta(days=1)
    while cand.weekday() not in allowed:
        cand += dt.timedelta(days=1)
    return {
        "in_window": False,
        "now_local": t.isoformat(timespec='seconds'),
        "next_window_opens_at": cand.isoformat(timespec='seconds'),
        "seconds_until_open": int((cand - t).total_seconds()),
    }


class ScheduledSendBody(BaseModel):
    file: str
    count: int
    no_jitter: bool = False
    wait: bool = False  # if True, script blocks until window opens


@app.post("/api/actions/scheduled-send")
def scheduled_send(body: ScheduledSendBody):
    path = resolve_batch(body.file)
    flag = "--wait-and-send" if body.wait else "--send-if-window"
    argv = [PY, str(SCRIPTS / "send_scheduler.py"), flag,
            "--file", path, "--count", str(body.count)]
    if body.no_jitter:
        argv.append("--no-jitter")
    label = f"{'Wait+send' if body.wait else 'Send-if-window'} {body.count} from {body.file}"
    return {"job_id": start_job(argv, label)}


@app.post("/api/actions/sync-sent")
def sync_sent():
    argv = [PY, str(SCRIPTS / "mark_sent.py")]
    return {"job_id": start_job(argv, "Sync Outlook Sent folder")}


@app.post("/api/actions/scan-replies")
def scan_replies():
    argv = [PY, str(SCRIPTS / "scan_replies.py")]
    return {"job_id": start_job(argv, "Scan Outlook inbox for replies")}


@app.get("/api/batches/files")
def batch_files():
    if not BATCHES_DIR.exists():
        return []
    out = []
    for f in sorted(BATCHES_DIR.glob("*.xlsx"), reverse=True):
        out.append({
            "name": f.name,
            "size_kb": round(f.stat().st_size / 1024),
            "modified": dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return out


@app.get("/api/batches/progress")
def batch_progress(file: str):
    """Per-batch counts from the xlsx + DB (DB is the source of truth)."""
    path = resolve_batch(file)
    import pandas as pd
    df = pd.read_excel(path)
    total = len(df)
    lead_ids = df['lead_id'].dropna().astype(str).tolist()
    if not lead_ids:
        return {"file": file, "total": total, "drafted": 0, "in_outlook": 0, "sent": 0,
                "pending_draft": total, "pending_outlook": 0, "pending_send": 0}
    placeholders = ",".join(["?"] * len(lead_ids))
    c = conn()
    try:
        drafted = c.execute(
            f"SELECT COUNT(DISTINCT lead_id) FROM emails_sent WHERE lead_id IN ({placeholders})",
            lead_ids,
        ).fetchone()[0]
        in_outlook = c.execute(
            f"SELECT COUNT(DISTINCT lead_id) FROM emails_sent "
            f"WHERE lead_id IN ({placeholders}) AND outlook_entry_id IS NOT NULL",
            lead_ids,
        ).fetchone()[0]
        sent = c.execute(
            f"SELECT COUNT(DISTINCT lead_id) FROM emails_sent "
            f"WHERE lead_id IN ({placeholders}) AND sent_at IS NOT NULL",
            lead_ids,
        ).fetchone()[0]
    finally:
        c.close()
    return {
        "file": file,
        "total": total,
        "drafted": drafted,
        "in_outlook": in_outlook,
        "sent": sent,
        "pending_draft": total - drafted,
        "pending_outlook": drafted - in_outlook,
        "pending_send": in_outlook - sent,
    }


# ---- Job status ----
@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404)
    # Drop non-serialisable internals (subprocess handle). Cap logs.
    out = {k: v for k, v in j.items() if k not in ("proc",)}
    out["logs"] = j.get("logs", [])[-200:]
    return out


@app.get("/api/jobs")
def jobs():
    return sorted(JOBS.values(), key=lambda j: j.get("started_at", ""), reverse=True)[:30]


# ---- Reply actions ----
class HandleReplyBody(BaseModel):
    reply_id: int
    handled: bool = True


@app.post("/api/replies/handle")
def handle_reply(body: HandleReplyBody):
    c = conn()
    c.execute(
        "UPDATE replies SET handled=?, handled_at=CURRENT_TIMESTAMP WHERE id=?",
        (1 if body.handled else 0, body.reply_id),
    )
    c.commit()
    c.close()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "db": DB,
        "time": dt.datetime.now().isoformat(),
        "auth_required": REQUIRE_AUTH,
    }


@app.get("/api/_bootstrap")
def bootstrap(request: Request):
    """Return the dashboard API key — but ONLY to loopback callers.

    This is the "one-time setup" hop so the frontend can fetch its own
    token at first boot without the user copy-pasting a string. We gate
    it on loopback IP so a tunnel or a LAN peer can never read it even
    if they reach the endpoint. Anyone with loopback access already has
    filesystem access (the key lives at BASE/.api_key), so no new
    exposure surface.
    """
    ip = request.client.host if request.client else ""
    if ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Bootstrap is loopback-only.")
    return {"api_key": API_KEY, "auth_required": REQUIRE_AUTH}


BRIDGE_DIR = Path(r"H:/Lead Generator/Bridge")


def _ping_bridge(timeout: float = 1.5) -> bool:
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:8765/", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500  # any response means server is up
    except Exception:
        return False


@app.get("/api/bridge-health")
def bridge_health():
    """Ping the local Claude bridge (localhost:8765). Used for header indicator."""
    return {"ok": _ping_bridge()}


@app.post("/api/actions/start-bridge")
def start_bridge():
    """Launch the bridge in background via start-silent.vbs, then poll health."""
    import time
    if _ping_bridge():
        return {"started": False, "already_running": True, "ok": True}
    vbs = BRIDGE_DIR / "start-silent.vbs"
    if not vbs.exists():
        raise HTTPException(500, f"Bridge launcher not found: {vbs}")
    try:
        subprocess.Popen(
            ["wscript.exe", str(vbs)],
            cwd=str(BRIDGE_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to launch bridge: {e}")
    # Poll up to ~6s for the server to bind
    for _ in range(12):
        time.sleep(0.5)
        if _ping_bridge(timeout=1.0):
            return {"started": True, "already_running": False, "ok": True}
    return {"started": True, "already_running": False, "ok": False,
            "hint": "Launched but not responding yet; check Bridge/bridge.log"}
