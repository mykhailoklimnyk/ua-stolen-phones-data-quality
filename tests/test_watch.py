from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from wantedmt import cli
from wantedmt.sources import Revision, is_ahead


@pytest.mark.parametrize(
    ("held", "published", "expected"),
    [
        ("2026-08-01", "2026-08-02", True),
        ("2026-08-02", "2026-08-02", False),
        ("2026-08-03", "2026-08-02", False),
        (None, "2026-08-02", True),
        ("", "2026-08-02", True),
        ("2026-08-01", "", False),
    ],
)
def test_ahead_is_decided_by_the_snapshot_date(
    held: str | None, published: str, expected: bool
) -> None:
    assert is_ahead(held, published) is expected


def test_a_republished_file_on_a_held_day_is_not_new() -> None:
    """The portal reissues bytes without moving the date; that is not a day to fold."""
    assert is_ahead("2026-08-02", "2026-08-02") is False


def _revision(day: str) -> Revision:
    return Revision(
        "npu",
        day,
        f"rev-{day}",
        "https://example/x.json",
        "hash",
        322_000_000,
        f"{day} 10:30:00",
    )


def _write_manifest(tmp_path: Path, as_of: str | None) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"as_of": as_of}), encoding="utf-8")

    return path


@pytest.mark.parametrize(
    ("held", "portal", "code"),
    [("2026-08-01", "2026-08-02", 0), ("2026-08-02", "2026-08-02", cli.NOTHING_NEW)],
)
def test_watch_exit_code_reports_the_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: str, portal: str, code: int
) -> None:
    monkeypatch.setattr(cli, "fetch_revisions", lambda source: [_revision(portal)])
    args = argparse.Namespace(source="npu", manifest=str(_write_manifest(tmp_path, held)))
    assert cli.cmd_watch(args) == code


def test_watch_fails_loudly_when_the_portal_returns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty listing is a broken API, never an answer of 'nothing new'."""
    monkeypatch.setattr(cli, "fetch_revisions", lambda source: [])
    args = argparse.Namespace(source="npu", manifest=str(_write_manifest(tmp_path, "2026-08-01")))
    assert cli.cmd_watch(args) == 1


def test_watch_writes_the_finding_to_the_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(cli, "fetch_revisions", lambda source: [_revision("2026-08-02")])
    args = argparse.Namespace(source="npu", manifest=str(_write_manifest(tmp_path, "2026-08-01")))
    cli.cmd_watch(args)
    assert "2026-08-02" in summary.read_text(encoding="utf-8")
    assert "new data" in summary.read_text(encoding="utf-8")
