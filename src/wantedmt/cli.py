"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import duckdb
import httpx
import structlog

from . import backfill, dq, notify, publish, reports
from .config import DEFAULT_DB, DEFAULT_WORK_DIR, DTL_PHONE_PATTERN, SOURCES
from .normalize import apply as normalize_apply
from .sources import build_timeline, dump_timeline, fetch_revisions, is_ahead, load_timeline
from .store import Store

log = structlog.get_logger()


NOTHING_NEW = 3


def _setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)

        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def cmd_timeline(args: argparse.Namespace) -> int:
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        revisions = build_timeline(client)
    dump_timeline(revisions, args.out)
    by_source: dict[str, int] = {}

    for rev in revisions:
        by_source[rev.source] = by_source.get(rev.source, 0) + 1
    log.info(
        "timeline",
        snapshots=len(revisions),
        first=revisions[0].snapshot_date if revisions else None,
        last=revisions[-1].snapshot_date if revisions else None,
        **by_source,
        out=args.out,
    )

    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    revisions = load_timeline(args.timeline) if Path(args.timeline).exists() else build_timeline()
    store = Store(Path(args.db))

    try:
        summary = backfill.run(
            store,
            revisions,
            work_dir=Path(args.work_dir),
            lookahead=args.lookahead,
            limit=args.limit,
            history_dir=Path(args.history) if args.history else None,
            stop_on_failure=not args.keep_going,
        )
    finally:
        store.close()

    return 0 if summary["failed"] == 0 else 1


def cmd_daily(args: argparse.Namespace) -> int:
    """Fold only what is new — the shape this runs in every day."""
    source = SOURCES[args.source]
    revisions = fetch_revisions(source)

    if not revisions:
        log.error("daily.no_revisions", source=args.source)

        return 1
    store = Store(Path(args.db))

    try:
        summary = backfill.run(
            store,
            revisions[-args.recent :],
            work_dir=Path(args.work_dir),
            lookahead=1,
            history_dir=Path(args.history) if args.history else None,
        )
    finally:
        store.close()

    if summary["failed"]:
        return 1

    return 0 if summary["folded"] else NOTHING_NEW


def _step_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")

    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def cmd_watch(args: argparse.Namespace) -> int:
    """Has the portal published a day we do not hold? Costs one API call and no download.

    Exit 0 means there is something to fold, NOTHING_NEW means the schedule fired
    before the publisher did — the ordinary outcome for most of the runs in a day.
    """
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    held = manifest.get("as_of")
    revisions = fetch_revisions(SOURCES[args.source])

    if not revisions:
        log.error("watch.no_revisions", source=args.source)

        return 1
    latest = revisions[-1]
    ahead = is_ahead(held, latest.snapshot_date)
    log.info(
        "watch",
        source=args.source,
        held=held,
        published=latest.snapshot_date,
        published_at=latest.created,
        megabytes=round(latest.size / 1e6, 1),
        new=ahead,
    )
    _step_summary(
        f"**{args.source}** — portal at `{latest.snapshot_date}` "
        f"(published {latest.created} UTC, {latest.size / 1e6:,.0f} MB), "
        f"store at `{held}` → {'new data' if ahead else 'nothing new'}"
    )

    return 0 if ahead else NOTHING_NEW


def cmd_normalize(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))

    try:
        result = normalize_apply.run(
            store.con,
            workers=args.workers,
            external_tac=args.external_tac,
            external_name=args.external_name,
        )
        log.info("normalized", **result)
    finally:
        store.close()

    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))

    try:
        for metric, value in reports.quality(store.con).items():
            print(f"{metric:28} {value:>12,}")
    finally:
        store.close()

    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    """Write the one small file a consumer has to read to know anything changed. Polling a 65 …"""
    import hashlib
    import json as jsonlib

    store = Store(Path(args.db))

    try:
        counts = reports.quality(store.con)
        as_of = store.con.execute(
            "SELECT max(snapshot_date) FROM snapshots WHERE coalesce(status, 'folded') = 'folded'"
        ).fetchone()
        observations = store.con.execute(
            "SELECT count(*) FROM snapshots WHERE coalesce(status, 'folded') = 'folded'"
        ).fetchone()
    finally:
        store.close()
    artefacts: dict[str, dict[str, object]] = {}
    export = Path(args.export)

    for path in sorted(export.glob("*")) if export.exists() else []:
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            artefacts[path.name] = {"bytes": path.stat().st_size, "sha256": digest}
    total = counts.get("records_total") or 1
    payload = {
        "dataset": "ua-stolen-phones-data-quality",
        "as_of": str(as_of[0]) if as_of and as_of[0] else None,
        "observations": int(observations[0]) if observations else 0,
        "records": {
            "total": counts.get("records_total"),
            "present": counts.get("records_present"),
            "closed": counts.get("records_disappeared"),
        },
        "coverage": {
            "brand": round((counts.get("brand_resolved") or 0) / total, 4),
            "model": round((counts.get("model_resolved") or 0) / total, 4),
            "imei_luhn_valid": round((counts.get("imei_luhn_valid") or 0) / total, 4),
        },
        "artefacts": artefacts,
    }
    out = Path(args.out)
    out.write_text(jsonlib.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("manifest.written", path=str(out), as_of=payload["as_of"])

    return 0


def cmd_volume(args: argparse.Namespace) -> int:
    """What the series weighed — the figures the methodology quotes."""
    store = Store(Path(args.db))

    try:
        figures = reports.volume(
            store.con,
            {
                "store": Path(args.db),
                "history": Path(args.history),
                "export": Path(args.export),
            },
        )
    finally:
        store.close()

    def gb(value: float | None) -> str:
        return f"{int(value or 0) / 1e9:,.2f} GB"

    print(f"{'revisions in the series':32} {figures['revisions']:>14,}")

    for key in ("folded", "duplicate", "skipped", "failed", "files_repaired"):
        print(f"{key:32} {figures[key]:>14,}")
    print()
    print(f"{'published by the portal':32} {gb(figures['bytes_published']):>14}")
    print(f"{'downloaded':32} {gb(figures['bytes_downloaded']):>14}")
    print(f"{'not fetched (hash already seen)':32} {gb(figures['bytes_not_fetched']):>14}")
    print(f"{'kept on disk':32} {gb(figures['kept_bytes']):>14}")
    share = figures["kept_share_of_published"]

    if share:
        print(f"{'kept / published':32} {share:>14.4%}")
    print()

    for key in ("rows_read", "rows_dropped_dupe_id", "rows_repaired"):
        print(f"{key:32} {int(figures[key] or 0):>14,}")
    print(f"{'covering':32} {str(figures['first_date']):>14} .. {figures['last_date']}")

    return 0


def cmd_unmatched(args: argparse.Namespace) -> int:
    """The review queue, written in both languages so neither can quietly go stale."""
    out = Path(args.out) if args.out else None
    english = out
    ukrainian = out.with_suffix(".uk.md") if out else None
    store = Store(Path(args.db))

    try:
        markdown = reports.weekly_review(
            store.con, args.limit, "en", ukrainian.name if ukrainian else ""
        )
        markdown_uk = reports.weekly_review(
            store.con, args.limit, "uk", english.name if english else ""
        )
    finally:
        store.close()

    if english is None or ukrainian is None:
        sys.stdout.write(markdown)

        return 0
    english.parent.mkdir(parents=True, exist_ok=True)

    for path, body in ((english, markdown), (ukrainian, markdown_uk)):
        path.write_text(body, encoding="utf-8")
        log.info("unmatched.written", out=str(path))

    return 0


def cmd_dq(args: argparse.Namespace) -> int:
    """Run the YAML-declared checks, render the report, optionally publish it."""
    store = Store(Path(args.db))

    try:
        report = dq.run(store.con, Path(args.config))
        headline = reports.quality(store.con)
    finally:
        store.close()
    source_note = publish.source_note()
    html = publish.to_html(report, source_note, headline)
    report_date = report.generated_at[:10]

    for path in publish.write_local(Path(args.out), report, html):
        log.info("dq.written", path=str(path))

    if args.publish:
        cfg = publish.R2Config.from_env()

        if cfg is None:
            log.error("dq.publish.skipped", reason="R2_* environment variables are not set")

            return 1
        markdown = dq.to_markdown(report, "en", "latest.uk.md")
        markdown_uk = dq.to_markdown(report, "uk", "latest.md")

        for url in publish.upload(cfg, report_date, html, markdown, markdown_uk):
            log.info("dq.published", url=url)

    if report.blocking:
        notify.blocking_failures([(r.human_en, r.failed) for r in report.blocking])
    unresolved = (
        [(raw, count) for raw, _clean, count in reports.unmatched(Store(Path(args.db)).con, 8)]
        if args.notify_review
        else []
    )

    if unresolved and sum(c for _, c in unresolved) >= notify.REVIEW_THRESHOLD:
        notify.review_queue(sum(c for _, c in unresolved), unresolved)
    log.info(
        "dq.summary",
        status=report.status,
        rows=f"{report.rows:,}",
        failed=len(report.failures),
        blocking=len(report.blocking),
    )

    return 1 if report.blocking else 0


def _phone_shaped(con: duckdb.DuckDBPyConnection, catalog: str) -> dict[str, int]:
    """Columns that could carry a subscriber number, and how many do. Only the fields DTL is …"""
    columns = con.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_catalog = ? AND data_type = 'VARCHAR' "
        "AND (lower(column_name) LIKE '%dtl%' OR lower(column_name) LIKE '%nomer%' "
        "OR lower(column_name) LIKE '%phone%' OR lower(column_name) LIKE '%msisdn%')",
        [catalog],
    ).fetchall()
    found: dict[str, int] = {}

    for table, column in columns:
        hits = con.execute(
            f"SELECT count(*) FROM {catalog}.{table} "
            f"WHERE regexp_matches(coalesce({column}, ''), ?)",
            [DTL_PHONE_PATTERN],
        ).fetchone()

        if hits and int(hits[0]):
            found[f"{table}.{column}"] = int(hits[0])

    return found


def cmd_state(args: argparse.Namespace) -> int:
    """Write the copy of the store that is allowed to leave this machine.

    Two copies rather than one, and deliberately: dropping the quarantine table
    from a file that already held it frees the blocks without overwriting them,
    so the numbers would still be readable in the raw bytes of a release asset.
    The published file is built from a source that never contained them.
    """
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_suffix(".staging.duckdb")

    for path in (out, staging):
        for leftover in (path, path.with_suffix(path.suffix + ".wal")):
            leftover.unlink(missing_ok=True)
    con = duckdb.connect(":memory:", config={"enable_external_file_cache": "false"})

    try:
        con.execute(f"ATTACH '{Path(args.db).as_posix()}' AS source (READ_ONLY)")
        con.execute(f"ATTACH '{staging.as_posix()}' AS staging")
        con.execute("COPY FROM DATABASE source TO staging")
        dropped = [
            name
            for (name,) in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = 'staging' AND table_name LIKE 'quarantine%'"
            ).fetchall()
        ]

        for name in dropped:
            con.execute(f"DROP TABLE staging.{name}")
        con.execute("CHECKPOINT staging")
        con.execute("DETACH source")
        con.execute("DETACH staging")
        con.execute(f"ATTACH '{staging.as_posix()}' AS staging (READ_ONLY)")
        con.execute(f"ATTACH '{out.as_posix()}' AS published")
        con.execute("COPY FROM DATABASE staging TO published")
        con.execute("CHECKPOINT published")
        carried = _phone_shaped(con, "published")
    finally:
        con.close()

    if carried:
        out.unlink(missing_ok=True)
        staging.unlink(missing_ok=True)
        log.error("state.refused", carrying=carried)

        raise SystemExit(
            f"refusing to write {out}: {carried} still hold phone-shaped values. "
            f"The fold routes those to quarantine; a state file that carries them "
            f"would publish subscriber numbers the moment the repository is public."
        )
    staging.unlink(missing_ok=True)
    staging.with_suffix(staging.suffix + ".wal").unlink(missing_ok=True)
    log.info(
        "state.written",
        path=str(out),
        megabytes=round(out.stat().st_size / 1e6, 1),
        withheld=",".join(dropped) or None,
    )

    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Write the publishable artefacts. Raw snapshots are never among them."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    store = Store(Path(args.db))

    try:
        store.con.execute(
            f"COPY records_normalized TO '{(out / 'records.parquet').as_posix()}' "
            "(FORMAT PARQUET, COMPRESSION zstd)"
        )
        store.con.execute(
            "COPY (SELECT *, coalesce(status, 'folded') = 'folded' AND NOT is_suspect "
            "AS content_is_new FROM snapshots ORDER BY snapshot_date) TO "
            f"'{(out / 'snapshots.csv').as_posix()}' (HEADER, DELIMITER ',')"
        )
        store.con.execute(
            "COPY (SELECT tac, brand, manufacturer, model, records, purity "
            "FROM dict_tac WHERE source = 'register') TO "
            f"'{(out / 'dict_tac.csv').as_posix()}' (HEADER, DELIMITER ',')"
        )
        store.con.execute(
            f"COPY record_changes TO '{(out / 'record_changes.parquet').as_posix()}' "
            "(FORMAT PARQUET, COMPRESSION zstd)"
        )

        for name in reports.AGGREGATES:
            store.con.execute(
                f"COPY ({reports.AGGREGATES[name]}) TO "
                f"'{(out / f'agg_{name}.csv').as_posix()}' (HEADER, DELIMITER ',')"
            )
        log.info("exported", out=str(out))
    finally:
        store.close()

    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="wantedmt", description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("timeline", help="discover all revisions of both sources")
    p.add_argument("--out", default="data/timeline.json")
    p.set_defaults(func=cmd_timeline)
    p = sub.add_parser("backfill", help="fold every pending snapshot, oldest first")
    p.add_argument("--timeline", default="data/timeline.json")
    p.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    p.add_argument(
        "--lookahead", type=int, default=0, help="parallel downloads (0 = cores minus two)"
    )
    p.add_argument("--limit", type=int, default=0)
    p.add_argument(
        "--history", default="", help="also write per-snapshot changes as parquet to this directory"
    )
    p.add_argument(
        "--keep-going",
        action="store_true",
        help="carry on past a snapshot that cannot be folded "
        "(leaves a hole; use only to survey the damage)",
    )
    p.set_defaults(func=cmd_backfill)
    p = sub.add_parser("daily", help="fold the newest revisions of one source")
    p.add_argument("--source", default="npu", choices=sorted(SOURCES))
    p.add_argument("--recent", type=int, default=3)
    p.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    p.add_argument("--history", default="", help="also write per-snapshot changes as parquet here")
    p.set_defaults(func=cmd_daily)
    p = sub.add_parser("watch", help="ask the portal whether it holds a day we do not")
    p.add_argument("--source", default="npu", choices=sorted(SOURCES))
    p.add_argument("--manifest", default="manifest.json")
    p.set_defaults(func=cmd_watch)
    p = sub.add_parser("normalize", help="rebuild IMEI and brand/model outputs")
    p.add_argument(
        "--workers", type=int, default=0, help="processes for spelling resolution (0 = auto)"
    )
    p.add_argument(
        "--external-tac",
        default="",
        help="CSV (Brand,TAC,SPECS) used to label TACs the register cannot",
    )
    p.add_argument(
        "--external-name",
        default="external",
        help="provenance label recorded for rows from that file",
    )
    p.set_defaults(func=cmd_normalize)
    p = sub.add_parser("report", help="print quality metrics")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("manifest", help="write manifest.json: as-of date, counts, checksums")
    p.add_argument("--out", default="manifest.json")
    p.add_argument("--export", default="data/export")
    p.set_defaults(func=cmd_manifest)
    p = sub.add_parser("volume", help="what the series weighed: published, moved, kept")
    p.add_argument("--history", default="data/history")
    p.add_argument("--export", default="data/export")
    p.set_defaults(func=cmd_volume)
    p = sub.add_parser("unmatched", help="weekly review of unresolved NZ spellings")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_unmatched)
    p = sub.add_parser("dq", help="run data-quality checks and render the report")
    p.add_argument("--config", default="dq/checks.yml")
    p.add_argument("--out", default="dq/reports")
    p.add_argument("--publish", action="store_true", help="upload the report to R2")
    p.add_argument(
        "--notify-review",
        action="store_true",
        help="also post the manual-review queue to the ops group",
    )
    p.set_defaults(func=cmd_dq)
    p = sub.add_parser("state", help="write the shareable copy of the store")
    p.add_argument("--out", default="data/state.duckdb")
    p.set_defaults(func=cmd_state)
    p = sub.add_parser("export", help="write publishable parquet/csv artefacts")
    p.add_argument("--out", default="data/export")
    p.set_defaults(func=cmd_export)
    args = parser.parse_args(argv)

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
