"""Fetch the external TAC reference list."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

URL = "https://raw.githubusercontent.com/MoazEb/tac-database/main/tac_full.csv"

DEST = Path("vendor/tac_full.csv")


MIN_BYTES = 5_000_000


def main() -> int:
    DEST.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as client:
        response = client.get(URL)
        response.raise_for_status()
        body = response.content

    if len(body) < MIN_BYTES:
        print(f"refusing {len(body):,} bytes — that is not the TAC list", file=sys.stderr)

        return 1
    header = body[:200].decode("utf-8", errors="replace").splitlines()[0]

    if "TAC" not in header.upper():
        print(f"refusing: unexpected header {header!r}", file=sys.stderr)

        return 1
    DEST.write_bytes(body)
    print(f"{DEST}: {len(body):,} bytes, header {header!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
