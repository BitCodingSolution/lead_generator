"""Source-related request/response models."""
from __future__ import annotations

from pydantic import BaseModel


class SourceActionReq(BaseModel):
    args: dict[str, object] | None = None


class CampaignReq(BaseModel):
    lead_ids: list[int]
    max: int | None = None
    industry_tag: str = "YC Portfolio"
    tier: int = 1
    group_by_company: bool = True


class ExportBatchReq(BaseModel):
    lead_ids: list[int] | None = None
    industry_tag: str = "YC Portfolio"
    tier: int = 1
    max: int = 100
    group_by_company: bool = True


class SendBatchReq(BaseModel):
    count: int = 10


class AutoRunReq(BaseModel):
    enabled: bool
    hour: int = 2
    minute: int = 0
