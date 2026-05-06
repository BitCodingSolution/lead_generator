"""Translate user-supplied dicts into CLI flags using a source's schema."""
from __future__ import annotations


def schema_flag_args(schema: dict, args: dict | None) -> list[str]:
    """Convert a dict of args into CLI flags using `option_args` descriptors,
    prepending `default_args` from the same schema section."""
    scraper = schema.get("scraper") or {}
    defaults = list(scraper.get("default_args") or [])
    opts = scraper.get("option_args") or []

    by_key: dict[str, dict] = {}
    for o in opts:
        flag = o.get("flag", "")
        key = flag.lstrip("-").replace("-", "_")
        by_key[key] = o

    chosen = list(defaults)
    for key, val in (args or {}).items():
        meta = by_key.get(key)
        if not meta:
            continue
        flag = meta["flag"]
        t = meta.get("type", "string")
        if t == "bool":
            if val and flag not in chosen:
                chosen.append(flag)
            elif not val and flag in chosen:
                chosen.remove(flag)
        elif t == "int":
            if val is not None and str(val).strip() != "":
                chosen += [flag, str(int(val))]
        else:
            if val:
                chosen += [flag, str(val)]
    return chosen
