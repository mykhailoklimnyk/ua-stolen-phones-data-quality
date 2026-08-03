"""Fetching one snapshot, with the portal's actual failure modes handled."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .sources import Revision

CHUNK = 1 << 20


class SnapshotTruncated(RuntimeError):
    """Body shorter than the Content-Length the server promised."""


@retry(
    retry=retry_if_exception_type((httpx.HTTPError, SnapshotTruncated)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=5, min=5, max=120),
    reraise=True,
)
def download(revision: Revision, dest: Path, client: httpx.Client) -> dict[str, object]:
    """Stream one revision to `dest`, verifying length and hashing as we go."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0

    with client.stream("GET", revision.url, timeout=httpx.Timeout(60.0, read=300.0)) as resp:
        resp.raise_for_status()
        expected = int(resp.headers.get("Content-Length") or 0)

        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes(CHUNK):
                fh.write(chunk)
                digest.update(chunk)
                written += len(chunk)

    if expected and written < expected:
        dest.unlink(missing_ok=True)
        raise SnapshotTruncated(
            f"{revision.source}/{revision.snapshot_date}: got {written:,} of {expected:,} bytes"
        )

    return {
        "bytes": written,
        "sha256": digest.hexdigest(),
        "content_length": expected,
    }


def iter_pending(revisions: list[Revision], done: set[tuple[str, str]]) -> Iterator[Revision]:
    """Revisions not yet folded into the store, in chronological order. Keyed on (source, …"""

    for rev in revisions:
        if (rev.source, rev.snapshot_date) not in done:
            yield rev
