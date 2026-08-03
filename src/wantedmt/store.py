"""Folds full snapshots into current state plus aggregates."""

from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import structlog

from .config import (
    DTL_PHONE_PATTERN,
    FIELDS,
    MAX_DISAPPEARED_SHARE,
    MAX_DISAPPEARED_SHARE_PER_DAY,
    MIN_SNAPSHOT_ROWS,
    PERSONAL_FIELDS,
)
from .sources import Revision
from .sql import count

log = structlog.get_logger()


class SnapshotTooSmall(ValueError):
    """The file arrived intact and is not the register. Distinct from a failure on purpose. A …"""


def _unlink_quietly(path: Path) -> None:
    """Delete a temporary file, and never let the deletion undo the work. This runs in a …"""

    for attempt in range(3):
        try:
            path.unlink(missing_ok=True)

            return
        except OSError:
            if attempt < 2:
                time.sleep(0.5)
    log.warning("temp.not_deleted", path=path.name)


REPAIRS: tuple[tuple[str, re.Pattern[bytes], bytes], ...] = (
    ("orphan_object_after_array", re.compile(rb"\]\s*\{"), b",{"),
    ("orphan_object_before_array", re.compile(rb"\}\s*\["), b"},"),
    ("concatenated_arrays", re.compile(rb"\]\s*\["), b","),
    ("empty_element", re.compile(rb"\},\s*(?:,\s*)+\{"), b"},{"),
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    source          VARCHAR NOT NULL,
    snapshot_date   DATE    NOT NULL,
    revision_slug   VARCHAR,
    url             VARCHAR,
    api_hash        VARCHAR,
    sha256          VARCHAR,
    bytes           BIGINT,
    record_count    INTEGER,
    appeared        INTEGER,
    changed         INTEGER,
    disappeared     INTEGER,
    is_suspect      BOOLEAN NOT NULL DEFAULT false,
    suspect_reason  VARCHAR,
    folded_at       TIMESTAMP DEFAULT now(),
    status          VARCHAR DEFAULT 'folded',
    rows_raw        INTEGER,
    rows_dupe_id    INTEGER,
    rows_repaired   INTEGER,
    rows_skipped    INTEGER,
    read_mode       VARCHAR,
    missing         INTEGER,
    gap_days        INTEGER,
    PRIMARY KEY (source, snapshot_date)
);

CREATE TABLE IF NOT EXISTS snapshot_duplicates (
    source        VARCHAR,
    snapshot_date DATE,
    hash_kind     VARCHAR,
    hash          VARCHAR,
    same_as_source VARCHAR,
    same_as_date  DATE,
    bytes         BIGINT,
    detected_at   TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS snapshot_aggregates (
    snapshot_date DATE    NOT NULL,
    source        VARCHAR NOT NULL,
    dimension     VARCHAR NOT NULL,
    key           VARCHAR,
    records       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    record_key      VARCHAR,
    ovd             VARCHAR,
    insert_date     VARCHAR,
    nz              VARCHAR,
    imei            VARCHAR,
    nk              VARCHAR,
    dk              VARCHAR,
    dtl             VARCHAR,
    extra_json      VARCHAR,
    content_hash    VARCHAR,
    first_seen      DATE,
    last_seen       DATE,
    disappeared_at  DATE,
    is_present      BOOLEAN,
    revision_no     INTEGER,
    disappeared_count INTEGER DEFAULT 0,
    returned_at     DATE,
    first_source    VARCHAR,
    last_source     VARCHAR
);
"""


SCHEMA_LOG = """
CREATE TABLE IF NOT EXISTS schema_variants (
    source        VARCHAR,
    snapshot_date DATE,
    fields        VARCHAR,
    extra_fields  VARCHAR,
    missing_fields VARCHAR,
    seen_at       TIMESTAMP DEFAULT now()
);
"""


_HASH_EXPR = "md5(" + " || '|' || ".join(f"coalesce({f}, '')" for f in FIELDS) + ")"


class Store:
    def __init__(self, path: Path, memory_limit: str = "8GB") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.con = duckdb.connect(str(path))
        self.con.execute(f"SET memory_limit='{memory_limit}'")
        self.con.execute("SET enable_external_file_cache=false")
        self.con.execute("SET enable_progress_bar=false")
        self.con.execute(SCHEMA)
        self.con.execute(SCHEMA_LOG)
        self._migrate()
        self.stats: dict[str, Any] = {}

    def _migrate(self) -> None:
        """Add columns a store built by an earlier version has not got. CREATE TABLE IF NOT …"""
        have = {row[0].lower() for row in self.con.execute("DESCRIBE snapshots").fetchall()}
        additions = {
            "status": "VARCHAR DEFAULT 'folded'",
            "rows_raw": "INTEGER",
            "rows_dupe_id": "INTEGER",
            "rows_repaired": "INTEGER",
            "rows_skipped": "INTEGER",
            "read_mode": "VARCHAR",
            "missing": "INTEGER",
            "gap_days": "INTEGER",
        }

        for name, ddl in additions.items():
            if name not in have:
                self.con.execute(f"ALTER TABLE snapshots ADD COLUMN {name} {ddl}")
        record_columns = {row[0].lower() for row in self.con.execute("DESCRIBE records").fetchall()}

        if "extra_json" not in record_columns:
            self.con.execute("ALTER TABLE records ADD COLUMN extra_json VARCHAR")

    def hash_seen(self, api_hash: str) -> tuple[str, str] | None:
        """The (source, date) of a folded snapshot with this API hash, if any. Checked before …"""

        if not api_hash:
            return None
        row = self.con.execute(
            "SELECT source, strftime(snapshot_date, '%Y-%m-%d') FROM snapshots "
            "WHERE api_hash = ? AND status = 'folded' ORDER BY snapshot_date LIMIT 1",
            [api_hash],
        ).fetchone()

        return (row[0], row[1]) if row else None

    def sha_seen(self, sha256: str) -> tuple[str, str] | None:
        """Same question after the fact, for the 77 revisions the API lists without a hash."""

        if not sha256:
            return None
        row = self.con.execute(
            "SELECT source, strftime(snapshot_date, '%Y-%m-%d') FROM snapshots "
            "WHERE sha256 = ? AND status = 'folded' ORDER BY snapshot_date LIMIT 1",
            [sha256],
        ).fetchone()

        return (row[0], row[1]) if row else None

    def record_duplicate(
        self,
        revision: Revision,
        kind: str,
        value: str,
        same_as: tuple[str, str],
        bytes_: int,
    ) -> None:
        self.con.execute(
            "INSERT INTO snapshot_duplicates (source, snapshot_date, hash_kind, "
            "hash, same_as_source, same_as_date, bytes) "
            "VALUES (?, CAST(? AS DATE), ?, ?, ?, CAST(? AS DATE), ?)",
            [revision.source, revision.snapshot_date, kind, value, same_as[0], same_as[1], bytes_],
        )
        self._record_outcome(
            revision, "duplicate", f"identical to {same_as[0]}/{same_as[1]} by {kind}"
        )

    def record_skip(self, revision: Revision, reason: str) -> None:
        self._record_outcome(revision, "skipped", reason)

    def record_failure(self, revision: Revision, reason: str) -> None:
        self._record_outcome(revision, "failed", reason)

    def _record_outcome(self, revision: Revision, status: str, reason: str) -> None:
        """Write a snapshot row for a revision that was not folded. Recorded rather than …"""
        self.con.execute(
            "INSERT INTO snapshots (source, snapshot_date, revision_slug, url, "
            "api_hash, bytes, status, suspect_reason, is_suspect) "
            "VALUES (?, CAST(? AS DATE), ?, ?, ?, ?, ?, ?, true) "
            "ON CONFLICT (source, snapshot_date) DO UPDATE SET "
            "status = excluded.status, suspect_reason = excluded.suspect_reason",
            [
                revision.source,
                revision.snapshot_date,
                revision.slug,
                revision.url,
                revision.api_hash,
                revision.size,
                status,
                reason,
            ],
        )

    def close(self) -> None:
        self.con.close()

    def checkpoint(self) -> int:
        """Flush and reclaim, returning the database's size on disk. Every fold rewrites …"""
        self.con.execute("CHECKPOINT")

        return self.path.stat().st_size if self.path.exists() else 0

    def folded(self) -> set[tuple[str, str]]:
        """Revisions a rerun should not attempt again. A failure is not among them: a dropped …"""
        rows = self.con.execute(
            "SELECT source, strftime(snapshot_date, '%Y-%m-%d') FROM snapshots "
            "WHERE coalesce(status, 'folded') <> 'failed'"
        ).fetchall()

        return {(r[0], r[1]) for r in rows}

    def last_snapshot(self) -> tuple[str, str] | None:
        """The newest snapshot that actually contributed state. Restricted to folded rows: a …"""
        row = self.con.execute(
            "SELECT source, strftime(snapshot_date, '%Y-%m-%d') FROM snapshots "
            "WHERE coalesce(status, 'folded') = 'folded' "
            "ORDER BY snapshot_date DESC, folded_at DESC LIMIT 1"
        ).fetchone()

        return (row[0], row[1]) if row else None

    @staticmethod
    def _read_sql(path: Path) -> str:
        """Read a snapshot without guessing at its types. `sample_size=-1` reads the whole …"""

        return (
            f"CREATE TEMP TABLE snap_raw AS SELECT * FROM "
            f"read_json('{path.as_posix()}', format='array', union_by_name=true, "
            f"maximum_object_size=33554432, sample_size=-1)"
        )

    def _read_json(self, json_path: Path) -> tuple[str, int]:
        """Get the file into `snap_raw`, repairing it only if it will not parse. The defect …"""

        try:
            self.con.execute(self._read_sql(json_path))

            return "direct", 0
        except duckdb.Error as first:
            log.warning("snapshot.unparsable", path=json_path.name, error=str(first)[:200])
        raw = json_path.read_bytes()
        applied: dict[str, int] = {}

        for name, pattern, replacement in REPAIRS:
            raw, hits = pattern.subn(replacement, raw)

            if hits:
                applied[name] = hits

        if not applied:
            raise ValueError(f"{json_path.name}: will not parse and holds no repairable defect")
        fixed = json_path.with_suffix(".fixed.json")
        fixed.write_bytes(raw)

        try:
            self.con.execute(self._read_sql(fixed))
        finally:
            _unlink_quietly(fixed)
        log.info("snapshot.repaired", path=json_path.name, **applied)

        return "repaired", sum(applied.values())

    def _dtl_keys(self, column: str) -> list[str]:
        """The sub-fields of DTL, when the file ships it as structured data. In the current …"""
        row = self.con.execute(
            f'SELECT json_keys(to_json("{column}"[1])) FROM snap_raw '
            f'WHERE "{column}" IS NOT NULL AND len("{column}") > 0 LIMIT 1'
        ).fetchone()

        return list(row[0]) if row and row[0] else []

    def _nested_personal(self, present: dict[str, str]) -> list[str]:
        if "dtl" not in present:
            return []

        try:
            keys = self._dtl_keys(present["dtl"])
        except duckdb.Error:
            return []

        return [k for k in keys if f"dtl.{k.lower()}" in PERSONAL_FIELDS]

    def _dtl_expression(self, present: dict[str, str], nested: list[str]) -> str:
        """DTL as it will be stored: every sub-field except the personal ones. Rebuilt rather …"""
        column = present["dtl"]

        if not nested:
            return (
                f'CASE WHEN regexp_matches(trim(CAST("{column}" AS VARCHAR)), '
                f"'{DTL_PHONE_PATTERN}') THEN NULL "
                f'ELSE CAST("{column}" AS VARCHAR) END AS dtl'
            )
        keys = [k for k in self._dtl_keys(column) if k not in nested]

        if not keys:
            return "NULL AS dtl"
        packed = ", ".join(f'"{k}" := element."{k}"' for k in keys)

        return (
            f'CAST(to_json(list_transform("{column}", '
            f"element -> struct_pack({packed}))) AS VARCHAR) AS dtl"
        )

    def _stage(self, json_path: Path, revision: Revision) -> int:
        """Load a snapshot whatever shape it arrives in. The published schema drifted: …"""
        self.con.execute("DROP TABLE IF EXISTS snap_raw")
        read_mode, repaired = self._read_json(json_path)
        self.stats = {"read_mode": read_mode, "rows_repaired": repaired}
        present = {
            row[0].lower(): row[0] for row in self.con.execute("DESCRIBE snap_raw").fetchall()
        }
        key = present.get("id", "ID")
        personal = sorted(f for f in present if f in PERSONAL_FIELDS)
        nested = self._nested_personal(present)
        bare_phone = "dtl" in present and not nested

        if personal or nested or bare_phone:
            self.con.execute("""
                CREATE TABLE IF NOT EXISTS quarantine_personal (
                    record_key VARCHAR, snapshot_date DATE, field VARCHAR, value VARCHAR
                )
            """)

            for field in personal:
                self.con.execute(f"""
                    INSERT INTO quarantine_personal
                    SELECT n.record_key, DATE '{revision.snapshot_date}', n.field, n.value
                    FROM (SELECT DISTINCT CAST("{key}" AS VARCHAR) AS record_key,
                                 '{field}' AS field,
                                 CAST("{present[field]}" AS VARCHAR) AS value
                          FROM snap_raw WHERE "{present[field]}" IS NOT NULL) n
                    WHERE NOT EXISTS (
                        SELECT 1 FROM quarantine_personal q
                        WHERE q.record_key = n.record_key AND q.field = n.field
                          AND q.value IS NOT DISTINCT FROM n.value)
                """)

            for field in nested:
                self.con.execute(f"""
                    INSERT INTO quarantine_personal
                    SELECT n.record_key, DATE '{revision.snapshot_date}', n.field, n.value
                    FROM (SELECT DISTINCT record_key, 'dtl.{field}' AS field,
                                 CAST(element."{field}" AS VARCHAR) AS value
                          FROM (SELECT CAST("{key}" AS VARCHAR) AS record_key,
                                       unnest("{present["dtl"]}") AS element
                                FROM snap_raw)
                          WHERE element."{field}" IS NOT NULL) n
                    WHERE NOT EXISTS (
                        SELECT 1 FROM quarantine_personal q
                        WHERE q.record_key = n.record_key AND q.field = n.field
                          AND q.value IS NOT DISTINCT FROM n.value)
                """)

            if bare_phone:
                self.con.execute(f"""
                    INSERT INTO quarantine_personal
                    SELECT n.record_key, DATE '{revision.snapshot_date}', 'dtl', n.value
                    FROM (SELECT DISTINCT CAST("{key}" AS VARCHAR) AS record_key,
                                 trim(CAST("{present["dtl"]}" AS VARCHAR)) AS value
                          FROM snap_raw
                          WHERE regexp_matches(
                              trim(CAST("{present["dtl"]}" AS VARCHAR)),
                              '{DTL_PHONE_PATTERN}')) n
                    WHERE NOT EXISTS (
                        SELECT 1 FROM quarantine_personal q
                        WHERE q.record_key = n.record_key AND q.field = 'dtl'
                          AND q.value IS NOT DISTINCT FROM n.value)
                """)
            named = personal + [f"dtl.{f}" for f in nested] + (["dtl"] if bare_phone else [])
            log.info(
                "schema.personal_quarantined", date=revision.snapshot_date, fields=",".join(named)
            )
        extra = sorted(set(present) - set(FIELDS) - PERSONAL_FIELDS)
        missing = sorted(set(FIELDS) - set(present))
        self.con.execute(
            "INSERT INTO schema_variants (source, snapshot_date, fields, "
            "extra_fields, missing_fields) VALUES (?, CAST(? AS DATE), ?, ?, ?)",
            [
                revision.source,
                revision.snapshot_date,
                ",".join(sorted(present)),
                ",".join(extra),
                ",".join(missing),
            ],
        )

        if extra or missing:
            log.info(
                "schema.drift",
                date=revision.snapshot_date,
                extra=",".join(extra) or "-",
                missing=",".join(missing) or "-",
            )
        select = ", ".join(
            (
                self._dtl_expression(present, nested)
                if f == "dtl" and f in present
                else f'CAST("{present[f]}" AS VARCHAR) AS {f}'
            )
            if f in present
            else f"NULL AS {f}"
            for f in FIELDS
        )
        extras = (
            "CAST(to_json(struct_pack("
            + ", ".join(f'"{present[e]}"' for e in extra)
            + ")) AS VARCHAR)"
            if extra
            else "CAST(NULL AS VARCHAR)"
        )
        rows_raw = count(self.con, "SELECT count(*) FROM snap_raw")
        self.con.execute("DROP TABLE IF EXISTS snap")
        self.con.execute(f"""
            CREATE TEMP TABLE snap AS
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, {_HASH_EXPR} AS content_hash,
                       row_number() OVER (PARTITION BY id) AS rn
                FROM (SELECT {select}, {extras} AS extra_json FROM snap_raw)
            ) WHERE rn = 1
        """)
        self.con.execute("DROP TABLE IF EXISTS snap_raw")
        staged = count(self.con, "SELECT count(*) FROM snap")
        self.stats.update(rows_raw=rows_raw, rows_dupe_id=rows_raw - staged, rows_skipped=0)

        return staged

    def _apply_delta(
        self,
        day: str,
        source: str,
        same_series: bool,
        is_suspect: bool,
        previous: tuple[str, str] | None,
    ) -> None:
        """Move the store forward by what changed, not by what was published. Consecutive …"""
        columns = ", ".join(FIELDS[1:])
        self.con.execute(f"""
            INSERT INTO records (record_key, {columns}, extra_json, content_hash,
                                 first_seen, last_seen, is_present, revision_no,
                                 disappeared_count, first_source, last_source)
            SELECT s.id, {", ".join(f"s.{f}" for f in FIELDS[1:])}, s.extra_json,
                   s.content_hash, DATE '{day}', NULL, true, 1, 0, '{source}', '{source}'
            FROM snap s LEFT JOIN records r ON r.record_key = s.id
            WHERE r.record_key IS NULL
        """)
        assignments = ", ".join(f"{f} = s.{f}" for f in FIELDS[1:])
        bump = "revision_no + 1" if same_series else "revision_no"
        self.con.execute(f"""
            UPDATE records r SET {assignments},
                extra_json = coalesce(s.extra_json, r.extra_json),
                content_hash = s.content_hash,
                revision_no = {bump},
                last_source = '{source}',
                last_seen = NULL,
                disappeared_at = NULL,
                is_present = true,
                returned_at = CASE WHEN r.is_present THEN r.returned_at ELSE DATE '{day}' END
            FROM snap s
            WHERE s.id = r.record_key AND r.content_hash <> s.content_hash
        """)
        self.con.execute(f"""
            UPDATE records r SET is_present = true, returned_at = DATE '{day}',
                                 disappeared_at = NULL, last_seen = NULL,
                                 last_source = '{source}'
            FROM snap s
            WHERE s.id = r.record_key AND r.is_present = false
        """)

        if not is_suspect:
            last_present = previous[1] if previous else day
            self.con.execute(f"""
                UPDATE records SET is_present = false,
                    disappeared_at = DATE '{day}',
                    last_seen = DATE '{last_present}',
                    disappeared_count = coalesce(disappeared_count, 0) + 1
                WHERE is_present
                  AND NOT EXISTS (SELECT 1 FROM snap s WHERE s.id = record_key)
            """)

    def _write_aggregates(self, day: str, source: str) -> None:
        self.con.execute(
            "DELETE FROM snapshot_aggregates WHERE snapshot_date = CAST(? AS DATE) AND source = ?",
            [day, source],
        )
        self.con.execute(f"""
            INSERT INTO snapshot_aggregates
            SELECT DATE '{day}', '{source}', 'total', NULL, count(*) FROM records WHERE is_present
            UNION ALL
            SELECT DATE '{day}', '{source}', 'closed_cumulative', NULL, count(*)
              FROM records WHERE NOT is_present
            UNION ALL
            SELECT DATE '{day}', '{source}', 'registered_year',
                   left(insert_date, 4), count(*)
              FROM records WHERE is_present AND insert_date >= '2004' GROUP BY 4
            UNION ALL
            SELECT DATE '{day}', '{source}', 'ovd', ovd, count(*)
              FROM records WHERE is_present AND nullif(trim(ovd), '') IS NOT NULL GROUP BY 4
        """)

    def _write_history(self, history_dir: Path, revision: Revision, is_suspect: bool) -> int:
        """Append this snapshot's changes to the durable parquet history. The store keeps only …"""
        year, month, _ = revision.snapshot_date.split("-")
        out_dir = history_dir / year / month
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{revision.source}_{revision.snapshot_date}.parquet"
        columns = ", ".join(f"s.{f}" for f in FIELDS)
        self.con.execute(f"""
            COPY (
                SELECT DATE '{revision.snapshot_date}' AS snapshot_date,
                       '{revision.source}' AS source,
                       CASE WHEN r.record_key IS NULL THEN 'appeared'
                            ELSE 'changed' END AS event,
                       {columns}, s.extra_json, s.content_hash
                FROM snap s LEFT JOIN records r ON r.record_key = s.id
                WHERE r.record_key IS NULL OR r.content_hash <> s.content_hash
                UNION ALL
                SELECT DATE '{revision.snapshot_date}', '{revision.source}',
                       {"'absent'" if is_suspect else "'disappeared'"},
                       r.record_key, r.ovd, r.insert_date, r.nz, r.imei, r.nk,
                       r.dk, r.dtl, r.extra_json, r.content_hash
                FROM records r
                WHERE r.is_present
                  AND NOT EXISTS (SELECT 1 FROM snap s WHERE s.id = r.record_key)
            ) TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION zstd)
        """)

        return out.stat().st_size if out.exists() else 0

    def fold(
        self,
        json_path: Path,
        revision: Revision,
        meta: dict[str, Any],
        history_dir: Path | None = None,
    ) -> dict[str, Any]:
        staged = self._stage(json_path, revision)

        if staged < MIN_SNAPSHOT_ROWS:
            raise SnapshotTooSmall(
                f"{revision.source}/{revision.snapshot_date}: {staged:,} records "
                f"(floor {MIN_SNAPSHOT_ROWS:,})"
            )
        day = revision.snapshot_date
        previous = self.last_snapshot()

        if previous is not None and day < previous[1]:
            raise ValueError(
                f"{revision.source}/{day}: out of order — the store already holds "
                f"{previous[0]}/{previous[1]}. Folding backwards would overwrite "
                f"current values with older ones; rebuild from this date instead."
            )
        gap_days = (
            (date.fromisoformat(day) - date.fromisoformat(previous[1])).days if previous else 0
        )
        same_series = previous is not None and previous[0] == revision.source
        present_before = count(self.con, "SELECT count(*) FROM records WHERE is_present")
        appeared = count(
            self.con,
            "SELECT count(*) FROM snap s LEFT JOIN records r ON r.record_key = s.id "
            "WHERE r.record_key IS NULL",
        )
        changed = count(
            self.con,
            "SELECT count(*) FROM snap s JOIN records r ON r.record_key = s.id "
            "WHERE r.content_hash <> s.content_hash",
        )
        missing = count(
            self.con,
            "SELECT count(*) FROM records r WHERE r.is_present "
            "AND NOT EXISTS (SELECT 1 FROM snap s WHERE s.id = r.record_key)",
        )
        allowance = min(
            MAX_DISAPPEARED_SHARE,
            MAX_DISAPPEARED_SHARE_PER_DAY * max(1, gap_days),
        )
        suspect_reason = None

        if not same_series and previous is not None:
            suspect_reason = f"source handover {previous[0]}->{revision.source}"
        elif present_before and missing > present_before * allowance:
            suspect_reason = (
                f"{missing:,} of {present_before:,} records absent "
                f"({missing / present_before:.1%} > {allowance:.0%} allowed "
                f"over {gap_days} d)"
            )
        is_suspect = suspect_reason is not None

        if history_dir is not None:
            self._write_history(history_dir, revision, is_suspect)
        self._apply_delta(day, revision.source, same_series, is_suspect, previous)
        disappeared = 0 if is_suspect else missing
        self._write_aggregates(day, revision.source)
        self.con.execute(
            """
            INSERT INTO snapshots (source, snapshot_date, revision_slug, url, api_hash,
                                   sha256, bytes, record_count, appeared, changed,
                                   disappeared, is_suspect, suspect_reason, status,
                                   rows_raw, rows_dupe_id, rows_repaired, rows_skipped,
                                   read_mode, missing, gap_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'folded', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, snapshot_date) DO UPDATE SET
                record_count = excluded.record_count, appeared = excluded.appeared,
                changed = excluded.changed, disappeared = excluded.disappeared,
                is_suspect = excluded.is_suspect, suspect_reason = excluded.suspect_reason,
                status = excluded.status, rows_raw = excluded.rows_raw,
                rows_dupe_id = excluded.rows_dupe_id, rows_repaired = excluded.rows_repaired,
                rows_skipped = excluded.rows_skipped, read_mode = excluded.read_mode,
                missing = excluded.missing, gap_days = excluded.gap_days,
                sha256 = excluded.sha256, bytes = excluded.bytes
            """,
            [
                revision.source,
                day,
                revision.slug,
                revision.url,
                revision.api_hash,
                meta.get("sha256"),
                meta.get("bytes"),
                staged,
                appeared,
                changed,
                disappeared,
                is_suspect,
                suspect_reason,
                self.stats.get("rows_raw"),
                self.stats.get("rows_dupe_id"),
                self.stats.get("rows_repaired"),
                self.stats.get("rows_skipped"),
                self.stats.get("read_mode"),
                missing,
                gap_days,
            ],
        )
        self.con.execute("DROP TABLE IF EXISTS snap")

        return {
            "snapshot_date": day,
            "source": revision.source,
            "records": staged,
            "appeared": appeared,
            "changed": changed,
            "missing": missing,
            "disappeared": disappeared,
            "is_suspect": is_suspect,
            "suspect_reason": suspect_reason,
            "gap_days": gap_days,
            **self.stats,
        }
