"""Runtime configuration.

Single source of truth for filesystem paths and runtime knobs. Reads
environment variables (or a local `.env` if python-dotenv is loaded).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo layout:
#   /B2B/                         <- BASE
#       dashboard/backend/app/    <- this file
#       Database/Marcel Data/...
#       grab_leads/...
#       scripts/...
_BASE_DEFAULT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All env-driven knobs. Names map 1:1 to environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Paths ----
    base_dir: Path = Field(default=_BASE_DEFAULT, alias="B2B_BASE_DIR")

    # ---- Quotas ----
    daily_quota: int = 25
    job_retention_seconds: int = 3600

    # ---- Auth ----
    # JWT signing secret. Auto-generated and persisted to <backend>/.jwt_secret
    # if left blank.
    dashboard_jwt_secret: str = Field(default="", alias="DASHBOARD_JWT_SECRET")

    # ---- CORS / rate limit / docs ----
    dashboard_extra_origins: str = Field(default="", alias="DASHBOARD_EXTRA_ORIGINS")
    dashboard_rate_limit: int = Field(default=120, alias="DASHBOARD_RATE_LIMIT")
    dashboard_docs: bool = Field(default=True, alias="DASHBOARD_DOCS")

    # ---- Outlook account used by Marcel pipeline ----
    outlook_account: str = Field(
        default="pradip@bitcodingsolutions.com",
        alias="OUTLOOK_ACCOUNT",
    )

    # ---- Bridge ----
    bridge_dir: Path = Field(default=Path(r"H:/Lead Generator/Bridge"), alias="BRIDGE_DIR")
    bridge_url: str = Field(default="http://127.0.0.1:8766", alias="BRIDGE_URL")

    # ---- Derived helpers (not env-driven) ----
    @property
    def db_path(self) -> Path:
        return self.base_dir / "Database" / "Marcel Data" / "leads.db"

    @property
    def scripts_dir(self) -> Path:
        return self.base_dir / "scripts"

    @property
    def batches_dir(self) -> Path:
        return self.base_dir / "Database" / "Marcel Data" / "01_Daily_Batches"

    @property
    def grab_root(self) -> Path:
        return self.base_dir / "grab_leads"

    @property
    def grab_batches_dir(self) -> Path:
        d = self.grab_root / "mailer" / "batches"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def schedules_file(self) -> Path:
        return Path(__file__).resolve().parent / "schedules.json"

    @property
    def python_executable(self) -> str:
        return sys.executable

    @property
    def allowed_origins(self) -> list[str]:
        base = [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ]
        if self.dashboard_extra_origins.strip():
            base.extend(
                o.strip().rstrip("/")
                for o in self.dashboard_extra_origins.split(",")
                if o.strip()
            )
        return base

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton so importing modules don't re-parse the env every call."""
    return Settings()


settings = get_settings()
