"""CKAN revision discovery."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import httpx

from .config import (
    MIN_REVISION_BYTES,
    MIN_REVISION_SIZE_SHARE,
    RESOURCE_SHOW,
    SOURCES,
    Source,
)


@dataclass(frozen=True)
class Revision:
    source: str
    snapshot_date: str
    slug: str
    url: str
    api_hash: str
    size: int
    created: str
    skip_reason: str = ""


def is_ahead(held: str | None, published: str) -> bool:
    """Does the portal hold a day we do not? Comparison is on the snapshot date, not on the …"""
    return bool(published) and (not held or published > held)


def fetch_revisions(source: Source, client: httpx.Client | None = None) -> list[Revision]:
    """All revisions of a source, oldest first, with broken uploads removed."""
    owns_client = client is None
    client = client or httpx.Client(timeout=120.0, follow_redirects=True)

    try:
        resp = client.get(RESOURCE_SHOW, params={"id": source.resource_id})
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
    finally:
        if owns_client:
            client.close()
    raw = payload.get("result", {}).get("resource_revisions", [])
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for rev in raw:
        created = rev.get("resource_created") or ""

        if created:
            by_day[created[:10]].append(rev)
    out: list[Revision] = []

    for day, revs in sorted(by_day.items()):
        largest = max((r.get("size") or 0) for r in revs)
        healthy = [r for r in revs if (r.get("size") or 0) >= largest * MIN_REVISION_SIZE_SHARE > 0]
        best = (
            max(healthy, key=lambda r: r.get("resource_created") or "")
            if healthy
            else max(revs, key=lambda r: r.get("size") or 0)
        )
        size = best.get("size") or 0
        reason = ""

        if not healthy:
            reason = f"every revision that day is undersized (largest {largest:,} B)"
        elif size < MIN_REVISION_BYTES:
            reason = f"{size:,} B is below the {MIN_REVISION_BYTES:,} B floor"
        out.append(
            Revision(
                source=source.key,
                snapshot_date=day,
                slug=(best.get("url") or "").rsplit("/", 1)[-1],
                url=best.get("url") or "",
                api_hash=best.get("file_hash_sum") or "",
                size=size,
                created=best.get("resource_created") or "",
                skip_reason=reason,
            )
        )

    return out


def build_timeline(client: httpx.Client | None = None) -> list[Revision]:
    """Both sources merged into one chronological series. Where the two overlap (MVS ran until …"""
    revisions: list[Revision] = []

    for key in ("mvs", "npu"):
        revisions.extend(fetch_revisions(SOURCES[key], client=client))
    preferred: dict[str, Revision] = {}

    for rev in revisions:
        current = preferred.get(rev.snapshot_date)

        if current is None:
            preferred[rev.snapshot_date] = rev
            continue

        if bool(current.skip_reason) != bool(rev.skip_reason):
            if current.skip_reason:
                preferred[rev.snapshot_date] = rev
        elif current.source == "mvs" and rev.source == "npu":
            preferred[rev.snapshot_date] = rev

    return [preferred[d] for d in sorted(preferred)]


def dump_timeline(revisions: list[Revision], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([r.__dict__ for r in revisions], fh, ensure_ascii=False, indent=1)


def load_timeline(path: str) -> list[Revision]:
    with open(path, encoding="utf-8") as fh:
        return [Revision(**row) for row in json.load(fh)]
