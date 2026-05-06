"""Reply-handling request bodies."""
from __future__ import annotations

from pydantic import BaseModel


class HandleReplyBody(BaseModel):
    reply_id: int
    handled: bool = True
