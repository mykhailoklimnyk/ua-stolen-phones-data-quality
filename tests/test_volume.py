"""Published, moved, kept — the three weights the methodology has to separate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import wantedmt.store as store_module
from wantedmt.reports import volume
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
        "INSERT_DATE": "2020-01-01T00:00:00",
        "NZ": "NOKIA 3310",
        "IMEI": "358240051111110",
        "NK": "1",
        "DK": "2020-01-01T00:00:00",
        "DTL": "",
    }


@pytest.fixture
def series(tmp_path: Path) -> Store:
    """Two readings, one republication, one revision too small to be the register."""
    store = Store(tmp_path / "test.duckdb")
    folded = [("2020-09-22", "a", 300), ("2021-03-31", "b", 400)]

    for day, sha, size in folded:
        path = tmp_path / f"mvs_{day}.json"
        path.write_text(json.dumps([phone("1"), phone("2")]), encoding="utf-8")
        store.fold(
            path,
            Revision("mvs", day, day, f"http://x/{day}", sha, size, day),
            {"sha256": sha, "bytes": size},
        )
    repeat = Revision("mvs", "2021-01-15", "r", "http://x/r", "a", 300, "r")
    store.record_duplicate(repeat, "api_hash", "a", ("mvs", "2020-09-22"), 300)
    store.record_skip(Revision("mvs", "2020-06-19", "s", "http://x/s", "", 3, "s"), "3 B")
    yield store
    store.close()


def test_the_three_weights_are_reported_separately(series: Store, tmp_path: Path) -> None:
    figures = volume(series.con, {"store": tmp_path / "test.duckdb"})
    assert figures["revisions"] == 4
    assert figures["folded"] == 2
    assert figures["duplicate"] == 1
    assert figures["skipped"] == 1
    assert figures["bytes_published"] == 300 + 400 + 300 + 3
    assert figures["bytes_downloaded"] == 700, "the republication was never fetched"
    assert figures["bytes_not_fetched"] == 300


def test_kept_counts_every_artefact_named(series: Store, tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.mkdir()
    (history / "a.parquet").write_bytes(b"x" * 111)
    (history / "b.parquet").write_bytes(b"y" * 222)
    figures = volume(series.con, {"history": history})
    assert figures["kept_bytes"] == 333, "a directory is summed recursively"


def test_a_missing_artefact_weighs_nothing_rather_than_raising(
    series: Store, tmp_path: Path
) -> None:
    """The figure has to be takeable mid-run, before an export exists."""
    figures = volume(series.con, {"export": tmp_path / "not-built-yet"})
    assert figures["export_bytes"] == 0
    assert figures["kept_bytes"] == 0


def test_rows_read_counts_what_the_files_held(series: Store) -> None:
    figures = volume(series.con, {})
    assert figures["rows_read"] == 4, "two records in each of the two folded files"
    assert figures["first_date"].isoformat() == "2020-06-19"
    assert figures["last_date"].isoformat() == "2021-03-31"
