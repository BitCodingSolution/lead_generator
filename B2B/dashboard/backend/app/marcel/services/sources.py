"""Source registry — supersedes the legacy `sources_api.py` registry.

Holds the in-memory list of registered data sources (Marcel + grab
sources). Routers read from `_SOURCES`; bootstrap code populates it
during app startup. Schema loading remains delegated to each Source's
`schema.json` so per-source columns/filters stay declarative.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import HTTPException


@dataclass
class Source:
    id: str
    label: str
    # All sources now live in Postgres. Grab-type sources declare which
    # tables hold their leads / founders / export-tracking rows so the
    # generic multi-source router can dispatch by Source instead of
    # hardcoding table names.
    leads_table: Optional[str] = None        # e.g. "yc_leads"
    founders_table: Optional[str] = None     # e.g. "yc_founders"
    exported_table: Optional[str] = None     # e.g. "yc_exported_leads"
    # `db_path` is kept for backward compat with any external caller
    # that might still construct a Source manually; nothing in the
    # backend reads it any more.
    db_path: Optional[Path] = None
    type: str = "grab"           # 'grab' | 'outreach'
    schema_path: Optional[Path] = None
    icon: str = "Database"
    description: str = ""
    extra: dict = field(default_factory=dict)

    def load_schema(self) -> dict:
        if self.schema_path and self.schema_path.exists():
            return json.loads(self.schema_path.read_text(encoding="utf-8"))
        return {
            "source": self.id,
            "type": self.type,
            "display": {
                "icon": self.icon,
                "label": self.label,
                "description": self.description,
                "table_columns": [],
                "filters": [],
            },
        }


_SOURCES: dict[str, Source] = {}


def register_source(s: Source) -> None:
    _SOURCES[s.id] = s


def get_source(sid: str) -> Source:
    if sid not in _SOURCES:
        raise HTTPException(status_code=404, detail=f"Source '{sid}' not registered")
    return _SOURCES[sid]


def all_sources() -> dict[str, Source]:
    return _SOURCES
