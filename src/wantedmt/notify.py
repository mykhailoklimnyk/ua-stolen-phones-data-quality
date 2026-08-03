"""Alerts to the ops group."""

from __future__ import annotations

import os

import httpx
import structlog

log = structlog.get_logger()


API = "https://api.telegram.org/bot{token}/sendMessage"


REGRESSION_TOLERANCE = 0.005


REVIEW_THRESHOLD = 500


def _credentials() -> tuple[str, str] | None:
    token = os.environ.get("BOT_TOKEN", "")
    chat = os.environ.get("OPS_CHAT_ID", "")

    return (token, chat) if token and chat else None


def send(text: str) -> bool:
    """Post to the ops group. Never raises: an alert that breaks the run it is reporting on …"""
    creds = _credentials()

    if creds is None:
        log.info("notify.skipped", reason="BOT_TOKEN or OPS_CHAT_ID is not set")

        return False
    token, chat = creds

    try:
        response = httpx.post(
            API.format(token=token),
            json={
                "chat_id": chat,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20.0,
        )
        response.raise_for_status()

        return True
    except Exception as exc:
        log.warning("notify.failed", error=f"{type(exc).__name__}: {exc}")

        return False


def failure(stage: str, error: str) -> bool:
    return send(
        f"❌ <b>WantedMT: прогін зупинено</b>\n"
        f"Крок: <code>{stage}</code>\n"
        f"<code>{error[:600]}</code>\n\n"
        f"Нічого не опубліковано, попередній звіт лишається чинним."
    )


def blocking_failures(failures: list[tuple[str, int]]) -> bool:
    lines = "\n".join(f"• {name} — {count:,} порушень" for name, count in failures[:10])

    return send(
        f"❌ <b>WantedMT: якість не пройшла</b>\n"
        f"Блокуючих перевірок: {len(failures)}\n{lines}\n\n"
        f"Набір не опубліковано."
    )


def regression(changes: list[tuple[str, float, float]]) -> bool:
    lines = "\n".join(
        f"• {metric}: {before:.2%} → {after:.2%} ({after - before:+.2%})"
        for metric, before, after in changes
    )

    return send(
        f"⚠️ <b>WantedMT: якість просіла</b>\n{lines}\n\n"
        f"Конвеєр відпрацював, але покриття впало — варто подивитись, "
        f"що змінилось у джерелі або в правилах."
    )


def review_queue(unresolved_records: int, top: list[tuple[str, int]]) -> bool:
    lines = "\n".join(f"• <code>{name}</code> — {count:,}" for name, count in top[:8])

    return send(
        f"📋 <b>WantedMT: є що розібрати руками</b>\n"
        f"Нерозпізнаних записів: {unresolved_records:,}\n{lines}\n\n"
        f"Додавати у <code>dict/brand_aliases.csv</code> згори вниз — "
        f"рядок словника окупається числом у колонці."
    )
