"""IMEI check-digit rules."""

from __future__ import annotations

import re

NON_DIGIT = re.compile(r"\D")


def digits_of(raw: str | None) -> str:
    return NON_DIGIT.sub("", raw or "")


def luhn_check_digit(fourteen: str) -> int:
    """Check digit for the first 14 digits of an IMEI (mod-10, doubling evens)."""
    total = 0

    for index, char in enumerate(fourteen):
        digit = int(char)

        if index % 2:
            digit *= 2

            if digit > 9:
                digit -= 9
        total += digit

    return (10 - total % 10) % 10


def luhn_valid(imei15: str) -> bool:
    return len(imei15) == 15 and luhn_check_digit(imei15[:14]) == int(imei15[14])


def canonical(raw: str | None) -> str | None:
    """15-digit form of a typed identifier, or None when it is not one."""
    digits = digits_of(raw)

    if not digits or len(set(digits)) == 1 or digits.startswith("00"):
        return None

    if len(digits) == 14:
        return digits + str(luhn_check_digit(digits))

    if len(digits) == 16:
        return digits[:14] + str(luhn_check_digit(digits[:14]))

    if len(digits) in (15, 17, 18):
        return digits[:15]

    return None
