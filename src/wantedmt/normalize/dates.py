"""Repairing dates where the reading is unambiguous, nulling them where it is not."""

from __future__ import annotations

import re
from datetime import date

ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})([T ].*)?$")


MIN_YEAR = 2004


def _max_year() -> int:
    return date.today().year


def repair(value: str | None) -> tuple[str | None, str]:
    """Return (value, verdict). verdict is one of: ok already plausible, untouched repaired …"""

    if not value or not value.strip():
        return None, "empty"
    match = ISO.match(value.strip())

    if not match:
        return None, "unparsable"
    year_text, month, day, tail = match.groups()
    year = int(year_text)
    ceiling = _max_year()

    if MIN_YEAR <= year <= ceiling:
        return value, "ok"
    swapped = int(year_text[1] + year_text[0] + year_text[2:])

    if MIN_YEAR <= swapped <= ceiling:
        return f"{swapped:04d}-{month}-{day}{tail or ''}", "repaired"

    return None, "implausible"
