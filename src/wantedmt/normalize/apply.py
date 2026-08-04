"""Bulk normalisation pass over the folded history."""

from __future__ import annotations

import csv
import multiprocessing
import os
import pathlib
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import Any, TypeVar

import duckdb
import structlog

from ..sql import count
from . import geo
from . import tac as tac_dict
from .text import RESOLVER_VERSION
from .text import resolve as resolve_nz

log = structlog.get_logger()

T = TypeVar("T")


def _luhn_sql(column: str) -> str:
    """Mod-10 checksum over a 15-character digit string, inline."""
    terms = []

    for pos in range(1, 15):
        digit = f"CAST(substr({column},{pos},1) AS INTEGER)"

        if pos % 2 == 0:
            terms.append(f"(CASE WHEN {digit}*2 > 9 THEN {digit}*2-9 ELSE {digit}*2 END)")
        else:
            terms.append(digit)

    return " + ".join(terms)


NZ_MAP_DDL = """
CREATE TABLE IF NOT EXISTS dict_nz_mapping (
    nz_raw       VARCHAR,
    nz_clean     VARCHAR,
    brand        VARCHAR,
    manufacturer VARCHAR,
    model        VARCHAR,
    method       VARCHAR,
    homoglyph_fixed BOOLEAN
);
"""


def _resolve_chunk(spellings: list[str]) -> list[tuple[Any, ...]]:
    """Worker body. Module level and argument-only so it survives `spawn`."""
    out = []

    for raw in spellings:
        r = resolve_nz(raw)
        out.append(
            (
                raw,
                r["nz_clean"],
                r["brand"],
                r["manufacturer"],
                r["model"],
                r["method"],
                r["homoglyph_fixed"],
            )
        )

    return out


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 2))


GEO_DDL = """
CREATE OR REPLACE TABLE dict_ovd (
    ovd VARCHAR, region VARCHAR, unit_type VARCHAR
);
"""


def build_geo(con: duckdb.DuckDBPyConnection) -> int:
    """Region per distinct unit name — 942 of them against a million records."""
    con.execute(GEO_DDL)
    values = [
        row[0]
        for row in con.execute(
            "SELECT DISTINCT ovd FROM records WHERE nullif(trim(ovd), '') IS NOT NULL"
        ).fetchall()
    ]
    rows = [(v, geo.region_of(v), geo.unit_type_of(v)) for v in values]
    con.executemany("INSERT INTO dict_ovd VALUES (?, ?, ?)", rows)

    return len(rows)


def build_nz_map(con: duckdb.DuckDBPyConnection, workers: int = 0) -> int:
    """Resolve every distinct NZ spelling not yet in the map. Returns new rows."""
    con.execute(NZ_MAP_DDL)
    con.execute("CREATE TABLE IF NOT EXISTS dict_nz_version (version INTEGER)")
    stored = con.execute("SELECT max(version) FROM dict_nz_version").fetchone()

    if stored is None or stored[0] != RESOLVER_VERSION:
        log.info("nz_map.resolver_changed", stored=stored and stored[0], now=RESOLVER_VERSION)
        con.execute("DELETE FROM dict_nz_mapping")
        con.execute("DELETE FROM dict_nz_version")
        con.execute("INSERT INTO dict_nz_version VALUES (?)", [RESOLVER_VERSION])
    con.execute("""
        CREATE OR REPLACE TABLE dict_nz_mapping AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY nz_raw) AS rn FROM dict_nz_mapping
        ) WHERE rn = 1
    """)
    pending = con.execute("""
        SELECT DISTINCT r.nz FROM records r
        WHERE NOT EXISTS (
            SELECT 1 FROM dict_nz_mapping m WHERE m.nz_raw IS NOT DISTINCT FROM r.nz
        )
    """).fetchall()

    if not pending:
        return 0
    spellings = [raw for (raw,) in pending]
    workers = workers or default_workers()

    if workers > 1 and len(spellings) > 5_000:
        size = max(500, len(spellings) // (workers * 4))
        chunks = [spellings[i : i + size] for i in range(0, len(spellings), size)]
        context = multiprocessing.get_context("spawn")
        log.info("nz_map.parallel", spellings=len(spellings), workers=workers, chunks=len(chunks))

        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
            rows = [row for part in pool.map(_resolve_chunk, chunks) for row in part]
    else:
        log.info("nz_map.serial", spellings=len(spellings))
        rows = _resolve_chunk(spellings)

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "nz_new.csv"

        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "nz_raw",
                    "nz_clean",
                    "brand",
                    "manufacturer",
                    "model",
                    "method",
                    "homoglyph_fixed",
                ]
            )
            writer.writerows(rows)
        con.execute(f"COPY dict_nz_mapping FROM '{path.as_posix()}' (HEADER, DELIMITER ',')")

    return len(rows)


IMEI_SQL = f"""
CREATE OR REPLACE TABLE imei_normalized AS
WITH d AS (
    SELECT record_key, imei AS imei_raw,
           regexp_replace(imei, '\\D', '', 'g') AS digits
    FROM records
),
k AS (
    SELECT record_key, imei_raw, digits, length(digits) AS n,
           CASE
             WHEN digits = '' OR digits LIKE '00%' THEN NULL
             WHEN length(digits) IN (15, 17, 18) THEN substr(digits, 1, 15)
             WHEN length(digits) = 14 THEN digits          -- check digit added below
             WHEN length(digits) = 16 THEN substr(digits, 1, 14)
             ELSE NULL
           END AS base
    FROM d
),
c AS (
    SELECT *,
           CASE WHEN base IS NOT NULL AND length(base) = 14
                THEN (10 - (({_luhn_sql("base")}) % 10)) % 10 END AS calc_for_14
    FROM k
),
f AS (
    SELECT record_key, imei_raw, digits, n,
           CASE WHEN base IS NULL THEN NULL
                WHEN length(base) = 14 THEN base || CAST(calc_for_14 AS VARCHAR)
                ELSE base END AS imei_norm,
           length(base) = 14 AS repaired
    FROM c
)
SELECT record_key, imei_raw, n AS imei_digits, imei_norm, repaired AS imei_was_repaired,
       CASE
         WHEN imei_norm IS NULL AND n BETWEEN 8 AND 13 THEN 'SERIAL'
         WHEN imei_norm IS NULL THEN 'INVALID'
         WHEN n = 16 THEN 'IMEISV'
         ELSE 'IMEI'
       END AS imei_kind,
       substr(imei_norm, 1, 8)  AS tac,
       substr(imei_norm, 1, 2)  AS rbi,
       substr(imei_norm, 1, 6)  AS tac_legacy,
       substr(imei_norm, 7, 2)  AS fac,
       substr(imei_norm, 9, 6)  AS snr,
       substr(imei_norm, 15, 1) AS check_digit,
       CASE WHEN imei_norm IS NULL THEN NULL
            ELSE (10 - (({_luhn_sql("substr(imei_norm,1,15)")}) % 10)) % 10
                 = CAST(substr(imei_norm, 15, 1) AS INTEGER) END AS imei_luhn_valid
FROM f
"""


OBSERVATIONS_SQL = """
CREATE OR REPLACE TABLE observations AS
SELECT snapshot_date, source, record_count, NOT is_suspect AS closures_trusted,
       snapshot_date - lag(snapshot_date) OVER (ORDER BY snapshot_date) AS days_since_previous
FROM snapshots
WHERE coalesce(status, 'folded') = 'folded'
ORDER BY snapshot_date
"""


MODEL_LINES_SQL = """
CREATE OR REPLACE TABLE dict_model_lines AS
SELECT upper(trim(brand)) AS brand, upper(trim(line_word)) AS line_word
FROM read_csv('dict/model_lines.csv', header=true, ignore_errors=true)
WHERE line_word IS NOT NULL AND trim(line_word) <> ''
"""


MODEL_KEY_SQL = r"""
ALTER TABLE records_normalized ADD COLUMN IF NOT EXISTS model_key VARCHAR;
UPDATE records_normalized r SET model_key = (
    WITH stripped AS (
        SELECT trim(regexp_replace(upper(r.model), '^' || upper(r.brand) || '\s+', '')) AS m
    )
    SELECT nullif(trim(regexp_replace(
        s.m,
        '^' || coalesce((SELECT max(l.line_word) FROM dict_model_lines l
                         WHERE l.brand = upper(r.brand)
                           AND (s.m LIKE l.line_word || ' %' OR s.m = l.line_word)), '~~')
             || '(\s+|$)', '')), '')
    FROM stripped s
)
WHERE r.model IS NOT NULL;
"""


NORMALIZED_SQL = """
CREATE OR REPLACE TABLE records_normalized AS
SELECT
    r.record_key,
    r.ovd,
    g.region,
    g.unit_type,
    CASE WHEN try_cast(r.insert_date AS TIMESTAMP)
              BETWEEN TIMESTAMP '2004-01-01' AND now() + INTERVAL 1 DAY
         THEN try_cast(r.insert_date AS TIMESTAMP) END           AS insert_date,
    CASE WHEN try_cast(r.dk AS TIMESTAMP)
              BETWEEN TIMESTAMP '2004-01-01' AND now() + INTERVAL 1 DAY
         THEN try_cast(r.dk AS TIMESTAMP) END                    AS dk,
    r.insert_date IS NOT NULL
      AND try_cast(r.insert_date AS TIMESTAMP) NOT BETWEEN TIMESTAMP '2004-01-01'
          AND now() + INTERVAL 1 DAY                             AS insert_date_implausible,
    nullif(trim(r.nk), '')                                       AS nk,
    nullif(trim(r.dtl), '')                                      AS dtl,
    r.nz                                                         AS nz_raw,
    m.nz_clean, m.brand, m.manufacturer, m.model,
    m.method AS nz_method, m.homoglyph_fixed,
    i.imei_raw, i.imei_norm, i.imei_kind, i.imei_digits,
    i.imei_luhn_valid, i.imei_was_repaired,
    i.tac, i.rbi, i.tac_legacy, i.fac, i.snr, i.check_digit,
    r.first_seen,
    CASE WHEN r.is_present THEN (SELECT max(snapshot_date) FROM observations)
         ELSE r.last_seen END                                     AS last_seen,
    r.disappeared_at,
    (SELECT max(o.snapshot_date) FROM observations o
      WHERE o.snapshot_date < r.first_seen)                       AS first_seen_after,
    r.first_seen - (SELECT max(o.snapshot_date) FROM observations o
                     WHERE o.snapshot_date < r.first_seen)        AS first_seen_days,
    CASE WHEN r.disappeared_at IS NOT NULL
         THEN r.disappeared_at - r.last_seen END                  AS disappeared_days,
    r.is_present,
    r.revision_no, r.first_source, r.last_source
FROM records r
LEFT JOIN dict_ovd g ON g.ovd = r.ovd
LEFT JOIN dict_nz_mapping m ON m.nz_raw IS NOT DISTINCT FROM r.nz
LEFT JOIN imei_normalized i ON i.record_key = r.record_key
"""


ISSUES_SQL = """
CREATE OR REPLACE TABLE data_issues AS
SELECT record_key, 'nz' AS field, nz_raw AS raw_value, 'brand_unresolved' AS issue
FROM records_normalized WHERE brand IS NULL AND nz_method <> 'empty'
UNION ALL
SELECT record_key, 'nz', nz_raw, 'script_mixed'
FROM records_normalized WHERE homoglyph_fixed AND brand IS NULL
UNION ALL
SELECT record_key, 'brand', nz_raw, 'conflicts_with_tac'
FROM records_normalized WHERE brand_conflict
UNION ALL
SELECT record_key, 'imei', imei_raw, 'imei_unusable'
FROM records_normalized WHERE imei_norm IS NULL
UNION ALL
SELECT record_key, 'imei', imei_raw, 'imei_checksum_failed'
FROM records_normalized WHERE imei_norm IS NOT NULL AND NOT imei_luhn_valid
UNION ALL
SELECT record_key, 'insert_date', nz_raw, 'date_implausible'
FROM records_normalized WHERE insert_date_implausible
UNION ALL
SELECT record_key, 'model', nz_raw, 'model_missing'
FROM records_normalized WHERE brand IS NOT NULL AND model IS NULL
UNION ALL
SELECT record_key, 'ovd', ovd, 'region_unknown'
FROM records_normalized WHERE region IS NULL AND nullif(trim(ovd), '') IS NOT NULL
"""


CHANGES_SQL = """
CREATE OR REPLACE TABLE record_changes AS
SELECT record_key, 'nz' AS field, nz_raw AS value_before,
       coalesce(brand, '') || CASE WHEN model IS NULL THEN '' ELSE ' ' || model END
           AS value_after,
       nz_method AS reason
FROM records_normalized
WHERE brand IS NOT NULL
  AND upper(trim(nz_raw)) <> coalesce(brand, '') ||
      CASE WHEN model IS NULL THEN '' ELSE ' ' || model END
UNION ALL
SELECT record_key, 'imei', imei_raw, imei_norm,
       CASE WHEN imei_was_repaired THEN 'check_digit_restored' ELSE 'reformatted' END
FROM records_normalized
WHERE imei_norm IS NOT NULL AND imei_raw <> imei_norm
UNION ALL
SELECT record_key, 'imei', imei_raw, NULL, 'unusable_' || lower(imei_kind)
FROM records_normalized
WHERE imei_norm IS NULL
UNION ALL
SELECT record_key, 'insert_date', nz_raw, NULL, 'implausible_date_nulled'
FROM records_normalized
WHERE insert_date_implausible
"""


def run(
    con: duckdb.DuckDBPyConnection,
    workers: int = 0,
    external_tac: str = "",
    external_name: str = "external",
) -> dict[str, int]:
    """Rebuild all normalised outputs. Idempotent."""

    def stage(name: str, fn: Callable[[], T]) -> T:
        started = time.monotonic()
        log.info("normalize.stage.start", stage=name)
        result = fn()
        log.info("normalize.stage.done", stage=name, seconds=round(time.monotonic() - started, 1))

        return result

    added = stage("nz_map", lambda: build_nz_map(con, workers=workers))
    geo_rows = stage("geo", lambda: build_geo(con))
    stage("imei", lambda: con.execute(IMEI_SQL))
    stage("observations", lambda: con.execute(OBSERVATIONS_SQL))
    stage("records_normalized", lambda: con.execute(NORMALIZED_SQL))
    tac_rows = stage("tac_build", lambda: tac_dict.build(con))

    if external_tac:
        stage("tac_external", lambda: tac_dict.load_external(con, external_tac, external_name))
    tac_fill = stage("tac_fill", lambda: tac_dict.fill(con))
    stage("model_lines", lambda: con.execute(MODEL_LINES_SQL))
    stage("model_key", lambda: con.execute(MODEL_KEY_SQL))
    stage("record_changes", lambda: con.execute(CHANGES_SQL))
    stage("data_issues", lambda: con.execute(ISSUES_SQL))
    total = count(con, "SELECT count(*) FROM records_normalized")
    resolved = count(con, "SELECT count(*) FROM records_normalized WHERE brand IS NOT NULL")
    changes = count(con, "SELECT count(*) FROM record_changes")
    issues = count(con, "SELECT count(*) FROM data_issues")

    return {
        "nz_spellings_added": added,
        "ovd_values": geo_rows,
        "records": total,
        "brand_resolved": resolved,
        "changes_logged": changes,
        "issues_logged": issues,
        "tac_dict_rows": tac_rows,
        **tac_fill,
    }


__all__ = ["run", "build_nz_map", "resolve_nz"]
