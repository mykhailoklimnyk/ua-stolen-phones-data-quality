from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

from wantedmt import dq

CONFIG = Path(__file__).resolve().parents[1] / "dq" / "checks.yml"

COLUMNS = {
    "record_key": "VARCHAR",
    "imei_raw": "VARCHAR",
    "imei_norm": "VARCHAR",
    "imei_kind": "VARCHAR",
    "imei_luhn_valid": "BOOLEAN",
    "tac": "VARCHAR",
    "rbi": "VARCHAR",
    "brand": "VARCHAR",
    "manufacturer": "VARCHAR",
    "snr": "VARCHAR",
    "check_digit": "VARCHAR",
    "model": "VARCHAR",
    "nz_method": "VARCHAR",
    "nz_raw": "VARCHAR",
    "ovd": "VARCHAR",
    "region": "VARCHAR",
    "insert_date": "DATE",
    "dk": "DATE",
    "insert_date_implausible": "BOOLEAN",
    "first_seen": "DATE",
    "last_seen": "DATE",
    "is_present": "BOOLEAN",
}


def config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _fields_only(tmp_path: Path) -> Path:
    """The real thresholds and the real severities, without the cross-table assertions."""
    cfg = config()
    cfg.pop("assertions", None)
    cfg["row_count_min"] = 0
    path = tmp_path / "checks.yml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    return path


def _table(model_share: float, rows: int = 1000) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE records_normalized ("
        + ", ".join(f"{name} {kind}" for name, kind in COLUMNS.items())
        + ")"
    )
    resolved = round(rows * model_share)
    con.execute(
        """
        INSERT INTO records_normalized
        SELECT CAST(i AS VARCHAR), '350000001234567', '350000001234567', 'IMEI', true,
               '35000000', '35', 'SAMSUNG', 'Samsung', '000123', '7',
               CASE WHEN i < ? THEN 'GALAXY A12' END,
               'exact', 'SAMSUNG', 'Київське УП', 'Київська область',
               DATE '2026-01-01', DATE '2026-01-01', false,
               DATE '2026-01-01', DATE '2026-08-01', true
        FROM range(?) t(i)
        """,
        [resolved, rows],
    )

    return con


def test_the_model_gate_blocks_the_collapse_it_was_written_for(tmp_path: Path) -> None:
    """Normalising without --external-tac took model coverage to 64.5%. That must stop a build."""
    con = _table(0.645)
    report = dq.run(con, _fields_only(tmp_path))
    con.close()
    assert "model" in {r.field_name for r in report.blocking}
    assert report.status == "FAIL"


def test_the_model_gate_passes_at_the_level_the_pipeline_actually_delivers(
    tmp_path: Path,
) -> None:
    con = _table(0.9695)
    report = dq.run(con, _fields_only(tmp_path))
    con.close()
    assert "model" not in {r.field_name for r in report.failures}


@pytest.mark.parametrize("field", ["brand", "model", "region", "imei_norm"])
def test_a_coverage_gate_can_actually_block(field: str) -> None:
    """`info` severity turns a gate into a comment: it reports and never stops anything."""
    spec = config()["fields"][field]
    assert "min_fill_rate" in spec
    assert spec["severity"] == "critical", f"{field} coverage cannot block the build"


def test_no_gate_sits_far_below_the_value_it_guards() -> None:
    """A floor 37 points under the measured share is not a floor. Measured 2026-08-03."""
    measured = {
        "brand": 0.9969,
        "model": 0.9695,
        "region": 0.9960,
        "imei_norm": 0.9964,
    }
    fields = config()["fields"]

    for name, actual in measured.items():
        gate = fields[name]["min_fill_rate"]
        assert gate <= actual, f"{name}: gate {gate} is above the measured {actual}"
        assert actual - gate <= 0.05, f"{name}: gate {gate} is {actual - gate:.0%} below reality"
