"""Chronological backfill runner."""

from __future__ import annotations

import json
import os
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import httpx
import structlog

from .config import DEFAULT_WORK_DIR
from .download import download
from .sources import Revision
from .store import SnapshotTooSmall, Store

log = structlog.get_logger()


USER_AGENT = (
    "ua-stolen-phones-data-quality/0.1 "
    "(+https://github.com/mykhailoklimnyk/ua-stolen-phones-data-quality)"
)


PROGRESS_FILE = Path("data/progress.json")


DEFAULT_LOOKAHEAD = min(16, max(8, (os.cpu_count() or 8) - 2))


CHECKPOINT_EVERY = 25


def _write_progress(**fields: object) -> None:
    """A file rather than only log lines, so 'how far along is it?' is one read instead of …"""

    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROGRESS_FILE.write_text(json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _keep_for_diagnosis(path: Path, failed_dir: Path) -> None:
    """Move a snapshot that would not fold somewhere it will survive the run."""

    if not path.exists():
        return

    try:
        failed_dir.mkdir(parents=True, exist_ok=True)
        path.replace(failed_dir / path.name)
        log.info("snapshot.kept_for_diagnosis", path=str(failed_dir / path.name))
    except OSError as exc:
        log.warning("snapshot.not_kept", path=path.name, error=str(exc))


def _discard(path: Path) -> None:
    """Delete a folded snapshot, and never fail the run over it. This is a `finally`, so an …"""

    for attempt in range(3):
        try:
            path.unlink(missing_ok=True)

            return
        except OSError:
            if attempt < 2:
                time.sleep(0.5)
    log.warning("snapshot.not_deleted", path=path.name)


def _size(byte_count: int) -> str:
    return f"{byte_count / 1e9:.1f} GB" if byte_count >= 1e9 else f"{byte_count / 1e6:.0f} MB"


def _human(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"

    if seconds < 5400:
        return f"{seconds / 60:.0f}m"

    return f"{seconds / 3600:.1f}h"


def run(
    store: Store,
    revisions: list[Revision],
    work_dir: Path = DEFAULT_WORK_DIR,
    lookahead: int = 0,
    limit: int = 0,
    history_dir: Path | None = None,
    stop_on_failure: bool = True,
) -> dict[str, int]:
    """Fold every pending revision. Returns a summary."""
    lookahead = lookahead or DEFAULT_LOOKAHEAD
    done = store.folded()
    pending = [r for r in revisions if (r.source, r.snapshot_date) not in done]
    frontier = store.last_snapshot()

    if frontier is not None:
        behind = [r for r in pending if r.snapshot_date < frontier[1]]

        if behind:
            log.warning(
                "backfill.left_behind",
                count=len(behind),
                frontier=frontier[1],
                dates=",".join(r.snapshot_date for r in behind[:10]),
            )
        pending = [r for r in pending if r.snapshot_date >= frontier[1]]

    if limit:
        pending = pending[:limit]
    log.info("backfill.start", pending=len(pending), already=len(done), lookahead=lookahead)

    if not pending:
        return {"folded": 0, "failed": 0, "skipped": len(done), "duplicate": 0}
    work_dir.mkdir(parents=True, exist_ok=True)
    folded = failed = duplicate = skipped = 0
    downloaded_bytes = 0
    saved_bytes = 0
    db_bytes = 0
    started = time.time()

    with (
        httpx.Client(
            timeout=httpx.Timeout(60.0, read=300.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client,
        ThreadPoolExecutor(max_workers=lookahead) as pool,
    ):
        queue: deque[tuple[Revision, Path, Future[dict[str, object]] | None]] = deque()

        def submit(rev: Revision) -> bool:
            """Queue one revision, downloading it only if it is worth downloading. Returns …"""
            nonlocal skipped, duplicate, saved_bytes
            path = work_dir / f"{rev.source}_{rev.snapshot_date}.json"

            if rev.skip_reason:
                store.record_skip(rev, rev.skip_reason)
                skipped += 1
                log.info("skipped", date=rev.snapshot_date, src=rev.source, reason=rev.skip_reason)

                return False
            same_as = store.hash_seen(rev.api_hash)

            if same_as is not None:
                store.record_duplicate(rev, "api_hash", rev.api_hash, same_as, rev.size)
                duplicate += 1
                saved_bytes += rev.size
                log.info(
                    "duplicate",
                    date=rev.snapshot_date,
                    src=rev.source,
                    same_as=f"{same_as[0]}/{same_as[1]}",
                    saved=_size(rev.size),
                )

                return False
            queue.append((rev, path, pool.submit(download, rev, path, client)))

            return True

        upcoming = iter(pending)

        def fill() -> None:
            """Keep `lookahead` real downloads in flight. Topping the queue up rather than …"""

            while len(queue) < lookahead:
                rev = next(upcoming, None)

                if rev is None:
                    return
                submit(rev)

        fill()

        while queue:
            rev, path, future = queue.popleft()
            fill()
            assert future is not None

            try:
                meta = future.result()
                size = int(str(meta.get("bytes") or 0))
                downloaded_bytes += size
                same_as = store.sha_seen(str(meta.get("sha256") or ""))

                if same_as is not None:
                    store.record_duplicate(rev, "sha256", str(meta["sha256"]), same_as, size)
                    duplicate += 1
                    log.info(
                        "duplicate",
                        date=rev.snapshot_date,
                        src=rev.source,
                        same_as=f"{same_as[0]}/{same_as[1]}",
                        by="sha256",
                    )
                    continue
                stats = store.fold(path, rev, meta, history_dir=history_dir)
                folded += 1
            except SnapshotTooSmall as exc:
                skipped += 1
                store.record_skip(rev, str(exc)[:500])
                log.warning(
                    "snapshot.not_the_register",
                    date=rev.snapshot_date,
                    src=rev.source,
                    reason=str(exc)[:200],
                )
            except Exception as exc:
                failed += 1
                reason = f"{type(exc).__name__}: {exc}"
                store.record_failure(rev, reason[:500])
                log.error("fold.failed", date=rev.snapshot_date, src=rev.source, error=reason[:300])
                _keep_for_diagnosis(path, work_dir / "failed")

                if stop_on_failure:
                    log.error(
                        "backfill.halted",
                        at=rev.snapshot_date,
                        folded=folded,
                        hint="fix the cause and rerun; it resumes here",
                    )
                    break
            else:
                log.info(
                    "folded",
                    date=stats["snapshot_date"],
                    src=stats["source"],
                    records=f"{stats['records']:,}",
                    new=stats["appeared"],
                    changed=stats["changed"],
                    gone=stats["disappeared"],
                    dupe_id=stats.get("rows_dupe_id"),
                    repaired=stats.get("rows_repaired") or "",
                    suspect=stats["suspect_reason"] or "",
                )
            finally:
                _discard(path)
                done_now = folded + duplicate + skipped + failed
                elapsed = time.time() - started

                if folded and folded % CHECKPOINT_EVERY == 0:
                    db_bytes = store.checkpoint()
                eta = (elapsed / done_now) * (len(pending) - done_now) if done_now else 0
                _write_progress(
                    done=done_now,
                    total=len(pending),
                    percent=round(done_now / len(pending) * 100, 2),
                    folded=folded,
                    duplicate=duplicate,
                    skipped=skipped,
                    failed=failed,
                    current=rev.snapshot_date,
                    source=rev.source,
                    downloaded=_size(downloaded_bytes),
                    not_downloaded=_size(saved_bytes),
                    speed=f"{downloaded_bytes / 1e6 / max(elapsed, 1):.1f} MB/s",
                    db=_size(db_bytes),
                    eta=_human(eta),
                    elapsed=_human(elapsed),
                    updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
    elapsed = time.time() - started
    processed = folded + duplicate + skipped + failed
    _write_progress(
        done=processed,
        total=len(pending),
        percent=round(processed / len(pending) * 100, 2),
        folded=folded,
        duplicate=duplicate,
        skipped=skipped,
        failed=failed,
        finished=processed >= len(pending),
        complete=processed >= len(pending),
        downloaded=_size(downloaded_bytes),
        not_downloaded=_size(saved_bytes),
        elapsed=_human(elapsed),
        updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )

    if processed < len(pending):
        log.error(
            "backfill.incomplete",
            processed=processed,
            pending=len(pending),
            hint="rerun the same command; it resumes at the frontier",
        )
    log.info(
        "backfill.done",
        folded=folded,
        duplicate=duplicate,
        skipped=skipped,
        failed=failed,
        downloaded=_size(downloaded_bytes),
        elapsed=_human(elapsed),
    )

    return {
        "folded": folded,
        "failed": failed,
        "skipped": skipped,
        "duplicate": duplicate,
        "already": len(done),
    }
