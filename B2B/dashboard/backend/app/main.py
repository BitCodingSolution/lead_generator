"""FastAPI application factory + middleware + lifespan.

Run:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import collections
import threading
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth.router import router as auth_router
from app.config import settings
from app.deps import authenticate_request
from app.marcel.routers.actions import router as actions_router
from app.marcel.routers.batches import router as batches_router
from app.marcel.routers.bridge import router as bridge_router
from app.marcel.routers.jobs import router as jobs_router
from app.marcel.routers.leads import router as leads_router
from app.marcel.routers.overview import router as overview_router
from app.marcel.routers.replies import router as replies_router
from app.marcel.routers.source_actions import router as source_actions_router
from app.marcel.routers.sources import router as sources_router
from app.marcel.services.sources import Source, register_source

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="BitCoding B2B Outreach API",
    version="2.0",
    docs_url="/docs" if settings.dashboard_docs else None,
    redoc_url="/redoc" if settings.dashboard_docs else None,
    openapi_url="/openapi.json" if settings.dashboard_docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

register_source(Source(
    id="marcel",
    label="Marcel Data",
    type="outreach",
    icon="Mail",
    description="Primary outreach dataset — already in the B2B pipeline.",
))

register_source(Source(
    id="ycombinator",
    label="Y Combinator",
    type="grab",
    leads_table="yc_leads",
    founders_table="yc_founders",
    exported_table="yc_exported_leads",
    schema_path=settings.grab_root / "sources" / "ycombinator" / "schema.json",
    icon="Rocket",
    description="YC portfolio companies — funded, US-heavy, actively hiring.",
))


# ---------------------------------------------------------------------------
# Per-IP token-bucket rate limiter
# ---------------------------------------------------------------------------

_RATE_BUCKETS: dict[str, collections.deque] = {}
_RATE_LOCK = threading.Lock()


def _rate_limit_check(ip: str) -> tuple[bool, int]:
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.setdefault(ip, collections.deque())
        while bucket and now - bucket[0] > 60.0:
            bucket.popleft()
        if len(bucket) >= settings.dashboard_rate_limit:
            retry_after = max(1, int(60 - (now - bucket[0])))
            return False, retry_after
        bucket.append(now)
        return True, 0


# ---------------------------------------------------------------------------
# Auth gate (middleware).
#
# All routes require a Bearer JWT issued by /api/auth/login, EXCEPT:
#   - PUBLIC_PATHS: health/docs/login itself.
#   - EXT_KEY_PATHS: LinkedIn extension ingest, which enforces its own
#     X-Ext-Key header inside the handler.
# ---------------------------------------------------------------------------

PUBLIC_PATHS: set[str] = {
    "/",
    "/docs", "/redoc", "/openapi.json",
    "/api/health",
    "/api/auth/login",
    "/api/bridge-health",
}

EXT_KEY_PATHS: set[str] = {
    "/api/linkedin/ingest",
    "/api/linkedin/account-warning",
}

# URL prefixes that bypass auth — the static mount lives here so PDF
# links open directly in the browser without bearer-token plumbing.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/static/",
)


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS or path in EXT_KEY_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def _attach_cors_headers(request: Request, response: JSONResponse) -> JSONResponse:
    """Add CORS headers manually on middleware short-circuits — otherwise
    the browser sees a generic CORS error instead of our JSON detail."""
    origin = request.headers.get("origin")
    if origin and origin in settings.allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        existing_vary = response.headers.get("Vary")
        response.headers["Vary"] = f"{existing_vary}, Origin" if existing_vary else "Origin"
    return response


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    ok, retry_after = _rate_limit_check(client_ip)
    if not ok:
        return _attach_cors_headers(request, JSONResponse(
            {"detail": f"Rate limit exceeded ({settings.dashboard_rate_limit}/min). Retry in {retry_after}s."},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        ))

    if _is_public(request.url.path):
        return await call_next(request)

    user = authenticate_request(request.headers.get("authorization"))
    if user is not None:
        request.state.user = user
        return await call_next(request)

    return _attach_cors_headers(request, JSONResponse(
        {"detail": "Authentication required."},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    ))


# ---------------------------------------------------------------------------
# Verbose 422 logging for debugging client payloads
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Router includes
# ---------------------------------------------------------------------------

app.include_router(auth_router)
app.include_router(overview_router)
app.include_router(leads_router)
app.include_router(replies_router)
app.include_router(sources_router)
app.include_router(source_actions_router)
app.include_router(batches_router)
app.include_router(actions_router)
app.include_router(jobs_router)
app.include_router(bridge_router)


# ---------------------------------------------------------------------------
# LinkedIn (kept as separate top-level routers — full refactor later)
# ---------------------------------------------------------------------------

from app.linkedin.extras import reset_orphans as _reset_orphans  # noqa: E402
from app.linkedin.routers.overview import router as linkedin_overview_router  # noqa: E402
from app.linkedin.routers.leads import router as linkedin_leads_router  # noqa: E402
from app.linkedin.routers.extension import router as linkedin_extension_router  # noqa: E402
from app.linkedin.routers.drafts import router as linkedin_drafts_router  # noqa: E402
from app.linkedin.routers.gmail import router as linkedin_gmail_router  # noqa: E402
from app.linkedin.routers.replies import router as linkedin_replies_router  # noqa: E402
from app.linkedin.routers.send import router as linkedin_send_router  # noqa: E402
from app.linkedin.routers.maintenance import router as linkedin_maintenance_router  # noqa: E402
from app.linkedin.routers.blocklist import router as linkedin_blocklist_router  # noqa: E402
from app.linkedin.routers.cvs import router as linkedin_cvs_router  # noqa: E402
from app.linkedin.routers.followups import router as linkedin_followups_router  # noqa: E402
from app.linkedin.routers.analytics import router as linkedin_analytics_router  # noqa: E402

app.include_router(linkedin_overview_router)
app.include_router(linkedin_leads_router)
app.include_router(linkedin_extension_router)
app.include_router(linkedin_drafts_router)
app.include_router(linkedin_gmail_router)
app.include_router(linkedin_replies_router)
app.include_router(linkedin_send_router)
app.include_router(linkedin_maintenance_router)
app.include_router(linkedin_blocklist_router)
app.include_router(linkedin_cvs_router)
app.include_router(linkedin_followups_router)
app.include_router(linkedin_analytics_router)

# Static assets — currently the LinkedIn CV PDFs under app/static/cvs/.
# Served at /static/* (e.g. /static/cvs/fullstack__Jaydip_CV_Full_Stack.pdf).
from pathlib import Path as _Path  # noqa: E402

_STATIC_DIR = _Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Startup hooks
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _start_scheduler() -> None:
    from app.marcel.services.schedules import start_scheduler_thread
    start_scheduler_thread()


@app.on_event("startup")
def _linkedin_startup_cleanup() -> None:
    """Revert any leads stuck mid-send + reconcile Gmail per-account counters."""
    try:
        _reset_orphans()
    except Exception as e:
        print(f"[linkedin] startup orphan reset failed: {e}")
    try:
        from app.linkedin.services.gmail import reconcile_today_counts
        info = reconcile_today_counts()
        print(f"[linkedin] gmail account counters reconciled: {info}")
    except Exception as e:
        print(f"[linkedin] account counter reconcile failed: {e}")


@app.on_event("startup")
def _start_linkedin_poll() -> None:
    def _loop():
        from app.linkedin.api import (
            _autopilot_tick,
            _digest_tick,
            _followups_tick,
            _poll_and_store,
            _scheduler_tick,
            _stale_drafts_sweep,
        )
        from app.linkedin.services.gmail import get_credentials as _gmail_creds
        tick = 0
        while True:
            try:
                _autopilot_tick()
                _scheduler_tick()
                if tick % 5 == 0 and _gmail_creds() is not None:
                    _poll_and_store()
                if tick % 60 == 0:
                    _stale_drafts_sweep()
                _digest_tick()
                _followups_tick()
            except Exception as e:
                print(f"[linkedin-poll] {e}")
            tick += 1
            time.sleep(60)

    threading.Thread(target=_loop, daemon=True).start()
