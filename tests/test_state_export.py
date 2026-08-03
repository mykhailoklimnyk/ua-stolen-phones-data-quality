from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pytest

from wantedmt import cli

NUMBER = "380671234567"


def _store(path: Path) -> None:
    con = duckdb.connect(str(path), config={"enable_external_file_cache": "false"})
    con.execute("CREATE TABLE records (record_key VARCHAR, imei VARCHAR)")
    con.execute("INSERT INTO records VALUES ('k1', '350000001234567')")
    con.execute(
        "CREATE TABLE snapshots (source VARCHAR, snapshot_date DATE, "
        "PRIMARY KEY (source, snapshot_date))"
    )
    con.execute("INSERT INTO snapshots VALUES ('npu', DATE '2026-08-01')")
    con.execute("CREATE TABLE quarantine_personal (record_key VARCHAR, nomer VARCHAR)")
    con.execute(f"INSERT INTO quarantine_personal VALUES ('k1', '{NUMBER}')")
    con.close()


def _export(tmp_path: Path) -> Path:
    source = tmp_path / "store.duckdb"
    _store(source)
    out = tmp_path / "state.duckdb"
    assert cli.cmd_state(argparse.Namespace(db=str(source), out=str(out))) == 0

    return out


def test_the_shared_copy_has_no_quarantine_table(tmp_path: Path) -> None:
    con = duckdb.connect(str(_export(tmp_path)), read_only=True)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert "quarantine_personal" not in tables
    assert tables == {"records", "snapshots"}


def test_the_number_is_absent_from_the_bytes_not_merely_from_the_schema(tmp_path: Path) -> None:
    """Dropping a table frees its blocks without overwriting them; a release asset is raw bytes."""
    assert NUMBER.encode() not in _export(tmp_path).read_bytes()


def test_the_copy_keeps_the_constraint_the_daily_upsert_depends_on(tmp_path: Path) -> None:
    out = _export(tmp_path)
    con = duckdb.connect(str(out))

    try:
        with pytest.raises(duckdb.ConstraintException):
            con.execute("INSERT INTO snapshots VALUES ('npu', DATE '2026-08-01')")
    finally:
        con.close()


def test_the_copy_carries_the_data(tmp_path: Path) -> None:
    con = duckdb.connect(str(_export(tmp_path)), read_only=True)
    rows = con.execute("SELECT record_key, imei FROM records").fetchall()
    con.close()
    assert rows == [("k1", "350000001234567")]


def test_staging_does_not_survive_the_run(tmp_path: Path) -> None:
    out = _export(tmp_path)
    assert not list(out.parent.glob("*.staging.duckdb*"))


def test_a_phone_left_in_dtl_stops_the_export(tmp_path: Path) -> None:
    """The quarantine table is not the only way a number can travel — DTL itself is one."""
    source = tmp_path / "store.duckdb"
    con = duckdb.connect(str(source), config={"enable_external_file_cache": "false"})
    con.execute("CREATE TABLE records (record_key VARCHAR, dtl VARCHAR)")
    con.execute(f"INSERT INTO records VALUES ('k1', '{NUMBER}')")
    con.close()
    out = tmp_path / "state.duckdb"

    with pytest.raises(SystemExit) as raised:
        cli.cmd_state(argparse.Namespace(db=str(source), out=str(out)))

    assert "records.dtl" in str(raised.value)
    assert not out.exists()


def test_an_imei_in_dtl_is_not_mistaken_for_a_phone(tmp_path: Path) -> None:
    """Fifteen digits are a handset, not a subscriber; refusing those would refuse every file."""
    source = tmp_path / "store.duckdb"
    con = duckdb.connect(str(source), config={"enable_external_file_cache": "false"})
    con.execute("CREATE TABLE records (record_key VARCHAR, dtl VARCHAR)")
    con.execute("INSERT INTO records VALUES ('k1', '350000001234567')")
    con.close()
    out = tmp_path / "state.duckdb"
    assert cli.cmd_state(argparse.Namespace(db=str(source), out=str(out))) == 0
    assert out.exists()
