"""The run must cover everything it was given, and say so honestly if it did not."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import wantedmt.backfill as backfill_module
import wantedmt.store as store_module
from wantedmt.sources import Revision
from wantedmt.store import Store


@pytest.fixture(autouse=True)
def _tiny_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "MIN_SNAPSHOT_ROWS", 1)
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE_PER_DAY", 1.0)
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE", 1.0)


def phone(record_id: str) -> dict[str, str]:
    return {
        "ID": record_id,
        "OVD": "ВІДДІЛ",
        "INSERT_DATE": "2021-01-01T00:00:00",
        "NZ": "NOKIA 3310",
        "IMEI": "358240051111110",
        "NK": "1",
        "DK": "2021-01-01T00:00:00",
        "DTL": "",
    }


def series(days: int, duplicate_every: int = 0, skip_every: int = 0) -> list[Revision]:
    """A month of revisions, some of which need no download."""
    out = []

    for i in range(days):
        day = f"2021-06-{i + 1:02d}"
        api_hash = "same" if duplicate_every and i % duplicate_every == 0 and i else f"h{i}"
        reason = "too small" if skip_every and i % skip_every == 0 and i else ""
        out.append(Revision("mvs", day, day, f"http://x/{day}", api_hash, 100, day, reason))

    return out


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Write a snapshot instead of fetching one; the queue is what is under test."""

    def _download(revision: Revision, dest: Path, client: object) -> dict[str, object]:
        dest.parent.mkdir(parents=True, exist_ok=True)
        rows = [phone("1"), phone(revision.snapshot_date.replace("-", ""))]
        dest.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

        return {"bytes": 100, "sha256": revision.api_hash, "content_length": 100}

    monkeypatch.setattr(backfill_module, "download", _download)
    monkeypatch.setattr(backfill_module, "PROGRESS_FILE", tmp_path / "progress.json")

    return _download


def run(store: Store, revisions: list[Revision], tmp_path: Path, lookahead: int = 4):
    return backfill_module.run(store, revisions, work_dir=tmp_path / "work", lookahead=lookahead)


def test_every_revision_is_accounted_for(tmp_path: Path, fake_download: object) -> None:
    store = Store(tmp_path / "s.duckdb")

    try:
        revisions = series(20)
        summary = run(store, revisions, tmp_path)
        assert summary["folded"] == 20
    finally:
        store.close()


def test_republications_do_not_drain_the_queue(tmp_path: Path, fake_download: object) -> None:
    """The bug: a duplicate queues nothing, so a one-for-one loop shrinks."""
    store = Store(tmp_path / "s.duckdb")

    try:
        revisions = series(20, duplicate_every=3)
        summary = run(store, revisions, tmp_path, lookahead=4)
        covered = summary["folded"] + summary["duplicate"] + summary["skipped"]
        assert covered == 20, (
            f"only {covered} of 20 revisions were reached — the queue drained on "
            f"the revisions that needed no download"
        )
    finally:
        store.close()


def test_broken_uploads_do_not_drain_the_queue(tmp_path: Path, fake_download: object) -> None:
    store = Store(tmp_path / "s.duckdb")

    try:
        summary = run(store, series(20, skip_every=4), tmp_path, lookahead=4)
        covered = summary["folded"] + summary["duplicate"] + summary["skipped"]
        assert covered == 20
    finally:
        store.close()


def test_both_at_once_with_a_queue_shallower_than_the_gaps(
    tmp_path: Path, fake_download: object
) -> None:
    """Worst case: more no-download revisions in a row than the queue is deep."""
    store = Store(tmp_path / "s.duckdb")

    try:
        revisions = series(30, duplicate_every=2, skip_every=3)
        summary = run(store, revisions, tmp_path, lookahead=2)
        covered = summary["folded"] + summary["duplicate"] + summary["skipped"]
        assert covered == 30
    finally:
        store.close()


def test_the_progress_file_does_not_claim_completion_it_did_not_reach(
    tmp_path: Path, fake_download: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`percent: 100.0` was written unconditionally, which is how a truncated run announced …"""
    store = Store(tmp_path / "s.duckdb")

    try:
        run(store, series(10), tmp_path, lookahead=3)
    finally:
        store.close()
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert progress["complete"] is True
    assert progress["percent"] == 100.0
    assert progress["done"] == progress["total"]


def test_a_bad_publication_is_skipped_rather_than_retried_for_ever(
    tmp_path: Path, fake_download: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2021-10-21 arrives exactly as the listing promises and holds half the register. …"""
    monkeypatch.setattr(store_module, "MIN_SNAPSHOT_ROWS", 3)
    store = Store(tmp_path / "s.duckdb")

    try:
        summary = run(store, series(5), tmp_path)
        assert summary["skipped"] == 5, "recorded as not-the-register"
        assert summary["failed"] == 0, "and not as something to try again"
    finally:
        store.close()
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert progress["complete"] is True, "the run covered the series and said so"


def test_a_rerun_does_not_reach_back_behind_the_frontier(
    tmp_path: Path, fake_download: object
) -> None:
    """A failure left behind must not be retried once the run has moved past it: the snapshot …"""
    store = Store(tmp_path / "s.duckdb")

    try:
        revisions = series(6)
        run(store, revisions, tmp_path)
        store.record_failure(revisions[1], "connection dropped")
        summary = run(store, revisions, tmp_path)
        assert summary["folded"] == 0, "nothing behind the frontier is refolded"
    finally:
        store.close()
