"""
LinkedIn Smart Search — local Claude Code bridge.

This tiny FastAPI server lets the Chrome extension use your Claude.ai Max
subscription (via Claude Code CLI OAuth) instead of paying for Anthropic
API credits.

Setup:
    1. Install Claude Code CLI:
           irm https://claude.ai/install.ps1 | iex        (Windows PowerShell)
           curl -fsSL https://claude.ai/install.sh | sh    (macOS/Linux)
    2. Log in with your claude.ai account:
           claude /login
    3. Install Python deps (run once):
           pip install -r requirements.txt
    4. Start this server:
           python server.py
    5. In the extension → Settings → Claude backend → pick "Local bridge".
"""

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from claude_agent_sdk import query, ClaudeAgentOptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bridge")

app = FastAPI(title="LinkedIn Smart Search Bridge", version="0.1.0")

# Chrome extensions call from chrome-extension://<id>. Local-only service, * is fine.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


class ReplyRequest(BaseModel):
    system_prompt: str = Field(..., description="System role / instructions")
    user_message: str = Field(..., description="Conversation + ask")
    max_turns: int = 1


class ReplyResponse(BaseModel):
    reply: str
    cost_usd: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "LinkedIn Smart Search Bridge",
        "version": "0.1.0",
    }


@app.post("/generate-reply", response_model=ReplyResponse)
async def generate_reply(req: ReplyRequest):
    if not req.user_message.strip():
        raise HTTPException(400, "user_message is empty")

    log.info("generate-reply: system=%d chars, user=%d chars",
             len(req.system_prompt), len(req.user_message))

    options = ClaudeAgentOptions(
        system_prompt=req.system_prompt,
        max_turns=req.max_turns,
        # Using Opus 4.6 — most capable model. User has Max subscription,
        # so quota hit is acceptable for best reply quality.
        model="claude-opus-4-6",
        # We want a plain text reply, not tool use
        allowed_tools=[],
    )

    collected: list[str] = []
    cost_usd: Optional[float] = None
    in_tok: Optional[int] = None
    out_tok: Optional[int] = None

    try:
        async for msg in query(prompt=req.user_message, options=options):
            # Assistant message with text content blocks
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        collected.append(text)

            # Result message carries cost + usage
            if hasattr(msg, "total_cost_usd"):
                cost_usd = getattr(msg, "total_cost_usd", None)
            usage = getattr(msg, "usage", None)
            if usage:
                if isinstance(usage, dict):
                    in_tok = usage.get("input_tokens") or in_tok
                    out_tok = usage.get("output_tokens") or out_tok
                else:
                    in_tok = getattr(usage, "input_tokens", None) or in_tok
                    out_tok = getattr(usage, "output_tokens", None) or out_tok
    except Exception as e:
        log.exception("Claude Agent SDK error")
        raise HTTPException(
            500,
            f"Claude SDK error: {e}. "
            "Check that Claude Code is installed and `claude login` is done.",
        )

    reply = "".join(collected).strip()
    if not reply:
        raise HTTPException(
            502,
            "Claude returned an empty response. "
            "Run `claude login` once, then try again.",
        )

    log.info("reply: %d chars, cost=%s", len(reply), cost_usd)

    return ReplyResponse(
        reply=reply,
        cost_usd=cost_usd,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  LinkedIn Smart Search Bridge")
    print("=" * 60)
    print("  Health:         http://127.0.0.1:8766/health")
    print("  Generate reply: POST http://127.0.0.1:8766/generate-reply")
    print("")
    print("  Stop:           Ctrl+C")
    print("=" * 60)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8766,
        log_level="info",
    )
