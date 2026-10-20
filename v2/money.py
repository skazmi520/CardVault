"""Strict money parsing, shared by the web forms and the CSV importer."""

import re

_MONEY_RE = re.compile(r"^-?\$?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?$")


def parse_money(v, field: str, *, allow_negative: bool = False):
    """A money value exactly as typed, or None when left blank. Anything that is
    not plainly a number ("3.5k", "1 200", "35O0", inf, nan) is refused rather
    than truncated: a value the user typed is either stored as typed or rejected
    so he can see it."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    if isinstance(v, bool):
        raise ValueError(f"{field} must be a number")
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        t = str(v).strip()
        if not _MONEY_RE.match(t) or not any(ch.isdigit() for ch in t):
            raise ValueError(f"{field}: {v!r} is not a plain amount")
        x = float(t.replace("$", "").replace(",", ""))
    if x != x or x in (float("inf"), float("-inf")) or abs(x) > 1e9:
        raise ValueError(f"{field} must be a real amount")
    if x < 0 and not allow_negative:
        raise ValueError(f"{field} cannot be negative")
    return round(x, 2)
