"""Marcel-pipeline action request bodies."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PickBody(BaseModel):
    industry: str
    count: int = 10
    tier: Optional[int] = None
    city: Optional[str] = None


class BatchFileBody(BaseModel):
    file: str  # filename only, resolved under BATCHES_DIR
    limit: Optional[int] = None


class RunPipelineBody(BaseModel):
    industry: str
    count: int
    tier: Optional[int] = None
    send_mode: str = Field(default="schedule", pattern="^(now|schedule|draft)$")
    no_jitter: bool = False


class SendBody(BaseModel):
    file: str
    count: int
    no_jitter: bool = False


class FollowupBody(BaseModel):
    touch: int   # 2 or 3
    days: int
    count: int = 20


class SendAllDraftsBody(BaseModel):
    mode: str = Field(default="schedule", pattern="^(now|schedule)$")
    no_jitter: bool = False


class ScheduledSendBody(BaseModel):
    file: str
    count: int
    no_jitter: bool = False
    wait: bool = False
