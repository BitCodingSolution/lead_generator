"""Reply handling endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.db import conn
from app.schemas.replies import HandleReplyBody

router = APIRouter(prefix="/api/replies", tags=["replies"])


@router.post("/handle")
def handle_reply(body: HandleReplyBody) -> dict:
    c = conn()
    try:
        c.execute(
            "UPDATE replies SET handled=?, handled_at=CURRENT_TIMESTAMP WHERE id=?",
            (1 if body.handled else 0, body.reply_id),
        )
        c.commit()
    finally:
        c.close()
    return {"ok": True}
