"""Rendering the quality report and publishing it to R2."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dq import DQReport, to_markdown

STATUS_COLOURS = {"PASS": "#31C97E", "WARN": "#E0A400", "FAIL": "#E0523E"}


CSS = """
  :root { color-scheme: light dark; }
  body { font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0 auto; max-width: 62rem; padding: 2rem 1rem; }
  h1 { font-size: 1.5rem; margin-bottom: .25rem; }
  h2 { font-size: 1.1rem; margin-top: 2rem; }
  .badge { display: inline-block; padding: .2rem .7rem; border-radius: 999px;
           color: #08130d; font-weight: 600; }
  .meta { color: #6b7280; margin: .5rem 0 1.5rem; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 2rem;
          display: block; overflow-x: auto; }
  th, td { text-align: left; padding: .4rem .6rem;
           border-bottom: 1px solid rgba(128,128,128,.25); white-space: nowrap; }
  th { font-weight: 600; }
  td.n { text-align: right; font-variant-numeric: tabular-nums; }
  .s.pass { color: #31C97E; }
  .s.fail { color: #E0523E; }
  .s.info { color: #6b7280; }
  footer { color: #6b7280; font-size: .85rem;
           border-top: 1px solid rgba(128,128,128,.25); padding-top: 1rem; }
  .tiles { display: grid; gap: .75rem; margin: 1.5rem 0 2rem;
           grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr)); }
  .tile { border: 1px solid rgba(128,128,128,.25); border-radius: .6rem;
          padding: .9rem 1rem; }
  .tile .v { font-size: 1.9rem; font-weight: 700; line-height: 1.1;
             font-variant-numeric: tabular-nums; }
  .tile .k { color: #6b7280; font-size: .8rem; margin-top: .25rem; }
  .tile.ok .v { color: #31C97E; }
  .tile.bad .v { color: #E0523E; }
  /* Both languages ship in the page; the button flips which one is shown, so
     switching costs no request and the page still works with JS disabled
     (English, the default, is visible without any script running). */
  /* Scoped to span and div on purpose: a bare [lang="uk"] also matches the
     <html> element, which hides the entire document. */
  span[lang="uk"], div[lang="uk"] { display: none; }
  body.uk span[lang="uk"] { display: revert; }
  body.uk div[lang="uk"] { display: revert; }
  body.uk span[lang="en"], body.uk div[lang="en"] { display: none; }
  .lang { float: right; font: inherit; cursor: pointer; padding: .3rem .8rem;
          border-radius: .5rem; background: none; color: inherit;
          border: 1px solid rgba(128,128,128,.4); }
"""


TOGGLE = '<button class="lang" onclick="document.body.classList.toggle(\'uk\')">EN / UA</button>'


NPU_URL = "https://data.gov.ua/dataset/30b67898-1968-4d99-8058-298b56f22bff"

MVS_URL = "https://data.gov.ua/dataset/5c6c156f-21ee-42cd-8da3-dcde6828be97"


def source_note() -> str:
    """Attribution for both publishers, as the source licence requires. Naming only the …"""
    en = (
        f'Sources, both CC BY 4.0: <a href="{NPU_URL}">National Police of Ukraine</a> '
        f'(current register) and <a href="{MVS_URL}">Ministry of Internal Affairs</a> '
        "(archived publication, the only source of history before 2026-04-30). "
        "Derived dataset prepared by the Trofey project. This is not an official "
        "registry, and absence of a device here certifies nothing about it."
    )
    uk = (
        f'Джерела, обидва CC BY 4.0: <a href="{NPU_URL}">Національна поліція України</a> '
        f'(чинний реєстр) та <a href="{MVS_URL}">Міністерство внутрішніх справ</a> '
        "(архівна публікація — єдине джерело історії до 2026-04-30). "
        "Похідний набір підготовлено проєктом Trofey. Це не офіційний реєстр, "
        "і відсутність апарата тут нічого про нього не засвідчує."
    )

    return _b(en, uk)


def _b(en: str, uk: str) -> str:
    """Both languages side by side; CSS shows one. No second request, and the English text is …"""

    return f'<span lang="en">{en}</span><span lang="uk">{uk}</span>'


def _tiles(report: DQReport, headline: dict[str, int] | None) -> str:
    """The numbers a reader needs before deciding whether to read further."""
    passed = sum(1 for r in report.results if r.status == "PASS")
    failed = len(report.failures)
    blocking = len(report.blocking)
    cells: list[tuple[str, str, str]] = [
        (f"{report.rows:,}".replace(",", " "), _b("records", "записів у наборі"), ""),
        (str(passed), _b("checks passed", "перевірок пройдено"), "ok" if not failed else ""),
        (str(failed), _b("failed", "не пройдено"), "bad" if failed else "ok"),
        (str(blocking), _b("blocking", "блокуючих помилок"), "bad" if blocking else "ok"),
    ]

    if headline:
        total = headline.get("records_total") or 1

        def pct(key: str) -> str:
            return f"{headline.get(key, 0) / total:.1%}"

        cells += [
            (pct("brand_resolved"), _b("brand resolved", "марку визначено"), ""),
            (pct("model_resolved"), _b("model resolved", "модель визначено"), ""),
            (
                pct("imei_luhn_valid"),
                _b("IMEI with a valid checksum", "IMEI з дійсною контрольною сумою"),
                "",
            ),
        ]

    return (
        '<div class="tiles">'
        + "".join(
            f'<div class="tile {cls}"><div class="v">{value}</div>'
            f'<div class="k">{label}</div></div>'
            for value, label, cls in cells
        )
        + "</div>"
    )


def _head_row() -> str:
    return (
        "<tr><th>" + _b("What was checked", "Що перевіряли") + "</th><th>Status</th>"
        "<th>Severity</th><th>" + _b("Violations", "Порушень") + "</th>"
        "<th>" + _b("Detail", "Деталі") + "</th></tr>"
    )


TITLE = "Якість даних — реєстр викрадених і втрачених мобільних телефонів України"

CANONICAL = "https://trofey.app/data-quality/wantedmt/latest.html"

REPO = "https://github.com/mykhailoklimnyk/ua-stolen-phones-data-quality"

SOURCE_DATASET = "https://data.gov.ua/dataset/30b67898-1968-4d99-8058-298b56f22bff"


def _description(report: DQReport) -> str:
    return (
        f"Щоденна перевірка якості відкритого набору НПУ: {report.rows:,} записів, "
        f"{sum(1 for r in report.results if r.status == 'PASS')} перевірок, "
        f"статус {report.status}. Нормалізовані марка, модель та IMEI."
    ).replace(",", " ")


def _json_ld(report: DQReport) -> str:
    """schema.org/Dataset — what Google Dataset Search actually reads. Meta tags help a person …"""
    import json

    payload = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": TITLE,
        "description": (
            "Нормалізована й перевірена похідна від відкритого набору «Інформація про "
            "викрадені, втрачені мобільні телефони» Національної поліції України. "
            "Додано історію записів, розбір IMEI за правилами GSMA, визначення марки, "
            "виробника й моделі з вільного тексту та регіон підрозділу реєстрації."
        ),
        "url": CANONICAL,
        "sameAs": REPO,
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "inLanguage": "uk",
        "isBasedOn": SOURCE_DATASET,
        "dateModified": report.generated_at[:10],
        "keywords": [
            "IMEI",
            "викрадені телефони",
            "відкриті дані",
            "Україна",
            "якість даних",
            "data.gov.ua",
        ],
        "creator": {"@type": "Organization", "name": "Trofey", "url": "https://trofey.app"},
        "spatialCoverage": {"@type": "Place", "name": "Україна"},
        "variableMeasured": [
            {"@type": "PropertyValue", "name": "Записів у наборі", "value": report.rows},
            {
                "@type": "PropertyValue",
                "name": "Перевірок пройдено",
                "value": sum(1 for r in report.results if r.status == "PASS"),
            },
        ],
    }

    return (
        '<script type="application/ld+json">'
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "</script>"
    )


INTRO_EN = (
    "<p>This page records what was verified in the stolen and lost mobile phones "
    "dataset before it was published. The dataset is built from open data of the "
    "National Police of Ukraine, where one record is one report of a missing "
    "device.</p>"
    "<p>The checks are of two kinds. <b>Blocking</b> ones concern our own internal "
    "consistency: a record cannot disappear before it appeared, and an IMEI split "
    "into its parts must reassemble without loss. If one of those fails, nothing "
    "is published at all. <b>Warnings and informational</b> checks describe the "
    "source itself — how many brands could be resolved, how many IMEIs pass their "
    "checksum. They do not block publication, because they measure a property of "
    "the register rather than a mistake of ours.</p>"
)


INTRO_UK = (
    "<p>Ця сторінка показує, що саме перевірено в наборі даних про викрадені та "
    "втрачені мобільні телефони перед публікацією. Набір будується з відкритих "
    "даних Національної поліції: один запис — одна заява про зниклий апарат.</p>"
    "<p>Перевірки двох родів. <b>Блокуючі</b> стосуються внутрішньої "
    "несуперечності: запис не може зникнути раніше, ніж з'явився, а розібраний "
    "на частини IMEI мусить складатися назад без втрат. Якщо така перевірка не "
    "проходить, набір не публікується взагалі. <b>Попередження та довідкові</b> "
    "описують самі дані — скільки марок вдалося розпізнати, скільки IMEI "
    "проходять контрольну суму. Вони не блокують публікацію, бо це властивість "
    "джерела, а не наша помилка.</p>"
)


INTRO = f'<div lang="en">{INTRO_EN}</div><div lang="uk">{INTRO_UK}</div>'


@dataclass(frozen=True)
class R2Config:
    account_id: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    prefix: str = "data-quality/wantedmt"
    public_base: str = "https://trofey.app"

    @classmethod
    def from_env(cls) -> R2Config | None:
        required = ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")

        if not all(os.environ.get(k) for k in required):
            return None

        return cls(
            account_id=os.environ["R2_ACCOUNT_ID"],
            bucket=os.environ["R2_BUCKET"],
            access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            prefix=os.environ.get("R2_DQ_PREFIX", "data-quality/wantedmt"),
            public_base=os.environ.get("R2_PUBLIC_BASE", "https://trofey.app"),
        )


def _client(cfg: R2Config) -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{cfg.account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name="auto",
    )


def _rows(results: list[Any]) -> str:
    """Status and severity stay in English — they are the machine's vocabulary and match what …"""

    return "\n".join(
        "<tr>"
        f'<td><span lang="en">{r.human_en}</span>'
        f'<span lang="uk">{r.human}</span></td>'
        f'<td class="s {r.status.lower()}">{r.status}</td>'
        f"<td>{r.severity}</td>"
        f'<td class="n">{r.failed:,}</td><td>{r.detail}</td>'
        "</tr>"
        for r in results
    )


def to_html(
    report: DQReport,
    source_note: str,
    headline: dict[str, int] | None = None,
    indexable: bool = False,
) -> str:
    """`indexable` stays False until the page is meant to be found. The schema.org block and …"""
    colour = STATUS_COLOURS.get(report.status, "#6b7280")
    failed_section = ""

    if report.failures:
        failed_section = (
            "<h2>"
            + _b("Checks that did not pass", "Перевірки, що не пройшли")
            + "</h2><table><thead>"
            + _head_row()
            + "</thead><tbody>"
            + _rows(report.failures)
            + "</tbody></table>"
        )
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{TITLE}</title>",
        f'<meta name="description" content="{_description(report)}">',
        '<meta name="robots" content="index, follow">'
        if indexable
        else '<meta name="robots" content="noindex, nofollow">',
        f'<link rel="canonical" href="{CANONICAL}">',
        f'<meta property="og:title" content="{TITLE}">',
        f'<meta property="og:description" content="{_description(report)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:url" content="{CANONICAL}">',
        '<meta name="twitter:card" content="summary">',
        _json_ld(report),
        "<style>" + CSS + "</style></head><body>",
        TOGGLE,
        '<h1><span lang="en">Data quality — stolen and lost mobile phones</span>'
        '<span lang="uk">Якість даних — викрадені та втрачені телефони</span></h1>',
        f'<p><span class="badge" style="background:{colour}">{report.status}</span></p>',
        f'<p class="meta">{_b("Updated", "Оновлено")}: '
        f"{report.generated_at[:16].replace('T', ' ')}</p>",
        _tiles(report, headline),
        INTRO,
        failed_section,
        "<h2>Усі перевірки</h2><table><thead>" + _head_row() + "</thead><tbody>",
        _rows(report.results),
        "</tbody></table>",
        f"<footer>{source_note}</footer>",
        "</body></html>",
    ]

    return "\n".join(parts) + "\n"


def upload(
    cfg: R2Config, report_date: str, html: str, markdown: str, markdown_uk: str
) -> list[str]:
    client = _client(cfg)
    fresh = "public, max-age=300, must-revalidate"
    payloads = [
        (f"{cfg.prefix}/latest.html", html, "text/html; charset=utf-8", fresh),
        (f"{cfg.prefix}/latest.md", markdown, "text/markdown; charset=utf-8", fresh),
        (f"{cfg.prefix}/latest.uk.md", markdown_uk, "text/markdown; charset=utf-8", fresh),
        (
            f"{cfg.prefix}/history/{report_date}.html",
            html,
            "text/html; charset=utf-8",
            "public, max-age=31536000, immutable",
        ),
    ]
    urls = []

    for key, body, content_type, cache in payloads:
        client.put_object(
            Bucket=cfg.bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType=content_type,
            CacheControl=cache,
        )
        urls.append(f"{cfg.public_base.rstrip('/')}/{key}")

    return urls


def write_local(out_dir: Path, report: DQReport, html: str) -> list[Path]:
    """Both languages, each linking to the sibling that sits beside it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report_date = report.generated_at[:10]
    written = {out_dir / "latest.html": html}

    for stem in ("latest", report_date):
        written[out_dir / f"{stem}.md"] = to_markdown(report, "en", f"{stem}.uk.md")
        written[out_dir / f"{stem}.uk.md"] = to_markdown(report, "uk", f"{stem}.md")

    for path, body in written.items():
        path.write_text(body, encoding="utf-8")

    return list(written)
