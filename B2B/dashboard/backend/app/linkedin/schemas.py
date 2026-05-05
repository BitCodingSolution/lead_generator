"""Pydantic request/response schemas for the LinkedIn module.

Extracted verbatim from `app.linkedin.api` and `app.linkedin.extras` so the
routers/services don't carry the schema definitions inline. Every class
below is byte-for-byte the same as it was in its original file.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ===========================================================================
# Schemas extracted from app.linkedin.api
# ===========================================================================


class LinkedInLead(BaseModel):
    id: int
    post_url: str
    posted_by: Optional[str]
    company: Optional[str]
    role: Optional[str]
    tech_stack: Optional[str]
    location: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    status: str
    call_status: Optional[str] = None
    reviewed_at: Optional[str] = None
    jaydip_note: Optional[str] = None
    open_count: int = 0
    first_opened_at: Optional[str] = None
    last_opened_at: Optional[str] = None
    scheduled_send_at: Optional[str] = None
    ooo_nudge_at: Optional[str] = None
    ooo_nudge_sent_at: Optional[str] = None
    fit_score: Optional[int] = None
    fit_score_reasons: Optional[str] = None
    gen_subject: Optional[str]
    cv_cluster: Optional[str]
    first_seen_at: str
    last_seen_at: str
    sent_at: Optional[str]
    replied_at: Optional[str]
    needs_attention: int


class AutoPausedAccount(BaseModel):
    id: int
    email: str
    reason: str


class OverviewResponse(BaseModel):
    total: int
    new: int
    drafted: int
    queued: int
    sent_today: int
    replied: int
    # Replies still awaiting Jaydip's action (handled = 0). Drives the
    # "X pending" sub-line on the Replied KPI so a glance at the dashboard
    # tells him whether any conversations need triage.
    replied_pending: int = 0
    bounced: int
    quota_used: int
    quota_cap: int
    gmail_connected: bool
    autopilot_enabled: bool
    safety_mode: str
    warning_paused_until: Optional[str]
    auto_paused_accounts: list[AutoPausedAccount] = []


class AutopilotTodayRun(BaseModel):
    fired_at: str
    total_queued: int
    status: str


class SafetyState(BaseModel):
    daily_sent_count: int
    daily_sent_date: Optional[str]
    last_send_at: Optional[str]
    consecutive_failures: int
    warning_paused_until: Optional[str]
    autopilot_enabled: bool
    autopilot_hour: int
    autopilot_minute: int
    # None = send the full effective daily cap; int = cap at this many.
    autopilot_count: Optional[int]
    autopilot_tz: str
    business_hours_only: bool
    safety_mode: str
    # Auto follow-up sequencer: when on, _followups_tick fires
    # run_followups() once a day at followups_hour local. Falls back to
    # the cadence in linkedin_extras.FOLLOWUP_DAYS (default 3, 7).
    followups_autopilot: bool = False
    followups_hour: int = 11
    # Populated when autopilot has already fired (or been skipped) today.
    # UI uses this to show a "Already ran at HH:MM" state + expose a manual
    # reset button so the user can re-fire for the same day.
    autopilot_today: Optional[AutopilotTodayRun] = None


class SafetyPatch(BaseModel):
    safety_mode: Optional[str] = None     # max | normal
    autopilot_enabled: Optional[bool] = None
    autopilot_hour: Optional[int] = Field(default=None, ge=0, le=23)
    autopilot_minute: Optional[int] = Field(default=None, ge=0, le=59)
    # 0 or null from the wire means "full cap"; otherwise cap at N.
    # Using -1 as the explicit "revert to full" sentinel so the client can
    # toggle between "limited" and "full" without ambiguity.
    autopilot_count: Optional[int] = Field(default=None, ge=-1, le=500)
    autopilot_tz: Optional[str] = Field(default=None, max_length=64)
    business_hours_only: Optional[bool] = None
    clear_warning_pause: Optional[bool] = None
    followups_autopilot: Optional[bool] = None
    followups_hour: Optional[int] = Field(default=None, ge=0, le=23)


class RuntimeSettingUpdate(BaseModel):
    key: str
    value: object  # bool | int | str — coerced per descriptor


class ExtensionKeyIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class IngestPost(BaseModel):
    # Accept any non-empty post_url OR an empty string (will be rejected
    # with a cleaner error downstream instead of a 422 validation blob).
    # Extra unknown fields are ignored — future extension versions can add
    # richer payloads without breaking the contract.
    model_config = {"extra": "ignore"}

    post_url: str = Field(default="")
    posted_by: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    tech_stack: Optional[str] = None
    rate: Optional[str] = None
    location: Optional[str] = None
    tags: Optional[str] = None
    post_text: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    # Optional extension-generated draft fields. If the Chrome extension ran
    # Claude locally (Auto-generate email on save), we accept its output so
    # the user doesn't have to regenerate on the dashboard. Absent means the
    # lead arrives as 'New' and needs a server-side Generate click.
    gen_subject: Optional[str] = None
    gen_body: Optional[str] = None
    email_mode: Optional[str] = None      # individual | company
    cv_cluster: Optional[str] = None      # python | ml | ai_llm | fullstack | scraping | n8n | default
    # Extension per-row quick-tag: stream the scanner's 🟢/🟡/🔴 pick
    # straight into the dashboard so the user doesn't have to re-tag
    # after save.
    call_status: Optional[str] = None     # green | yellow | red | ""
    should_skip: Optional[bool] = None
    skip_reason: Optional[str] = None

    @field_validator("tags", "tech_stack", mode="before")
    @classmethod
    def _join_list(cls, v):
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x is not None and str(x).strip())
        return v

    @field_validator("should_skip", mode="before")
    @classmethod
    def _coerce_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "y"}
        return v


class IngestBatch(BaseModel):
    leads: list[IngestPost]


class AccountWarning(BaseModel):
    phrase: str
    url: Optional[str] = None


class LeadPatch(BaseModel):
    gen_subject: Optional[str] = None
    gen_body: Optional[str] = None
    jaydip_note: Optional[str] = None
    email_mode: Optional[str] = None
    needs_attention: Optional[bool] = None
    call_status: Optional[str] = None    # green | yellow | red | "" (clears)
    # Inline email correction: drafter / scraper sometimes captures a
    # malformed address (e.g. "abhishek@jigya..com"). Letting Jaydip fix
    # it inline avoids re-running the whole pipeline.
    email: Optional[str] = None
    phone: Optional[str] = None


class ArchiveRequest(BaseModel):
    reason: str = Field(default="manual", max_length=40)


class BulkLeadIdsBody(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    reason: Optional[str] = None


class BulkSnoozeBody(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    remind_at: str = Field(min_length=2, max_length=40)


class DraftBatchIn(BaseModel):
    max: int = Field(default=100, ge=1, le=500)


class GmailConnectIn(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    app_password: str = Field(min_length=10, max_length=32)
    display_name: Optional[str] = None
    daily_cap: Optional[int] = None


class GmailCapIn(BaseModel):
    daily_cap: int = Field(ge=1, le=500)


class GmailWarmupIn(BaseModel):
    enabled: bool
    reset_start: bool = False


class WarmupCurveIn(BaseModel):
    # Each stage: send up to `cap` per day until day `days` (exclusive).
    # List must cover the ramp — the last stage's cap applies until daily_cap
    # caps in. Example: [[1,5],[3,10],[7,20],[14,35]] = 5/day on day 0,
    # 10/day days 1-2, 20/day days 3-6, 35/day days 7-13, full cap day 14+.
    stages: list[list[int]] = Field(min_length=1, max_length=10)


class DraftReplyBody(BaseModel):
    # User-typed direction for this specific reply. Claude blends the
    # instruction into the tone/content instead of ignoring it. Optional —
    # an empty value falls back to the generic drafter.
    hint: Optional[str] = Field(default=None, max_length=1000)


class SendReplyBody(BaseModel):
    body: str = Field(min_length=5, max_length=20_000)
    subject: Optional[str] = None  # defaults to "Re: <original subject>"


class MarkHandledBody(BaseModel):
    handled: bool = True


class BulkHandleBody(BaseModel):
    reply_ids: list[int] = Field(min_length=1, max_length=500)
    handled: bool = True


class ScheduleBody(BaseModel):
    scheduled_send_at: str = Field(min_length=10, max_length=40)


class SnoozeBody(BaseModel):
    # ISO timestamp OR a relative hint like "1d" / "3d" / "1w"
    remind_at: str = Field(min_length=2, max_length=40)


class BatchSendIn(BaseModel):
    # Upper bound is generous — real daily quota is enforced dynamically from
    # the sum of active Gmail account caps in _check_safety_before_send.
    count: int = Field(default=5, ge=1, le=500)
    source: str = Field(default="manual")   # manual | autopilot



# ===========================================================================
# Schemas extracted from app.linkedin.extras
# ===========================================================================


class BlocklistIn(BaseModel):
    kind: str = Field(pattern="^(company|domain|email)$")
    value: str = Field(min_length=2, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=200)


class BlocklistBulkIn(BaseModel):
    # Paste a newline- or comma-separated list of emails (or domains).
    # kind is auto-inferred per entry: contains '@' -> email; else domain.
    text: str = Field(min_length=1, max_length=50_000)
    reason: Optional[str] = Field(default=None, max_length=200)


class CVMeta(BaseModel):
    id: int
    cluster: str
    filename: str
    size_bytes: Optional[int]
    uploaded_at: str


class FollowupRunIn(BaseModel):
    lead_ids: Optional[list[int]] = None        # empty → run all due
    dry_run: bool = False


