"""Compatibility shim — the multi-source registry + endpoints have moved.

Registry lives at `app.services.sources`; HTTP routes live at
`app.routers.sources`. New code should import from those modules.
"""
from app.routers.sources import router  # noqa: F401
from app.services.sources import (  # noqa: F401
    Source,
    _SOURCES,
    all_sources,
    get_source,
    register_source,
)
