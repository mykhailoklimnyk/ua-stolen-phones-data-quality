"""A lifecycle date is only as precise as the gap before it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import wantedmt.store as store_module
from wantedmt.reports import AGGREGATES
from wantedmt.sources import Revision
from wantedmt.store import Store


@pytest.fixture(autouse=True)
def _tiny_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "MIN_SNAPSHOT_ROWS", 1)
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE_PER_DAY", 1.0)
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE", 1.0)


def phone(record_id: str, nz: str = "NOKIA 3310") -> dict[str, str]:
    return {
        "ID": record_id,
        "OVD": "ВІДДІЛ",
        "INSERT_DATE": "2020-01-01T00:00:00",
        "NZ": nz,
        "IMEI": "358240051111110",
        "NK": "1",
        "DK": "2020-01-01T00:00:00",
        "DTL": "",
    }


@pytest.fixture
def frozen(tmp_path: Path) -> Store:
    """2020-09-22 read, six months of nothing, 2021-03-31 read again. The middle snapshot is …"""
    store = Store(tmp_path / "test.duckdb")

    for day, rows, sha in (
        ("2020-09-22", [phone("1"), phone("2")], "a"),
        ("2021-01-15", [phone("1"), phone("2")], "a"),
        ("2021-03-31", [phone("1"), phone("3")], "b"),
    ):
        path = tmp_path / f"mvs_{day}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        revision = Revision(
            source="mvs",
            snapshot_date=day,
            slug=day,
            url=f"http://x/{day}",
            api_hash=sha,
            size=1,
            created=day,
        )

        if store.hash_seen(sha) is not None:
            store.record_duplicate(
                revision, "api_hash", sha, store.hash_seen(sha) or ("mvs", ""), 1
            )
            continue
        store.fold(path, revision, {"sha256": sha, "bytes": 1})
    store.con.execute(_observations())
    store.con.execute("""
        CREATE OR REPLACE TABLE records_normalized AS
        SELECT r.record_key, r.first_seen,
               CASE WHEN r.is_present THEN (SELECT max(snapshot_date) FROM observations)
                    ELSE r.last_seen END AS last_seen,
               r.disappeared_at,
               (SELECT max(o.snapshot_date) FROM observations o
                 WHERE o.snapshot_date < r.first_seen) AS first_seen_after,
               r.first_seen - (SELECT max(o.snapshot_date) FROM observations o
                                WHERE o.snapshot_date < r.first_seen) AS first_seen_days,
               CASE WHEN r.disappeared_at IS NOT NULL
                    THEN r.disappeared_at - r.last_seen END AS disappeared_days,
               r.is_present
        FROM records r
    """)
    yield store
    store.close()


def _observations() -> str:
    from wantedmt.normalize.apply import OBSERVATIONS_SQL

    return OBSERVATIONS_SQL


def row(store: Store, record_id: str) -> dict[str, object]:
    columns = (
        "first_seen",
        "first_seen_after",
        "first_seen_days",
        "last_seen",
        "disappeared_at",
        "disappeared_days",
        "is_present",
    )
    result = store.con.execute(
        f"SELECT {', '.join(columns)} FROM records_normalized WHERE record_key = ?",
        [record_id],
    ).fetchone()
    assert result is not None

    return dict(zip(columns, result, strict=True))


def test_republished_bytes_are_not_an_observation(frozen: Store) -> None:
    dates = [
        str(r[0])
        for r in frozen.con.execute("SELECT snapshot_date FROM observations ORDER BY 1").fetchall()
    ]
    assert dates == ["2020-09-22", "2021-03-31"], (
        "the reissued file would say the register was checked in January"
    )


def test_a_record_that_arrived_during_the_silence_carries_the_whole_window(
    frozen: Store,
) -> None:
    arrived = row(frozen, "3")
    assert str(arrived["first_seen"]) == "2021-03-31"
    assert str(arrived["first_seen_after"]) == "2020-09-22"
    assert arrived["first_seen_days"] == 190, (
        "the entry is known to within six months, and the column has to say so"
    )


def test_a_record_present_from_the_start_has_no_lower_bound(frozen: Store) -> None:
    """NULL, not zero: nothing this dataset holds bounds its entry from below."""
    first = row(frozen, "1")
    assert first["first_seen_after"] is None
    assert first["first_seen_days"] is None


def test_a_departure_carries_its_window_too(frozen: Store) -> None:
    gone = row(frozen, "2")
    assert gone["is_present"] is False
    assert str(gone["last_seen"]) == "2020-09-22", "the last reading that held it"
    assert str(gone["disappeared_at"]) == "2021-03-31", "the first that did not"
    assert gone["disappeared_days"] == 190


def test_the_monthly_series_reports_months_nobody_looked_at(frozen: Store) -> None:
    rows = frozen.con.execute(AGGREGATES["monthly_coverage"]).fetchall()
    periods = {str(r[0]): r for r in rows}
    assert "2020-11-01" in periods, (
        "a month absent from the series is a month the reader's chart draws straight through"
    )
    november = periods["2020-11-01"]
    assert november[1] == 0, "days_observed"
    assert november[6] is False, "is_measured must say the zero is not a measurement"
    assert periods["2020-09-01"][6] is True


def test_a_month_with_no_reading_reports_no_arrivals_rather_than_zero_thefts(
    frozen: Store,
) -> None:
    rows = frozen.con.execute(AGGREGATES["monthly_coverage"]).fetchall()
    unmeasured = [r for r in rows if r[6] is False]
    assert unmeasured, "the fixture is meant to contain unobserved months"
    assert all(r[5] == 0 for r in unmeasured), "no arrivals can be attributed there"
    assert all(r[3] == 0.0 for r in unmeasured), "coverage_pct is zero, not null"
