"""Normalising NZ — the free-text "Марка/модель" field."""

from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

HOMOGLYPHS = {
    "А": "A",
    "В": "B",
    "С": "C",
    "Е": "E",
    "Н": "H",
    "І": "I",
    "Ј": "J",
    "К": "K",
    "М": "M",
    "О": "O",
    "Р": "P",
    "Ѕ": "S",
    "Т": "T",
    "Х": "X",
    "Ү": "Y",
    "а": "a",
    "в": "b",
    "с": "c",
    "е": "e",
    "н": "h",
    "і": "i",
    "к": "k",
    "м": "m",
    "о": "o",
    "р": "p",
    "т": "t",
    "х": "x",
}


CYRILLIC = re.compile(r"[Ѐ-ӿ]")

LATIN = re.compile(r"[A-Za-z]")

SEPARATORS = re.compile(r"[\s/,;+\-_.]+")

NOISE = re.compile(r"[\"'«»`()\[\]{}]")


DICT_PATH = Path(__file__).resolve().parents[3] / "dict" / "brand_aliases.csv"

MODEL_DICT_PATH = Path(__file__).resolve().parents[3] / "dict" / "model_aliases.csv"


@lru_cache(maxsize=1)
def load_model_aliases() -> dict[str, str]:
    """Transliterated product-line words -> their Latin spelling. A dictionary is unavoidable …"""
    aliases: dict[str, str] = {}

    with open(MODEL_DICT_PATH, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = row["alias"].strip().upper()

            if key:
                aliases.setdefault(key, row["canonical"].strip())

    return aliases


def clean(value: str | None) -> str:
    """Trim, drop quoting noise, collapse whitespace, uppercase."""

    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = NOISE.sub(" ", text)
    text = SEPARATORS.sub(" ", text)

    return text.strip().upper()


def defuse_homoglyphs(text: str) -> tuple[str, bool]:
    """Convert Cyrillic look-alikes to Latin, but only in mixed-script strings. Returns the …"""
    converted = " ".join(
        "".join(HOMOGLYPHS.get(ch, ch) for ch in token) if _latin_dominates(token) else token
        for token in text.split(" ")
    )

    return converted, converted != text


HAS_DIGIT = re.compile(r"[0-9]")


MODEL_TRANSLIT = {
    "Б": "B",
    "Г": "G",
    "Д": "D",
    "З": "Z",
    "И": "I",
    "Й": "I",
    "Л": "L",
    "П": "P",
    "У": "U",
    "Ф": "F",
    "Ц": "C",
    "Э": "E",
    "Ы": "Y",
    "Ь": "",
    "Ъ": "",
}


def normalize_model(model: str | None) -> str | None:
    """Latinise Cyrillic look-alikes inside model codes. A model code is alphanumeric, so a …"""

    if not model:
        return model
    words = load_model_aliases()
    out = []

    for token in model.split(" "):
        if token in words:
            out.append(words[token])
        elif HAS_DIGIT.search(token) or len(token) == 1:
            out.append("".join(MODEL_TRANSLIT.get(c, HOMOGLYPHS.get(c, c)) for c in token))
        else:
            out.append(token)

    return " ".join(out)


def script_of(token: str) -> str:
    """latin | cyrillic | mixed | none. Only `mixed` is ambiguous, and it is the only case …"""
    latin = len(LATIN.findall(token))
    cyrillic = len(CYRILLIC.findall(token))

    if latin and cyrillic:
        return "mixed"

    if latin:
        return "latin"

    return "cyrillic" if cyrillic else "none"


def _latin_dominates(token: str) -> bool:
    latin = len(LATIN.findall(token))
    cyrillic = len(CYRILLIC.findall(token))

    return bool(cyrillic) and latin > cyrillic


def script_mixed(text: str) -> bool:
    """True when any token mixes alphabets — the cases to review by hand."""

    return any(script_of(token) == "mixed" for token in text.split(" "))


@lru_cache(maxsize=1)
def load_aliases(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """alias -> (brand, manufacturer). Two axes, because they answer different questions: …"""
    source = path or DICT_PATH
    aliases: dict[str, tuple[str, str]] = {}

    with open(source, encoding="utf-8") as fh:
        for row in csv.DictReader(line for line in fh if not line.startswith("#")):
            alias = clean(row["alias"])

            if alias:
                brand = row["brand"].strip()
                manufacturer = (row.get("manufacturer") or brand).strip()
                aliases.setdefault(alias, (brand, manufacturer))

    return aliases


@lru_cache(maxsize=1)
def _aliases_by_length() -> tuple[str, ...]:
    return tuple(sorted(load_aliases(), key=len, reverse=True))


@lru_cache(maxsize=1)
def _fuzzy_index() -> dict[int, tuple[tuple[str, str, str], ...]]:
    """Candidates keyed by length only. Bucketing on the first character as well looked …"""
    index: dict[int, list[tuple[str, str, str]]] = {}

    for alias, (brand, manufacturer) in load_aliases().items():
        if alias:
            index.setdefault(len(alias), []).append((alias, brand, manufacturer))

    return {key: tuple(value) for key, value in index.items()}


def levenshtein(a: str, b: str) -> int:
    """Damerau-Levenshtein: adjacent transposition costs 1, not 2. NOKAI vs NOKIA is one …"""

    if a == b:
        return 0

    if not a or not b:
        return len(a) or len(b)
    prev2: list[int] = []
    previous = list(range(len(b) + 1))

    for i, ca in enumerate(a, 1):
        current = [i]

        for j, cb in enumerate(b, 1):
            cost = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))

            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cost = min(cost, prev2[j - 2] + 1)
            current.append(cost)
        prev2, previous = previous, current

    return previous[-1]


def similarity(a: str, b: str) -> float:
    longest = max(len(a), len(b))

    return 1.0 if longest == 0 else 1.0 - levenshtein(a, b) / longest


def nearest(
    value: str, limit: int = 5, min_similarity: float = 0.7
) -> list[tuple[str, str, float]]:
    """Closest known aliases to a possibly misspelled query."""
    query = defuse_homoglyphs(clean(value))[0]

    if not query:
        return []
    scored = []

    for alias, (brand, _manufacturer) in load_aliases().items():
        if abs(len(alias) - len(query)) > 3:
            continue
        score = similarity(query, alias)

        if score >= min_similarity:
            scored.append((alias, brand, round(score, 3)))
    scored.sort(key=lambda row: (-row[2], row[0]))

    return scored[:limit]


def resolve(value: str | None) -> dict[str, object]:
    """Resolve one NZ spelling to brand + model. method records how the brand was found, so …"""
    raw = value or ""
    cleaned = clean(raw)

    if not cleaned:
        return {
            "nz_clean": "",
            "brand": None,
            "manufacturer": None,
            "model": None,
            "method": "empty",
            "homoglyph_fixed": False,
            "script_mixed": False,
        }
    mixed = script_mixed(cleaned)
    text, fixed = defuse_homoglyphs(cleaned)
    aliases = load_aliases()

    if text in aliases:
        brand, manufacturer = aliases[text]

        return {
            "nz_clean": text,
            "brand": brand,
            "manufacturer": manufacturer,
            "model": None,
            "method": "exact",
            "homoglyph_fixed": fixed,
            "script_mixed": mixed,
        }
    tokens = text.split()

    for size in range(min(3, len(tokens)), 0, -1):
        head = " ".join(tokens[:size])

        if head in aliases:
            brand, manufacturer = aliases[head]
            model = " ".join(tokens[size:]).strip() or None

            return {
                "nz_clean": text,
                "brand": brand,
                "manufacturer": manufacturer,
                "model": normalize_model(model),
                "method": "prefix",
                "homoglyph_fixed": fixed,
                "script_mixed": mixed,
            }
    head = tokens[0] if tokens else ""

    for alias in _aliases_by_length():
        if len(head) > len(alias) and head.startswith(alias):
            tail = head[len(alias) :].strip("-_ ")

            if tail and (not tail.isalpha() or len(alias) >= 5):
                brand, manufacturer = aliases[alias]
                model = " ".join([tail, *tokens[1:]]).strip()

                return {
                    "nz_clean": text,
                    "brand": brand,
                    "manufacturer": manufacturer,
                    "model": normalize_model(model),
                    "method": "glued",
                    "homoglyph_fixed": fixed,
                    "script_mixed": mixed,
                }

    if len(head) >= 4:
        budget = 1 if len(head) <= 6 else 2
        index = _fuzzy_index()
        best: tuple[int, str, str, str] | None = None

        for length in range(len(head) - budget, len(head) + budget + 1):
            for alias, brand, manufacturer in index.get(length, ()):
                distance = levenshtein(head, alias)

                if distance <= budget and (best is None or distance < best[0]):
                    best = (distance, alias, brand, manufacturer)

        if best is not None:
            model = " ".join(tokens[1:]).strip() or None

            return {
                "nz_clean": text,
                "brand": best[2],
                "manufacturer": best[3],
                "model": normalize_model(model),
                "method": "fuzzy",
                "homoglyph_fixed": fixed,
                "script_mixed": mixed,
            }

    return {
        "nz_clean": text,
        "brand": None,
        "manufacturer": None,
        "model": None,
        "method": "none",
        "homoglyph_fixed": fixed,
        "script_mixed": mixed,
    }
