"""Local Claude bridge probe + launcher."""
from __future__ import annotations

import json as _json
import subprocess
from urllib.parse import urlparse
import urllib.request

from fastapi import HTTPException

from app.config import settings


def ping_bridge(timeout: float = 1.5) -> bool:
    """Detect whether OUR bridge is reachable. Hits /health and requires
    the service-name signature in the body so a port-squatter can't
    masquerade as the bridge."""
    parsed = urlparse(settings.bridge_url)
    target = f"{parsed.scheme}://{parsed.netloc}/health"
    try:
        req = urllib.request.Request(target, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return False
            payload = _json.loads(r.read().decode("utf-8", errors="replace"))
            return (payload.get("service") or "").startswith("LinkedIn Smart Search")
    except Exception:
        return False


def start_bridge_async() -> dict:
    """Launch the bridge via start-silent.vbs, then poll for readiness."""
    import time
    if ping_bridge():
        return {"started": False, "already_running": True, "ok": True}
    vbs = settings.bridge_dir / "start-silent.vbs"
    if not vbs.exists():
        raise HTTPException(500, f"Bridge launcher not found: {vbs}")
    try:
        subprocess.Popen(
            ["wscript.exe", str(vbs)],
            cwd=str(settings.bridge_dir),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to launch bridge: {e}")
    for _ in range(12):
        time.sleep(0.5)
        if ping_bridge(timeout=1.0):
            return {"started": True, "already_running": False, "ok": True}
    return {
        "started": True, "already_running": False, "ok": False,
        "hint": "Launched but not responding yet; check Bridge/bridge.log",
    }
