"""The non-public lookup projections handed to the Trofey IMEI check (issue #1).

These files never enter a release: the full TAC dictionary carries the MIT catalogue we
may use but not republish. They travel to one prod Postgres, so the guards here are the
only thing standing between a broken query and a public-facing lookup that answers
«not stolen» to everyone.
"""

from __future__ import annotations

import argparse
import csv
import inspect
import json
from pathlib import Path

import duckdb
import pytest

from wantedmt import cli

PHONE = "0501234567"


def _store(path: Path, *, poisoned_brand: str | None = None, tacs: int = 3) -> None:
    con = duckdb.connect(str(path), config={"enable_external_file_cache": "false"})
    # Six records, five distinct numbers: one number is claimed twice (the fold leaves
    # 8_309 such groups in the real store), one record is closed, one has no IMEI at all.
    con.execute("""
        CREATE TABLE records_normalized (
            record_key VARCHAR, imei_norm VARCHAR, insert_date DATE, is_present BOOLEAN
        )
    """)
    con.execute("""
        INSERT INTO records_normalized VALUES
            ('k1', '350000001234561', DATE '2026-03-02', true),
            ('k2', '350000001234561', DATE '2026-01-05', true),
            ('k3', '350000001234570', DATE '2026-02-10', true),
            ('k4', '350000001234588', NULL,             true),
            ('k5', '350000009999999', DATE '2026-02-11', false),
            ('k6', NULL,              DATE '2026-02-12', true)
    """)
    con.execute("""
        CREATE TABLE dict_tac (
            tac VARCHAR, brand VARCHAR, manufacturer VARCHAR, model VARCHAR,
            records INTEGER, purity DOUBLE, source VARCHAR
        )
    """)

    for i in range(tacs):
        brand = poisoned_brand if poisoned_brand and i == 0 else "Apple"
        source = "external" if i % 2 else "register"
        con.execute(
            "INSERT INTO dict_tac VALUES (?, ?, 'Apple Inc', ?, 10, 0.99, ?)",
            [f"3532561{i}", brand, f"iPhone 1{i}", source],
        )
    con.execute("""
        CREATE TABLE snapshots (
            source VARCHAR, snapshot_date DATE, status VARCHAR, is_suspect BOOLEAN
        )
    """)
    con.execute("""
        INSERT INTO snapshots VALUES
            ('npu', DATE '2026-08-06', 'folded', false),
            ('npu', DATE '2026-08-07', 'folded', false),
            ('npu', DATE '2026-08-08', 'pending', false)
    """)
    con.execute("CREATE TABLE quarantine_personal (record_key VARCHAR, nomer VARCHAR)")
    con.execute(f"INSERT INTO quarantine_personal VALUES ('k1', '{PHONE}')")
    con.close()


def _run(tmp_path: Path, **overrides: object) -> Path:
    source = tmp_path / "store.duckdb"

    if not source.exists():
        _store(source)
    out = tmp_path / "lookup"
    args = argparse.Namespace(db=str(source), out=str(out), min_imei=1, min_tac=1)

    for key, value in overrides.items():
        setattr(args, key, value)
    assert cli.cmd_lookup_export(args) == 0

    return out


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_projection_is_one_row_per_number_dated_by_the_first_claim(tmp_path: Path) -> None:
    rows = _rows(_run(tmp_path) / "imei_current.csv")
    assert [r["imei"] for r in rows] == [
        "350000001234561",
        "350000001234570",
        "350000001234588",
    ]
    # the twice-claimed number keeps the EARLIER date — «wanted since», not «last seen»
    assert rows[0]["listed_on"] == "2026-01-05"
    # a record with no insert_date still travels; the consumer renders it without a date
    assert rows[2]["listed_on"] == ""


def test_closed_records_and_missing_numbers_stay_out(tmp_path: Path) -> None:
    imeis = {r["imei"] for r in _rows(_run(tmp_path) / "imei_current.csv")}
    assert "350000009999999" not in imeis  # is_present = false
    assert "" not in imeis  # imei_norm IS NULL


def test_the_status_is_the_one_claim_the_source_supports(tmp_path: Path) -> None:
    """The source has no stolen/lost marker on the record — only the name of the set says
    it. So the projection says «listed», and the Trofey page says «у розшуку» (Trofey#671
    spec v2.1). Anything richer would be our invention presented as police data."""
    assert {r["status"] for r in _rows(_run(tmp_path) / "imei_current.csv")} == {"listed"}


def test_the_dictionary_carries_both_sources_unlike_the_public_export(tmp_path: Path) -> None:
    rows = _rows(_run(tmp_path) / "dict_tac.csv")
    assert [r["tac"] for r in rows] == ["35325610", "35325611", "35325612"]  # ordered
    assert list(rows[0]) == ["tac", "brand", "model"]  # exactly the contract columns
    assert {r["model"] for r in rows} == {"iPhone 10", "iPhone 11", "iPhone 12"}


def test_meta_names_the_last_folded_day_and_the_counts(tmp_path: Path) -> None:
    out = _run(tmp_path)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"as_of": "2026-08-07", "imei_rows": 3, "tac_rows": 3}
    # the loader refuses on a mismatch, so the counts must describe the files as written
    assert meta["imei_rows"] == len(_rows(out / "imei_current.csv"))
    assert meta["tac_rows"] == len(_rows(out / "dict_tac.csv"))


def test_a_thin_export_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    """A query that stops matching returns few rows, not an error — and few rows would
    replace a million-row registry with a stub that clears every phone."""
    source = tmp_path / "store.duckdb"
    _store(source)
    out = tmp_path / "lookup"

    with pytest.raises(SystemExit) as raised:
        cli.cmd_lookup_export(
            argparse.Namespace(db=str(source), out=str(out), min_imei=1_000_000, min_tac=1)
        )

    assert "below the floor" in str(raised.value)
    assert not list(out.glob("*"))


def test_a_thin_dictionary_is_refused_too(tmp_path: Path) -> None:
    source = tmp_path / "store.duckdb"
    _store(source)
    out = tmp_path / "lookup"

    with pytest.raises(SystemExit) as raised:
        cli.cmd_lookup_export(
            argparse.Namespace(db=str(source), out=str(out), min_imei=1, min_tac=200_000)
        )

    assert "TAC rows is below the floor" in str(raised.value)
    assert not list(out.glob("*"))


def test_a_phone_shaped_label_stops_the_export(tmp_path: Path) -> None:
    """A subscriber number that reached a brand field would travel to a public lookup —
    the same scan `state` runs, aimed at the two free-text columns leaving here."""
    source = tmp_path / "store.duckdb"
    _store(source, poisoned_brand=PHONE)
    out = tmp_path / "lookup"

    with pytest.raises(SystemExit) as raised:
        cli.cmd_lookup_export(
            argparse.Namespace(db=str(source), out=str(out), min_imei=1, min_tac=1)
        )

    assert "phone-shaped" in str(raised.value)
    assert not list(out.glob("*"))


def test_the_handset_numbers_themselves_are_not_mistaken_for_phones(tmp_path: Path) -> None:
    """Fifteen digits are a handset and eight are a model code; refusing those would
    refuse every export ever made."""
    out = _run(tmp_path)
    assert (out / "imei_current.csv").exists() and (out / "dict_tac.csv").exists()


def test_a_grown_projection_stops_the_run(tmp_path: Path) -> None:
    """The allow-list is not decoration: a column added to the query — the way `records`
    and `purity` sit next to brand and model in the store — would be handed over silently
    otherwise. DuckDB is asked what the query returns; the answer must be the contract."""
    source = tmp_path / "store.duckdb"
    _store(source)
    out = tmp_path / "lookup"
    original = cli.LOOKUP_TAC_SQL

    try:
        cli.LOOKUP_TAC_SQL = "SELECT tac, brand, model, purity FROM dict_tac ORDER BY tac"

        with pytest.raises(SystemExit) as raised:
            cli.cmd_lookup_export(
                argparse.Namespace(db=str(source), out=str(out), min_imei=1, min_tac=1)
            )
    finally:
        cli.LOOKUP_TAC_SQL = original

    assert "the contract is" in str(raised.value)
    assert not list(out.glob("*"))


def test_a_store_with_nothing_folded_hands_over_no_date(tmp_path: Path) -> None:
    """as_of is what the Trofey watchdog watches; «today» inferred from the clock would
    make a frozen registry look fresh forever."""
    source = tmp_path / "store.duckdb"
    _store(source)
    con = duckdb.connect(str(source))
    con.execute("UPDATE snapshots SET status = 'pending'")
    con.close()
    out = tmp_path / "lookup"

    with pytest.raises(SystemExit) as raised:
        cli.cmd_lookup_export(
            argparse.Namespace(db=str(source), out=str(out), min_imei=1, min_tac=1)
        )

    assert "no as-of date" in str(raised.value)
    assert not list(out.glob("*"))


def test_the_quarantine_is_not_reachable_from_this_command(tmp_path: Path) -> None:
    """The blocking invariant of the whole repository, extended to this export: the
    personal-data table is not named in its SQL, not in its columns, and its content is
    absent from the bytes that leave — the same proof `state` carries."""
    source = inspect.getsource(cli.cmd_lookup_export) + cli.LOOKUP_IMEI_SQL + cli.LOOKUP_TAC_SQL
    assert "quarantine" not in source.lower()
    out = _run(tmp_path)

    for name in ("imei_current.csv", "dict_tac.csv", "meta.json"):
        assert PHONE.encode() not in (out / name).read_bytes()
