"""Shared response shapes."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class JobStartResponse(BaseModel):
    job_id: str
    argv: list[str] | None = None
    steps: list[str] | None = None
    count: int | None = None
    remaining_before: int | None = None


class OkResponse(BaseModel):
    ok: bool = True
    detail: str | None = None
    extra: dict[str, Any] | None = None
