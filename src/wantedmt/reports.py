"""Quality reports and aggregates."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb

METRICS: tuple[tuple[str, str], ...] = (
    ("records_total", "count(*)"),
    ("records_present", "count(*) FILTER (WHERE is_present)"),
    ("records_disappeared", "count(*) FILTER (WHERE disappeared_at IS NOT NULL)"),
    ("brand_resolved", "count(*) FILTER (WHERE brand IS NOT NULL)"),
    ("model_resolved", "count(*) FILTER (WHERE model IS NOT NULL)"),
    ("homoglyph_fixed", "count(*) FILTER (WHERE homoglyph_fixed)"),
    ("imei_usable", "count(*) FILTER (WHERE imei_norm IS NOT NULL)"),
    ("imei_luhn_valid", "count(*) FILTER (WHERE imei_luhn_valid)"),
    ("imei_repaired", "count(*) FILTER (WHERE imei_was_repaired)"),
    ("imei_invalid", "count(*) FILTER (WHERE imei_kind = 'INVALID')"),
    ("insert_date_implausible", "count(*) FILTER (WHERE insert_date_implausible)"),
    ("distinct_tac", "count(DISTINCT tac)"),
    ("distinct_brand", "count(DISTINCT brand)"),
    ("distinct_nz_raw", "count(DISTINCT nz_raw)"),
)


QUALITY_CHECKS = "\nUNION ALL ".join(
    f"SELECT '{name}' AS metric, {expression} AS value FROM records_normalized"
    for name, expression in METRICS
)


UNMATCHED = """
SELECT m.nz_raw, m.nz_clean, count(*) AS records
FROM records_normalized r
JOIN dict_nz_mapping m ON m.nz_raw = r.nz_raw
WHERE m.brand IS NULL AND m.method <> 'empty'
GROUP BY 1, 2
ORDER BY records DESC
LIMIT ?
"""


def quality(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {row[0]: int(row[1]) for row in con.execute(QUALITY_CHECKS).fetchall()}


def unmatched(con: duckdb.DuckDBPyConnection, limit: int = 200) -> list[tuple[str, str, int]]:
    return [(r[0], r[1], int(r[2])) for r in con.execute(UNMATCHED, [limit]).fetchall()]


REVIEW_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "sibling": "🇺🇦 Читати українською",
        "heading": "Weekly review of unresolved spellings",
        "records": "Records in total",
        "brand": "Brand resolved",
        "model": "Model resolved",
        "homoglyphs": "Look-alike characters repaired",
        "imei": "IMEI usable",
        "luhn": "passing the Luhn check",
        "section": "Top {n} unresolved spellings ({records} records in this list)",
        "advice": (
            "Work down the list into `dict/brand_aliases.csv` — a dictionary line pays "
            "for exactly the number of records in its column."
        ),
        "columns": "| # | NZ as published | After cleaning | Records |",
    },
    "uk": {
        "sibling": "🇬🇧 Read in English",
        "heading": "Тижневий огляд нерозпізнаних написань",
        "records": "Записів усього",
        "brand": "Бренд визначено",
        "model": "Модель визначено",
        "homoglyphs": "Виправлено літер-двійників",
        "imei": "IMEI придатних",
        "luhn": "проходять перевірку Луна",
        "section": "Топ-{n} нерозпізнаних написань ({records} записів у цьому списку)",
        "advice": (
            "Додавати в `dict/brand_aliases.csv` згори вниз — рядок словника окупається "
            "рівно тим числом записів, що в колонці."
        ),
        "columns": "| # | NZ як у джерелі | Після очистки | Записів |",
    },
}


def weekly_review(
    con: duckdb.DuckDBPyConnection,
    limit: int = 200,
    lang: str = "en",
    sibling: str = "",
) -> str:
    """Markdown for the weekly 'what did not match' review, in one language."""
    t = REVIEW_TEXT[lang]
    stats = quality(con)
    rows = unmatched(con, limit)
    total = stats["records_total"] or 1
    covered = stats["brand_resolved"]
    unresolved_records = sum(r[2] for r in rows)
    lines = [f"# {t['heading']} — {date.today().isoformat()}", ""]

    if sibling:
        lines += [f"[{t['sibling']}]({sibling})", ""]
    lines += [
        f"- {t['records']}: **{total:,}**",
        f"- {t['brand']}: **{covered:,}** ({covered / total:.2%})",
        f"- {t['model']}: **{stats['model_resolved']:,}** ({stats['model_resolved'] / total:.2%})",
        f"- {t['homoglyphs']}: **{stats['homoglyph_fixed']:,}**",
        f"- {t['imei']}: **{stats['imei_usable']:,}**, "
        f"{t['luhn']}: **{stats['imei_luhn_valid']:,}** "
        f"({stats['imei_luhn_valid'] / total:.2%})",
        "",
        f"## {t['section'].format(n=len(rows), records=f'{unresolved_records:,}')}",
        "",
        t["advice"],
        "",
        t["columns"],
        "|--:|---|---|--:|",
    ]

    for i, (raw, clean, count) in enumerate(rows, 1):
        safe_raw = (raw or "").replace("|", "\\|")
        safe_clean = (clean or "").replace("|", "\\|")
        lines.append(f"| {i} | `{safe_raw}` | `{safe_clean}` | {count:,} |")

    return "\n".join(lines) + "\n"


AGGREGATES = {
    "by_year": """
        SELECT date_trunc('year', insert_date)::DATE AS period,
               count(*) AS records,
               count(*) FILTER (WHERE brand IS NOT NULL) AS with_brand
        FROM records_normalized WHERE insert_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """,
    "by_brand": """
        SELECT brand, count(*) AS records,
               count(DISTINCT tac) AS tacs,
               round(100.0 * count(*) FILTER (WHERE imei_luhn_valid) / count(*), 2) AS luhn_pct
        FROM records_normalized WHERE brand IS NOT NULL
        GROUP BY 1 ORDER BY records DESC
    """,
    "by_ovd": """
        SELECT ovd, count(*) AS records,
               min(insert_date)::DATE AS first_record,
               max(insert_date)::DATE AS last_record
        FROM records_normalized WHERE ovd IS NOT NULL AND insert_date IS NOT NULL
        GROUP BY 1 ORDER BY records DESC
    """,
    "closures": """
        SELECT disappeared_at AS period, count(*) AS closed
        FROM records_normalized WHERE disappeared_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """,
    "monthly_coverage": """
        WITH months AS (
            SELECT unnest(generate_series(
                date_trunc('month', (SELECT min(snapshot_date) FROM observations)),
                date_trunc('month', (SELECT max(snapshot_date) FROM observations)),
                INTERVAL 1 MONTH))::DATE AS period
        ),
        seen AS (
            SELECT date_trunc('month', snapshot_date)::DATE AS period,
                   count(*) AS days_observed,
                   max(days_since_previous) AS longest_gap_days
            FROM observations GROUP BY 1
        ),
        entered AS (
            SELECT date_trunc('month', first_seen)::DATE AS period,
                   count(*) AS first_seen_here
            FROM records_normalized GROUP BY 1
        )
        SELECT m.period,
               coalesce(s.days_observed, 0) AS days_observed,
               datediff('day', m.period, m.period + INTERVAL 1 MONTH) AS days_in_month,
               round(100.0 * coalesce(s.days_observed, 0)
                     / datediff('day', m.period, m.period + INTERVAL 1 MONTH), 1)
                                                           AS coverage_pct,
               s.longest_gap_days,
               coalesce(e.first_seen_here, 0)              AS first_seen_here,
               coalesce(s.days_observed, 0) > 0            AS is_measured
        FROM months m
        LEFT JOIN seen s USING (period)
        LEFT JOIN entered e USING (period)
        ORDER BY m.period
    """,
    "observations": """
        SELECT snapshot_date, source, record_count, days_since_previous
        FROM observations ORDER BY snapshot_date
    """,
}


def aggregate(con: duckdb.DuckDBPyConnection, name: str) -> duckdb.DuckDBPyRelation:
    return con.sql(AGGREGATES[name])


VOLUME = """
SELECT
    count(*)                                                   AS revisions,
    count(*) FILTER (WHERE coalesce(status,'folded') = 'folded')    AS folded,
    count(*) FILTER (WHERE status = 'duplicate')               AS duplicate,
    count(*) FILTER (WHERE status = 'skipped')                 AS skipped,
    count(*) FILTER (WHERE status = 'failed')                  AS failed,
    sum(bytes)                                                 AS bytes_published,
    sum(bytes) FILTER (WHERE coalesce(status,'folded') = 'folded')  AS bytes_downloaded,
    sum(bytes) FILTER (WHERE status = 'duplicate')             AS bytes_not_fetched,
    sum(rows_raw)                                              AS rows_read,
    sum(rows_dupe_id)                                          AS rows_dropped_dupe_id,
    sum(rows_repaired)                                         AS rows_repaired,
    count(*) FILTER (WHERE read_mode = 'repaired')             AS files_repaired,
    min(snapshot_date)                                         AS first_date,
    max(snapshot_date)                                         AS last_date
FROM snapshots
"""


def volume(con: duckdb.DuckDBPyConnection, paths: dict[str, Path]) -> dict[str, Any]:
    """The series by weight: published, moved, kept. `paths` names the artefacts to measure on …"""
    row = con.execute(VOLUME).fetchone()
    assert row is not None
    columns = [d[0] for d in con.execute(VOLUME).description or []]
    measured = dict(zip(columns, row, strict=True))
    out: dict[str, Any] = dict(measured)
    sizes = {name: _disk_bytes(path) for name, path in paths.items()}

    for name, size in sizes.items():
        out[f"{name}_bytes"] = size
    published = int(measured["bytes_published"] or 0)
    kept = sum(sizes.values())
    out["kept_bytes"] = kept
    out["kept_share_of_published"] = round(kept / published, 6) if published else None

    return out


def _disk_bytes(path: Path) -> int:
    if not path.exists():
        return 0

    if path.is_file():
        return path.stat().st_size

    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
