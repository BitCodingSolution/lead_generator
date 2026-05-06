"""Local Claude bridge probe + launcher."""
from __future__ import annotations

from fastapi import APIRouter

from app.marcel.services.bridge import ping_bridge, start_bridge_async

router = APIRouter(prefix="/api", tags=["bridge"])


@router.get("/bridge-health")
def bridge_health() -> dict:
    return {"ok": ping_bridge()}


@router.post("/actions/start-bridge")
def start_bridge() -> dict:
    return start_bridge_async()
