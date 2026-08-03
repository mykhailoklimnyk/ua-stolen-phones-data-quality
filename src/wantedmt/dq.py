"""YAML-driven data-quality engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import yaml

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


FIELD_LABELS = {
    "record_key": "ідентифікатор запису",
    "imei_raw": "IMEI як у джерелі",
    "imei_norm": "IMEI після звірки",
    "imei_kind": "тип ідентифікатора",
    "imei_luhn_valid": "IMEI проходить контрольну суму",
    "tac": "код моделі (TAC)",
    "rbi": "код органу сертифікації",
    "snr": "серійний номер у межах моделі",
    "check_digit": "контрольна цифра",
    "brand": "марка",
    "manufacturer": "виробник",
    "model": "модель",
    "nz_method": "спосіб визначення марки",
    "nz_raw": "марка/модель як у джерелі",
    "ovd": "підрозділ, що зареєстрував",
    "insert_date": "дата внесення",
    "dk": "дата реєстрації в журналі",
    "insert_date_implausible": "дата поза правдоподібним діапазоном",
    "first_seen": "перша поява в реєстрі",
    "last_seen": "остання поява в реєстрі",
    "is_present": "є в останньому вивантаженні",
    "_table_": "набір загалом",
}


CHECK_LABELS_EN = {
    "row_count": "Volume is at least the expected floor",
    "not_null": "Field is populated",
    "allowed_values": "Values come only from the permitted set",
    "length": "Length matches the format",
    "regex": "Format matches the pattern",
    "range": "Values fall within the allowed bounds",
    "fill_rate": "Share of populated values",
    "true_share": "Share that passes the check",
    "true_share_max": "Share of deviations stays under the limit",
    "distinct_count": "Number of distinct values",
}


CHECK_LABELS = {
    "row_count": "Обсяг не менший за очікуваний",
    "not_null": "Поле заповнене",
    "allowed_values": "Значення лише з дозволеного переліку",
    "length": "Довжина відповідає формату",
    "regex": "Формат відповідає зразку",
    "range": "Значення в допустимих межах",
    "fill_rate": "Частка заповнених значень",
    "true_share": "Частка тих, що проходять перевірку",
    "true_share_max": "Частка відхилень не перевищує межу",
    "distinct_count": "Кількість різних значень",
}


@dataclass
class CheckResult:
    field_name: str
    check: str
    status: str
    severity: str
    total: int = 0
    failed: int = 0
    detail: str = ""
    title: str = ""
    title_en: str = ""

    @property
    def human_en(self) -> str:
        if self.title_en:
            return self.title_en
        label = CHECK_LABELS_EN.get(self.check, self.check)
        field = self.field_name if self.field_name != "_table_" else "dataset"

        return f"{label} — {field}" if field != "_assertion_" else label

    @property
    def human(self) -> str:
        """What was checked, in words rather than identifiers."""

        if self.title:
            return self.title
        label = CHECK_LABELS.get(self.check, self.check)
        field = FIELD_LABELS.get(self.field_name)

        return f"{label} — {field}" if field else label

    def human_in(self, lang: str) -> str:
        return self.human_en if lang == "en" else self.human


@dataclass
class DQReport:
    dataset_key: str
    rows: int
    results: list[CheckResult] = field(default_factory=list)
    generated_at: str = ""

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "FAIL"]

    @property
    def blocking(self) -> list[CheckResult]:
        return [r for r in self.failures if r.severity == "critical"]

    @property
    def status(self) -> str:
        if self.blocking:
            return "FAIL"

        return "WARN" if self.failures else "PASS"


def _pct(value: float) -> str:
    """A threshold of 0.005 is half a percent, not zero — rounding it to 0% in the report …"""
    return f"{value:.2%}".rstrip("0").rstrip(".").replace("%", "") + "%"


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()

    return int(row[0]) if row and row[0] is not None else 0


def run(con: duckdb.DuckDBPyConnection, config_path: Path) -> DQReport:
    cfg: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    table = cfg.get("table", "records_normalized")
    rows = _scalar(con, f"SELECT count(*) FROM {table}")
    report = DQReport(
        dataset_key=cfg.get("dataset_key", table),
        rows=rows,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    floor = int(cfg.get("row_count_min", 0))
    report.results.append(
        CheckResult(
            "_table_",
            "row_count",
            "PASS" if rows >= floor else "FAIL",
            "critical",
            rows,
            0 if rows >= floor else 1,
            f">= {floor:,}",
        )
    )

    for name, spec in (cfg.get("fields") or {}).items():
        severity = spec.get("severity", "warning")

        if not spec.get("nullable", True):
            nulls = _scalar(con, f"SELECT count(*) FROM {table} WHERE {name} IS NULL")
            report.results.append(
                CheckResult(
                    name,
                    "not_null",
                    "PASS" if nulls == 0 else "FAIL",
                    severity,
                    rows,
                    nulls,
                    f"{(rows - nulls) / rows:.2%}" if rows else "",
                )
            )

        if (allowed := spec.get("allowed_values")) is not None:
            literals = ", ".join(f"'{v}'" for v in allowed)
            bad = _scalar(
                con,
                f"SELECT count(*) FROM {table} "
                f"WHERE {name} IS NOT NULL AND {name}::VARCHAR NOT IN ({literals})",
            )
            report.results.append(
                CheckResult(
                    name,
                    "allowed_values",
                    "PASS" if bad == 0 else "FAIL",
                    severity,
                    rows,
                    bad,
                    str(allowed),
                )
            )

        if (length := spec.get("length")) is not None:
            bad = _scalar(
                con,
                f"SELECT count(*) FROM {table} "
                f"WHERE {name} IS NOT NULL AND length({name}::VARCHAR) <> {length}",
            )
            report.results.append(
                CheckResult(
                    name,
                    "length",
                    "PASS" if bad == 0 else "FAIL",
                    severity,
                    rows,
                    bad,
                    f"= {length}",
                )
            )

        if (pattern := spec.get("regex")) is not None:
            bad = _scalar(
                con,
                f"SELECT count(*) FROM {table} "
                f"WHERE {name} IS NOT NULL AND NOT regexp_matches({name}::VARCHAR, '{pattern}')",
            )
            report.results.append(
                CheckResult(
                    name,
                    "regex",
                    "PASS" if bad == 0 else "FAIL",
                    severity,
                    rows,
                    bad,
                    pattern,
                )
            )
        lo, hi = spec.get("min_value"), spec.get("max_value")

        if lo is not None or hi is not None:
            clauses = []

            if lo is not None:
                clauses.append(
                    f"{name} < DATE '{lo}'" if spec.get("type") == "date" else f"{name} < {lo}"
                )

            if hi is not None:
                bound = (
                    "now()"
                    if hi == "today"
                    else (f"DATE '{hi}'" if spec.get("type") == "date" else str(hi))
                )
                clauses.append(f"{name} > {bound}")
            bad = _scalar(
                con,
                f"SELECT count(*) FROM {table} "
                f"WHERE {name} IS NOT NULL AND ({' OR '.join(clauses)})",
            )
            report.results.append(
                CheckResult(
                    name,
                    "range",
                    "PASS" if bad == 0 else "FAIL",
                    severity,
                    rows,
                    bad,
                    f"[{lo}, {hi}]",
                )
            )

        if (target := spec.get("min_fill_rate")) is not None:
            filled = _scalar(con, f"SELECT count(*) FROM {table} WHERE {name} IS NOT NULL")
            share = filled / rows if rows else 0.0
            report.results.append(
                CheckResult(
                    name,
                    "fill_rate",
                    "PASS" if share >= target else "FAIL",
                    severity,
                    rows,
                    rows - filled,
                    f"{share:.2%} (>= {_pct(target)})",
                )
            )

        if (target := spec.get("min_true_share")) is not None:
            true_rows = _scalar(con, f"SELECT count(*) FROM {table} WHERE {name}")
            considered = _scalar(con, f"SELECT count(*) FROM {table} WHERE {name} IS NOT NULL")
            share = true_rows / considered if considered else 0.0
            report.results.append(
                CheckResult(
                    name,
                    "true_share",
                    "PASS" if share >= target else "FAIL",
                    severity,
                    considered,
                    considered - true_rows,
                    f"{share:.2%} (>= {_pct(target)})",
                )
            )

        if (ceiling := spec.get("max_true_share")) is not None:
            true_rows = _scalar(con, f"SELECT count(*) FROM {table} WHERE {name}")
            share = true_rows / rows if rows else 0.0
            report.results.append(
                CheckResult(
                    name,
                    "true_share_max",
                    "PASS" if share <= ceiling else "FAIL",
                    severity,
                    rows,
                    true_rows,
                    f"{share:.2%} (<= {_pct(ceiling)})",
                )
            )

        if spec.get("distinct_count"):
            distinct = _scalar(con, f"SELECT count(DISTINCT {name}) FROM {table}")
            report.results.append(
                CheckResult(
                    name,
                    "distinct_count",
                    "INFO",
                    "info",
                    rows,
                    0,
                    f"{distinct:,}",
                )
            )

    for assertion in cfg.get("assertions") or []:
        bad = _scalar(con, assertion["sql"])
        report.results.append(
            CheckResult(
                "_assertion_",
                assertion["name"],
                "PASS" if bad == 0 else "FAIL",
                assertion.get("severity", "critical"),
                rows,
                bad,
                "= 0",
                title=assertion.get("title", ""),
                title_en=assertion.get("title_en", ""),
            )
        )
    report.results.sort(
        key=lambda r: (r.status != "FAIL", SEVERITY_ORDER.get(r.severity, 3), r.field_name)
    )

    return report


MARKDOWN_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "sibling": "🇺🇦 Читати українською",
        "heading": "Data-quality report",
        "status": "Status",
        "rows": "Records",
        "checks": "Checks",
        "checks_value": "{passed} of {checked} passed, {failed} failed",
        "timestamp": "Generated",
        "failed_section": "Failed checks",
        "all_section": "All checks",
        "head_failed": "| What was checked | Field | Severity | Total | Failed | Detail |",
        "head_all": "| What was checked | Field | Status | Detail |",
        "critical": "blocking",
        "warning": "warning",
        "info": "info",
    },
    "uk": {
        "sibling": "🇬🇧 Read in English",
        "heading": "Звіт про якість даних",
        "status": "Статус",
        "rows": "Записів",
        "checks": "Перевірок",
        "checks_value": "{passed} з {checked} пройдено, {failed} провалено",
        "timestamp": "Сформовано",
        "failed_section": "Провалені перевірки",
        "all_section": "Усі перевірки",
        "head_failed": "| Що перевірено | Поле | Критичність | Усього | Провалено | Деталі |",
        "head_all": "| Що перевірено | Поле | Статус | Деталі |",
        "critical": "блокує",
        "warning": "попередження",
        "info": "довідково",
    },
}


def to_markdown(report: DQReport, lang: str = "en", sibling: str = "") -> str:
    t = MARKDOWN_TEXT[lang]
    passed = sum(1 for r in report.results if r.status == "PASS")
    checked = sum(1 for r in report.results if r.status in ("PASS", "FAIL"))
    lines = [f"# {t['heading']}: {report.dataset_key} — {report.generated_at[:10]}", ""]

    if sibling:
        lines += [f"[{t['sibling']}]({sibling})", ""]
    lines += [
        f"**{t['status']}:** {report.status}  ",
        f"**{t['rows']}:** {report.rows:,}  ",
        f"**{t['checks']}:** "
        + t["checks_value"].format(passed=passed, checked=checked, failed=len(report.failures))
        + "  ",
        f"**{t['timestamp']}:** {report.generated_at}",
        "",
    ]

    if report.failures:
        lines += [
            f"## {t['failed_section']}",
            "",
            t["head_failed"],
            "|---|---|---|------:|------:|---|",
        ]

        for r in report.failures:
            lines.append(
                f"| {r.human_in(lang)} | `{r.field_name}` | {t.get(r.severity, r.severity)} | "
                f"{r.total:,} | {r.failed:,} | {r.detail} |"
            )
        lines.append("")
    lines += [f"## {t['all_section']}", "", t["head_all"], "|---|---|---|---|"]

    for r in report.results:
        lines.append(f"| {r.human_in(lang)} | `{r.field_name}` | {r.status} | {r.detail} |")

    return "\n".join(lines) + "\n"
