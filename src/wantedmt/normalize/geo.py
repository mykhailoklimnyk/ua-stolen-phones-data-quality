"""Region of the police unit that registered the record."""

from __future__ import annotations

import re

from .text import levenshtein

REGIONS = (
    "Вінницька",
    "Волинська",
    "Дніпропетровська",
    "Донецька",
    "Житомирська",
    "Закарпатська",
    "Запорізька",
    "Івано-Франківська",
    "Київська",
    "Кіровоградська",
    "Луганська",
    "Львівська",
    "Миколаївська",
    "Одеська",
    "Полтавська",
    "Рівненська",
    "Сумська",
    "Тернопільська",
    "Харківська",
    "Херсонська",
    "Хмельницька",
    "Черкаська",
    "Чернівецька",
    "Чернігівська",
)


SPECIAL = (
    ("КИЄВ", "м. Київ"),
    ("СЕВАСТОПОЛ", "м. Севастополь"),
    ("СЕВАСТПОЛ", "м. Севастополь"),
    ("КРИМ", "Автономна Республіка Крим"),
    ("КРЫМ", "Автономна Республіка Крим"),
)


FUZZY_BUDGET = 2

MIN_TOKEN = 6


def _key(value: str) -> str:
    """Letters only, Ї folded to І, so case endings are all that can differ."""

    return re.sub(r"[^А-ЯІЄҐЫЭЪ]", "", value.upper().replace("Ї", "І"))


_STEMS = tuple((re.sub(r"ЬКА$", "", _key(region)), f"{region} область") for region in REGIONS)


def region_of(ovd: str | None) -> str | None:
    """The region named anywhere in the unit name, or None. None is a legitimate answer: a …"""

    if not ovd or not ovd.strip():
        return None
    key = _key(ovd)

    for marker, name in SPECIAL:
        if _key(marker) in key:
            return name
    best = max(
        ((stem, name) for stem, name in _STEMS if stem and stem in key),
        key=lambda pair: len(pair[0]),
        default=None,
    )

    if best:
        return best[1]

    for token in re.findall(rf"[А-ЯІЄҐЫЭЪ]{{{MIN_TOKEN},}}", key):
        for stem, name in _STEMS:
            if abs(len(token) - len(stem)) <= FUZZY_BUDGET and (
                levenshtein(token, stem) <= FUZZY_BUDGET
            ):
                return name

    return None


UNIT_TYPES = (
    ("районне управління", re.compile(r"РАЙОНН\w*\s+УПРАВЛІНН")),
    ("управління", re.compile(r"УПРАВЛІНН")),
    ("відділення", re.compile(r"ВІДДІЛЕНН")),
    ("відділ", re.compile(r"ВІДДІЛ")),
)


def unit_type_of(ovd: str | None) -> str | None:
    if not ovd:
        return None
    text = ovd.upper()

    return next((label for label, rx in UNIT_TYPES if rx.search(text)), None)
