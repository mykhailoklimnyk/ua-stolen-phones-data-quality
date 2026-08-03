from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from wantedmt import dq, reports

ROOT = Path(__file__).resolve().parents[1]

UK_SUFFIX = ".uk.md"


def markdown_docs() -> list[Path]:
    return sorted([*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md")])


def english_of(path: Path) -> Path:
    return path.with_name(path.name[: -len(UK_SUFFIX)] + ".md")


def ukrainian_of(path: Path) -> Path:
    return path.with_name(path.name[: -len(".md")] + UK_SUFFIX)


def test_the_repository_actually_has_documents() -> None:
    assert len(markdown_docs()) >= 8


@pytest.mark.parametrize("doc", markdown_docs(), ids=lambda p: p.name)
def test_every_document_exists_in_both_languages(doc: Path) -> None:
    sibling = english_of(doc) if doc.name.endswith(UK_SUFFIX) else ukrainian_of(doc)
    assert sibling.exists(), (
        f"{doc.name} has no {sibling.name}. A translated page is edited in both "
        f"languages in the same change, or the untranslated one goes stale."
    )


@pytest.mark.parametrize("doc", markdown_docs(), ids=lambda p: p.name)
def test_every_document_links_to_its_translation(doc: Path) -> None:
    sibling = english_of(doc) if doc.name.endswith(UK_SUFFIX) else ukrainian_of(doc)
    text = doc.read_text(encoding="utf-8")
    assert f"({sibling.name})" in text, f"{doc.name} does not link to {sibling.name}"


@pytest.mark.parametrize("doc", markdown_docs(), ids=lambda p: p.name)
def test_relative_links_point_at_files_that_exist(doc: Path) -> None:
    targets = re.findall(r"\]\(([^)#:]+?\.(?:md|py|sql|cff|txt|json|sh))\)", doc.read_text("utf-8"))
    missing = [t for t in targets if not (doc.parent / t).exists()]
    assert not missing, f"{doc.name} links to files that do not exist: {missing}"


@pytest.mark.parametrize("doc", markdown_docs(), ids=lambda p: p.name)
def test_the_pair_carries_the_same_sections(doc: Path) -> None:
    if doc.name.endswith(UK_SUFFIX):
        pytest.skip("checked once per pair, from the English side")
    other = ukrainian_of(doc)

    def depths(path: Path) -> list[int]:
        return [
            len(m.group(1))
            for m in re.finditer(r"^(#{1,3}) ", path.read_text("utf-8"), flags=re.MULTILINE)
        ]

    assert depths(doc) == depths(other), (
        f"{doc.name} and {other.name} have different heading structures — "
        f"one of them gained a section the other did not"
    )


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE records_normalized AS
        SELECT 'k' AS record_key, 'SAMSUNG' AS nz_raw, 'SAMSUNG' AS brand,
               'GALAXY' AS model, '35000000' AS tac, '350000001234567' AS imei_norm,
               true AS imei_luhn_valid, false AS imei_was_repaired, 'IMEI' AS imei_kind,
               false AS homoglyph_fixed, true AS is_present,
               CAST(NULL AS DATE) AS disappeared_at, false AS insert_date_implausible
    """)
    con.execute("""
        CREATE TABLE dict_nz_mapping AS
        SELECT 'ЯКАСЬ МАРКА' AS nz_raw, 'ЯКАСЬ МАРКА' AS nz_clean,
               CAST(NULL AS VARCHAR) AS brand, 'text' AS method
    """)
    con.execute(
        "INSERT INTO records_normalized "
        "SELECT * REPLACE ('ЯКАСЬ МАРКА' AS nz_raw) FROM records_normalized"
    )

    return con


def test_the_review_queue_renders_in_both_languages() -> None:
    con = _con()
    english = reports.weekly_review(con, 10, "en", "unmatched.uk.md")
    ukrainian = reports.weekly_review(con, 10, "uk", "unmatched.md")
    con.close()
    assert "Weekly review" in english and "unmatched.uk.md" in english
    assert "Тижневий огляд" in ukrainian and "unmatched.md" in ukrainian
    assert english != ukrainian
    assert english.count("|") == ukrainian.count("|")


def test_the_quality_report_renders_in_both_languages() -> None:
    report = dq.DQReport("phones", 10, generated_at="2026-08-03T00:00:00+00:00")
    report.results.append(dq.CheckResult("imei_norm", "not_null", "FAIL", "critical", 10, 3, "70%"))
    english = dq.to_markdown(report, "en", "latest.uk.md")
    ukrainian = dq.to_markdown(report, "uk", "latest.md")
    assert "Field is populated" in english and "latest.uk.md" in english
    assert "Поле заповнене" in ukrainian and "latest.md" in ukrainian
    assert english.count("\n") == ukrainian.count("\n")
