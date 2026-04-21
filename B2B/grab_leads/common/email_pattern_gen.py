"""
Email pattern generator.

Given first name, last name, and domain, produce ordered list of likely
email addresses. Order reflects empirical frequency across US SMB/startups
(most common first).

No external deps. No network calls.
"""
from __future__ import annotations

import re
import unicodedata


def _slug(s: str) -> str:
    """Lowercase, strip accents, drop non-alphanumeric."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    return re.sub(r"[^a-z0-9]", "", s)


def generate(first: str, last: str, domain: str) -> list[str]:
    """Return ordered candidate emails. Empty list if insufficient input."""
    f = _slug(first)
    l = _slug(last)
    d = (domain or "").lower().strip().removeprefix("www.")
    if not f or not d:
        return []

    patterns: list[str] = []
    if l:
        patterns += [
            f"{f}@{d}",                 # spenser@
            f"{f}.{l}@{d}",             # spenser.skates@
            f"{f}{l}@{d}",              # spenserskates@
            f"{f[0]}{l}@{d}",           # sskates@
            f"{f}{l[0]}@{d}",           # spensers@
            f"{f}_{l}@{d}",             # spenser_skates@
            f"{f}-{l}@{d}",             # spenser-skates@
            f"{l}.{f}@{d}",             # skates.spenser@
            f"{l}{f}@{d}",              # skatesspenser@
            f"{l}@{d}",                 # skates@
            f"{f[0]}.{l}@{d}",          # s.skates@
        ]
    else:
        patterns += [f"{f}@{d}"]

    # Dedup while preserving order
    seen = set()
    out = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out
