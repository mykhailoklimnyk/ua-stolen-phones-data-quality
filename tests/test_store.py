"""What the fold must get right about a record's life."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import wantedmt.store as store_module
from wantedmt.sources import Revision
from wantedmt.store import Store


@pytest.fixture(autouse=True)
def _tiny_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both guards are proportions of a real register and cannot be expressed by three rows: …"""
    monkeypatch.setattr(store_module, "MIN_SNAPSHOT_ROWS", 1)
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE_PER_DAY", 1.0)
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE", 1.0)


def write(tmp_path: Path, day: str, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / f"mvs_{day}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    return path


def revision(day: str, source: str = "mvs") -> Revision:
    return Revision(
        source=source,
        snapshot_date=day,
        slug=day,
        url=f"http://x/{day}",
        api_hash=f"hash-{day}",
        size=1,
        created=day,
    )


def phone(
    record_id: str, nz: str = "NOKIA 3310", imei: str = "358240051111110", dtl: str = ""
) -> dict[str, str]:
    return {
        "ID": record_id,
        "OVD": "ВІДДІЛ",
        "INSERT_DATE": "2019-01-01T00:00:00",
        "NZ": nz,
        "IMEI": imei,
        "NK": "1",
        "DK": "2019-01-01T00:00:00",
        "DTL": dtl,
    }


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.duckdb")
    yield s
    s.close()


def fold(
    store: Store, tmp_path: Path, day: str, rows: list[dict[str, str]], source: str = "mvs"
) -> dict[str, object]:
    path = write(tmp_path, day, rows)

    return store.fold(path, revision(day, source), {"sha256": day, "bytes": 1})


def state(store: Store, record_id: str) -> dict[str, object]:
    columns = (
        "is_present",
        "first_seen",
        "last_seen",
        "disappeared_at",
        "returned_at",
        "disappeared_count",
        "revision_no",
        "nz",
    )
    row = store.con.execute(
        f"SELECT {', '.join(columns)} FROM records WHERE record_key = ?", [record_id]
    ).fetchone()
    assert row is not None, f"record {record_id} is not in the store"

    return dict(zip(columns, row, strict=True))


def test_first_snapshot_makes_every_record_new(store: Store, tmp_path: Path) -> None:
    result = fold(store, tmp_path, "2019-07-17", [phone("1"), phone("2")])
    assert result["appeared"] == 2
    assert result["changed"] == 0
    assert state(store, "1")["is_present"] is True
    assert str(state(store, "1")["first_seen"]) == "2019-07-17"


def test_unchanged_record_is_not_counted_as_changed(store: Store, tmp_path: Path) -> None:
    fold(store, tmp_path, "2019-07-17", [phone("1")])
    result = fold(store, tmp_path, "2019-07-18", [phone("1")])
    assert result["appeared"] == 0
    assert result["changed"] == 0
    assert state(store, "1")["revision_no"] == 1


def test_changed_values_are_stored_and_counted(store: Store, tmp_path: Path) -> None:
    fold(store, tmp_path, "2019-07-17", [phone("1", nz="NOKIA 3310")])
    result = fold(store, tmp_path, "2019-07-18", [phone("1", nz="NOKIA 6300")])
    assert result["changed"] == 1
    after = state(store, "1")
    assert after["nz"] == "NOKIA 6300"
    assert after["revision_no"] == 2
    assert str(after["first_seen"]) == "2019-07-17"


def test_absent_record_is_closed_on_the_day_it_goes(store: Store, tmp_path: Path) -> None:
    fold(store, tmp_path, "2019-07-17", [phone("1"), phone("2")])
    result = fold(store, tmp_path, "2019-07-18", [phone("1")])
    assert result["disappeared"] == 1
    gone = state(store, "2")
    assert gone["is_present"] is False
    assert str(gone["disappeared_at"]) == "2019-07-18"
    assert gone["disappeared_count"] == 1
    assert str(gone["last_seen"]) == "2019-07-17"


def test_returning_record_is_reopened_and_the_absence_remembered(
    store: Store, tmp_path: Path
) -> None:
    fold(store, tmp_path, "2019-07-17", [phone("1"), phone("2")])
    fold(store, tmp_path, "2019-07-18", [phone("1")])
    fold(store, tmp_path, "2019-07-19", [phone("1"), phone("2")])
    back = state(store, "2")
    assert back["is_present"] is True
    assert back["disappeared_at"] is None
    assert str(back["returned_at"]) == "2019-07-19"
    assert back["disappeared_count"] == 1


def test_returning_record_is_seen_even_when_its_values_did_not_move(
    store: Store, tmp_path: Path
) -> None:
    """The values match, so the update that watches for changes cannot see it."""
    fold(store, tmp_path, "2019-07-17", [phone("1"), phone("2", nz="SAMSUNG")])
    fold(store, tmp_path, "2019-07-18", [phone("1")])
    fold(store, tmp_path, "2019-07-19", [phone("1"), phone("2", nz="SAMSUNG")])
    assert state(store, "2")["is_present"] is True


def test_a_suspect_snapshot_closes_nothing(store: Store, tmp_path: Path) -> None:
    """A handover is not evidence that a million phones were found."""
    fold(store, tmp_path, "2019-07-17", [phone("1"), phone("2")])
    result = fold(store, tmp_path, "2019-07-18", [phone("1")], source="npu")
    assert result["is_suspect"] is True
    assert result["missing"] == 1, "the observation is kept"
    assert result["disappeared"] == 0, "but nothing is closed on it"
    assert state(store, "2")["is_present"] is True


def test_revision_number_does_not_tick_across_publishers(store: Store, tmp_path: Path) -> None:
    fold(store, tmp_path, "2019-07-17", [phone("1", nz="NOKIA")])
    fold(store, tmp_path, "2019-07-18", [phone("1", nz="SAMSUNG")], source="npu")
    assert state(store, "1")["revision_no"] == 1


def test_record_key_stays_unique_across_folds(store: Store, tmp_path: Path) -> None:
    fold(store, tmp_path, "2019-07-17", [phone("1"), phone("2")])
    fold(store, tmp_path, "2019-07-18", [phone("1", nz="X"), phone("3")])
    fold(store, tmp_path, "2019-07-19", [phone("1"), phone("2"), phone("3")])
    duplicates = store.con.execute(
        "SELECT count(*) FROM (SELECT record_key FROM records GROUP BY 1 HAVING count(*) > 1)"
    ).fetchone()
    assert duplicates is not None and duplicates[0] == 0


def test_ids_repeated_inside_one_file_are_counted_and_collapsed(
    store: Store, tmp_path: Path
) -> None:
    result = fold(store, tmp_path, "2019-07-17", [phone("1"), phone("1"), phone("2")])
    assert result["rows_raw"] == 3
    assert result["rows_dupe_id"] == 1
    assert result["records"] == 2


def test_history_records_every_kind_of_event(store: Store, tmp_path: Path) -> None:
    history = tmp_path / "history"
    path = write(tmp_path, "2019-07-17", [phone("1"), phone("2")])
    store.fold(path, revision("2019-07-17"), {"sha256": "a", "bytes": 1}, history_dir=history)
    path = write(tmp_path, "2019-07-18", [phone("1", nz="CHANGED"), phone("3")])
    store.fold(path, revision("2019-07-18"), {"sha256": "b", "bytes": 1}, history_dir=history)
    written = sorted(p.relative_to(history).as_posix() for p in history.rglob("*.parquet"))
    assert written == [
        "2019/07/mvs_2019-07-17.parquet",
        "2019/07/mvs_2019-07-18.parquet",
    ]
    events = dict(
        store.con.execute(
            f"SELECT event, count(*) FROM read_parquet"
            f"('{(history / '2019' / '07' / 'mvs_2019-07-18.parquet').as_posix()}') "
            f"GROUP BY 1"
        ).fetchall()
    )
    assert events == {"changed": 1, "appeared": 1, "disappeared": 1}


def test_the_same_bytes_under_a_new_date_are_recognised(store: Store, tmp_path: Path) -> None:
    fold(store, tmp_path, "2019-07-17", [phone("1")])
    assert store.hash_seen("hash-2019-07-17") == ("mvs", "2019-07-17")
    assert store.hash_seen("hash-2019-07-18") is None


def test_a_snapshot_dropping_too_much_closes_nothing(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observation is kept either way; only the closing is refused."""
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE_PER_DAY", 0.02)
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE", 0.25)
    fold(store, tmp_path, "2019-07-17", [phone(str(i)) for i in range(100)])
    result = fold(store, tmp_path, "2019-07-18", [phone(str(i)) for i in range(90)])
    assert result["missing"] == 10
    assert result["disappeared"] == 0
    assert "10 of 100" in str(result["suspect_reason"])


def test_the_allowance_grows_with_the_gap_between_snapshots(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five percent is a collapse over one day and unremarkable over a month."""
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE_PER_DAY", 0.02)
    monkeypatch.setattr(store_module, "MAX_DISAPPEARED_SHARE", 0.25)
    fold(store, tmp_path, "2019-07-17", [phone(str(i)) for i in range(100)])
    result = fold(store, tmp_path, "2019-08-17", [phone(str(i)) for i in range(95)])
    assert result["gap_days"] == 31
    assert result["disappeared"] == 5, "a month of attrition is not a broken file"


def quarantined(store: Store) -> list[tuple[str, str]]:
    return [
        (row[0], row[1])
        for row in store.con.execute(
            "SELECT record_key, value FROM quarantine_personal ORDER BY record_key"
        ).fetchall()
    ]


def stored_dtl(store: Store, record_id: str) -> str | None:
    row = store.con.execute("SELECT dtl FROM records WHERE record_key = ?", [record_id]).fetchone()

    return None if row is None else row[0]


def test_a_subscriber_number_in_dtl_never_reaches_the_records(store: Store, tmp_path: Path) -> None:
    """From 2021 the DTL string simply is the number, with nothing to name it."""
    fold(
        store,
        tmp_path,
        "2021-03-31",
        [
            phone("1", dtl="0501234567"),
            phone("2", dtl="380501234567"),
            phone("3", dtl="80501234567"),
        ],
    )
    assert stored_dtl(store, "1") is None
    assert stored_dtl(store, "2") is None
    assert stored_dtl(store, "3") is None
    assert quarantined(store) == [
        ("1", "0501234567"),
        ("2", "380501234567"),
        ("3", "80501234567"),
    ]


def test_an_imei_in_dtl_is_kept(store: Store, tmp_path: Path) -> None:
    """Fifteen digits is the second handset, not a person: 30_456 of the 51_500 such values in …"""
    fold(
        store,
        tmp_path,
        "2021-03-31",
        [
            phone("1", dtl="862634028016403"),
            phone("2", dtl="*"),
        ],
    )
    assert stored_dtl(store, "1") == "862634028016403"
    assert stored_dtl(store, "2") == "*"
    assert quarantined(store) == []


def test_a_number_already_held_is_not_stored_again(store: Store, tmp_path: Path) -> None:
    """The register restates the same numbers every day; inserting them each time reached 406 …"""
    fold(store, tmp_path, "2021-03-31", [phone("1", dtl="0501234567")])
    fold(store, tmp_path, "2021-04-01", [phone("1", dtl="0501234567")])
    fold(store, tmp_path, "2021-04-02", [phone("1", dtl="0501234567")])
    assert quarantined(store) == [("1", "0501234567")]


def test_an_object_orphaned_between_arrays_is_recovered(store: Store, tmp_path: Path) -> None:
    """`}]{...}[{` — every snapshot from 2021-03-31 on carries three of these, and they cost …"""
    path = tmp_path / "mvs_2021-03-31.json"
    body = json.dumps([phone("1"), phone("2")], ensure_ascii=False)
    orphan = json.dumps(phone("3"), ensure_ascii=False)
    tail = json.dumps([phone("4")], ensure_ascii=False)
    path.write_text(f"{body}{orphan}{tail}", encoding="utf-8")
    result = store.fold(path, revision("2021-03-31"), {"sha256": "a", "bytes": 1})
    assert result["read_mode"] == "repaired"
    assert result["records"] == 4, "the orphan is a record like any other"


def test_two_arrays_written_end_to_end_are_read_as_one(store: Store, tmp_path: Path) -> None:
    path = tmp_path / "mvs_2021-04-01.json"
    first = json.dumps([phone("1")], ensure_ascii=False)
    second = json.dumps([phone("2")], ensure_ascii=False)
    path.write_text(first + second, encoding="utf-8")
    result = store.fold(path, revision("2021-04-01"), {"sha256": "b", "bytes": 1})
    assert result["records"] == 2


def test_a_file_that_is_simply_broken_is_refused(store: Store, tmp_path: Path) -> None:
    """Repairs are for structure. Truncation has no single correct reading, and inventing one …"""
    path = tmp_path / "mvs_2021-04-02.json"
    path.write_text('[{"ID":"1","NZ":"NOKIA', encoding="utf-8")

    with pytest.raises(ValueError, match="no.*repairable defect"):
        store.fold(path, revision("2021-04-02"), {"sha256": "c", "bytes": 1})


def test_an_empty_date_does_not_make_the_file_unreadable(store: Store, tmp_path: Path) -> None:
    """The reader sampled 20_480 rows, decided INSERT_DATE was a timestamp, and refused record …"""
    rows = [phone(str(i)) for i in range(3)]
    rows.append({**phone("late"), "INSERT_DATE": ""})
    result = fold(store, tmp_path, "2021-03-31", rows)
    assert result["records"] == 4


def test_folding_backwards_is_refused(store: Store, tmp_path: Path) -> None:
    """A gap cannot be patched later: the older file would overwrite the present. This is what …"""
    fold(store, tmp_path, "2021-03-31", [phone("1", nz="OLD")])
    fold(store, tmp_path, "2026-08-01", [phone("1", nz="CURRENT")])

    with pytest.raises(ValueError, match="out of order"):
        fold(store, tmp_path, "2021-08-30", [phone("1", nz="STALE")])
    assert state(store, "1")["nz"] == "CURRENT", "the present survived the attempt"


def test_the_same_day_may_be_folded_again(store: Store, tmp_path: Path) -> None:
    """Refusing to go backwards must not refuse to redo the newest day, which is how a rerun …"""
    fold(store, tmp_path, "2026-08-01", [phone("1", nz="A")])
    fold(store, tmp_path, "2026-08-01", [phone("1", nz="B")])
    assert state(store, "1")["nz"] == "B"


def test_a_file_holding_less_than_the_register_changes_nothing(
    store: Store, tmp_path: Path
) -> None:
    """The floor has to refuse before the store moves, not after. Raised as its own type …"""
    store_module.MIN_SNAPSHOT_ROWS = 2

    try:
        fold(store, tmp_path, "2019-07-17", [phone("1"), phone("2")])

        with pytest.raises(store_module.SnapshotTooSmall):
            fold(store, tmp_path, "2019-07-18", [phone("1")])
    finally:
        store_module.MIN_SNAPSHOT_ROWS = 1
    assert state(store, "2")["is_present"] is True
