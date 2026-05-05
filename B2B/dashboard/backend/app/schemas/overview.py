"""Overview / stats response shapes."""
from __future__ import annotations

from pydantic import BaseModel


class OverviewResponse(BaseModel):
    total_leads: int
    leads_by_source: dict[str, int]
    drafted: int
    total_sent: int
    sent_today: int
    total_replies: int
    hot_pending: int
    reply_rate_pct: float
    positive_rate_pct: float
    daily_quota: int
    remaining_today: int
    has_replies: bool


class FunnelStage(BaseModel):
    stage: str
    count: int


class DailyActivityRow(BaseModel):
    day: str
    sent: int
    replies: int


class HealthResponse(BaseModel):
    ok: bool
    db: str
    time: str
    auth_required: bool
    ms_auth_enabled: bool
