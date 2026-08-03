from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import duckdb
import pytest

from wantedmt import cli, reports

TIES = 40


def _store(path: Path) -> None:
    con = duckdb.connect(str(path), config={"enable_external_file_cache": "false"})
    con.execute("""
        CREATE TABLE records_normalized AS
        SELECT CAST(i AS VARCHAR) AS record_key,
               'BRAND' || CAST(i % 40 AS VARCHAR) AS brand,
               'UNIT ' || CAST(i % 40 AS VARCHAR) AS ovd,
               '3500000' || CAST(i % 40 AS VARCHAR) AS tac,
               DATE '2026-01-01' + CAST(i % 5 AS INTEGER) AS insert_date,
               DATE '2026-01-01' AS first_seen,
               CAST(NULL AS DATE) AS disappeared_at,
               true AS is_present,
               (i % 3) <> 0 AS imei_luhn_valid,
               CAST(NULL AS INTEGER) AS days_since_previous
        FROM range(400) t(i)
    """)
    con.execute("""
        CREATE TABLE record_changes AS
        SELECT CAST(i % 50 AS VARCHAR) AS record_key, 'dtl' AS field,
               'a' AS value_before, 'b' AS value_after,
               CASE WHEN i % 2 = 0 THEN 'changed' ELSE 'appeared' END AS reason
        FROM range(200) t(i)
    """)
    con.execute("""
        CREATE TABLE dict_tac AS
        SELECT '3500000' || CAST(i % 40 AS VARCHAR) AS tac, 'BRAND' AS brand,
               'Maker' AS manufacturer, 'MODEL' AS model, CAST(NULL AS INTEGER) AS release_year,
               10 AS records, 0.99 AS purity, 'register' AS source
        FROM range(40) t(i)
    """)
    con.execute("""
        CREATE TABLE snapshots AS
        SELECT 'npu' AS source, DATE '2026-01-01' AS snapshot_date, 'folded' AS status,
               false AS is_suspect
    """)
    con.execute("""
        CREATE TABLE observations AS
        SELECT DATE '2026-01-01' AS snapshot_date, 'npu' AS source, 100 AS record_count,
               CAST(NULL AS INTEGER) AS days_since_previous
    """)
    con.close()


def _digests(out: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob("*"))}


def test_two_exports_of_one_state_are_byte_identical(tmp_path: Path) -> None:
    """manifest.json publishes a sha256 per artefact. A checksum nobody can reproduce reads
    as corruption, so every COPY has to carry a total order rather than a scan order."""
    db = tmp_path / "store.duckdb"
    _store(db)
    runs = []

    for name in ("a", "b"):
        out = tmp_path / name
        assert cli.cmd_export(argparse.Namespace(db=str(db), out=str(out))) == 0
        runs.append(_digests(out))

    assert runs[0] == runs[1]
    assert len(runs[0]) >= 8


@pytest.mark.parametrize("name", sorted(reports.AGGREGATES))
def test_every_aggregate_orders_by_something_unique(name: str) -> None:
    """A tie broken by whichever row the scan reached first is a tie broken differently
    tomorrow. Ordering by a count alone is exactly that."""
    sql = " ".join(reports.AGGREGATES[name].split())
    match = re.search(r"ORDER BY (.+?)$", sql)
    assert match, f"{name} has no ORDER BY"
    terms = [t.strip() for t in match.group(1).split(",")]
    assert terms, name
    last = terms[-1].removesuffix(" DESC").removesuffix(" ASC")
    assert not last.lower().startswith(("count", "records", "closed")), (
        f"{name} breaks its last tie on a count — two rows with the same count "
        f"can come out in either order"
    )
