"""TAC-derived brand and model."""

from __future__ import annotations

import duckdb
import structlog

from ..sql import count
from .text import resolve

log = structlog.get_logger()


DDL = """
CREATE TABLE IF NOT EXISTS dict_tac (
    tac          VARCHAR,
    brand        VARCHAR,
    manufacturer VARCHAR,
    model        VARCHAR,
    release_year INTEGER,
    records      INTEGER,
    purity       DOUBLE,
    source       VARCHAR
);
"""


MIN_RECORDS = 5

MIN_PURITY = 0.9

MIN_MODEL_PURITY = 0.8


def build(
    con: duckdb.DuckDBPyConnection,
    min_records: int = MIN_RECORDS,
    min_purity: float = MIN_PURITY,
) -> int:
    """Derive TAC -> brand/manufacturer/model from records the text resolved. Built from …"""
    con.execute(DDL)
    con.execute("DELETE FROM dict_tac WHERE source = 'register'")
    con.execute(f"""
        INSERT INTO dict_tac
        WITH b AS (
            SELECT tac, brand, manufacturer, count(*) AS c,
                   sum(count(*)) OVER (PARTITION BY tac) AS tot,
                   row_number() OVER (PARTITION BY tac ORDER BY count(*) DESC) AS rn
            FROM records_normalized
            WHERE tac IS NOT NULL AND brand IS NOT NULL AND imei_luhn_valid
            GROUP BY tac, brand, manufacturer
        ),
        m AS (
            SELECT tac, model, count(*) AS c,
                   sum(count(*)) OVER (PARTITION BY tac) AS tot,
                   row_number() OVER (PARTITION BY tac ORDER BY count(*) DESC) AS rn
            FROM records_normalized
            WHERE tac IS NOT NULL AND model IS NOT NULL AND imei_luhn_valid
            GROUP BY tac, model
        )
        SELECT b.tac, b.brand, b.manufacturer,
               CASE WHEN m.c * 1.0 / m.tot >= {MIN_MODEL_PURITY} THEN m.model END,
               NULL, b.tot, round(b.c * 1.0 / b.tot, 4), 'register'
        FROM b LEFT JOIN m ON m.tac = b.tac AND m.rn = 1
        WHERE b.rn = 1 AND b.tot >= {min_records} AND b.c * 1.0 / b.tot >= {min_purity}
    """)
    con.execute("CREATE TABLE IF NOT EXISTS dict_tac_ext_model (tac VARCHAR, model VARCHAR)")
    refilled = con.execute("""
        UPDATE dict_tac AS d SET model = x.model
        FROM dict_tac_ext_model AS x
        WHERE d.tac = x.tac AND d.source = 'register' AND d.model IS NULL
    """).fetchone()

    if refilled is not None and int(refilled[0]):
        log.info("tac.register_models_refilled", rows=int(refilled[0]))

    return count(con, "SELECT count(*) FROM dict_tac WHERE source = 'register'")


def load_external(con: duckdb.DuckDBPyConnection, csv_path: str, name: str) -> int:
    """Merge an outside TAC list for TACs the register cannot label itself. Only TACs absent …"""
    con.execute(DDL)
    con.execute("DELETE FROM dict_tac WHERE source = ?", [name])
    raw_brands = con.execute(f"""
        SELECT DISTINCT upper(trim(Brand)) FROM read_csv('{csv_path}', header=true,
            ignore_errors=true, columns={{Brand:'VARCHAR', TAC:'VARCHAR', SPECS:'VARCHAR'}})
        WHERE Brand IS NOT NULL
    """).fetchall()
    mapped = []

    for (raw,) in raw_brands:
        r = resolve(raw)
        mapped.append((raw, r["brand"], r["manufacturer"]))
    con.execute(
        "CREATE OR REPLACE TEMP TABLE ext_brand (raw VARCHAR, brand VARCHAR, manufacturer VARCHAR)"
    )
    con.executemany("INSERT INTO ext_brand VALUES (?, ?, ?)", mapped)
    con.execute(f"""
        INSERT INTO dict_tac
        SELECT e.tac, coalesce(bm.brand, e.brand), coalesce(bm.manufacturer, e.brand),
               coalesce(
                   nullif(trim(regexp_replace(
                       upper(trim(split_part(e.specs, ',', 1))), '^' || e.brand, '')), ''),
                   nullif(trim(regexp_replace(
                       upper(trim(split_part(e.specs, ',', 2))), '^' || e.brand, '')), '')
               ),
               try_cast(regexp_extract(e.specs, '\b(19|20)[0-9]{{2}}\b') AS INTEGER),
               0, NULL, '{name}'
        FROM (
            SELECT tac, any_value(brand) AS brand, min(specs) AS specs
            FROM (
                SELECT lpad(trim(TAC), 8, '0') AS tac, upper(trim(Brand)) AS brand,
                       trim(SPECS) AS specs
                FROM read_csv('{csv_path}', header=true, ignore_errors=true,
                              columns={{Brand:'VARCHAR', TAC:'VARCHAR', SPECS:'VARCHAR'}})
                WHERE regexp_matches(trim(TAC), '^[0-9]+$')
            ) GROUP BY tac
        ) e
        LEFT JOIN ext_brand bm ON bm.raw = e.brand
        WHERE NOT EXISTS (SELECT 1 FROM dict_tac d WHERE d.tac = e.tac)
    """)
    con.execute("CREATE TABLE IF NOT EXISTS dict_tac_ext_model (tac VARCHAR, model VARCHAR)")
    con.execute("DELETE FROM dict_tac_ext_model")
    con.execute(f"""
        INSERT INTO dict_tac_ext_model
        SELECT tac, any_value(model) FROM (
            SELECT lpad(trim(TAC), 8, '0') AS tac,
                   coalesce(
                       nullif(trim(regexp_replace(upper(trim(split_part(trim(SPECS), ',', 1))),
                                                  '^' || upper(trim(Brand)), '')), ''),
                       nullif(trim(regexp_replace(upper(trim(split_part(trim(SPECS), ',', 2))),
                                                  '^' || upper(trim(Brand)), '')), '')
                   ) AS model
            FROM read_csv('{csv_path}', header=true, ignore_errors=true,
                          columns={{Brand:'VARCHAR', TAC:'VARCHAR', SPECS:'VARCHAR'}})
            WHERE regexp_matches(trim(TAC), '^[0-9]+$')
        ) WHERE model IS NOT NULL GROUP BY tac
    """)
    filled = con.execute(f"""
        UPDATE dict_tac AS d
        SET model = e.model
        FROM (
            SELECT tac, any_value(model) AS model FROM (
                SELECT lpad(trim(TAC), 8, '0') AS tac,
                       coalesce(
                           nullif(trim(regexp_replace(upper(trim(split_part(trim(SPECS), ',', 1))),
                                                      '^' || upper(trim(Brand)), '')), ''),
                           nullif(trim(regexp_replace(upper(trim(split_part(trim(SPECS), ',', 2))),
                                                      '^' || upper(trim(Brand)), '')), '')
                       ) AS model
                FROM read_csv('{csv_path}', header=true, ignore_errors=true,
                              columns={{Brand:'VARCHAR', TAC:'VARCHAR', SPECS:'VARCHAR'}})
                WHERE regexp_matches(trim(TAC), '^[0-9]+$')
            ) WHERE model IS NOT NULL GROUP BY tac
        ) AS e
        WHERE d.tac = e.tac AND d.source = 'register' AND d.model IS NULL
    """).fetchone()

    if filled is not None:
        log.info("tac.register_models_filled_from_external", rows=int(filled[0]))

    return count(con, "SELECT count(*) FROM dict_tac WHERE source = ?", [name])


def fill(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Fill missing brand/model per record from that record's own TAC. The unit is the record, …"""
    filled_brand = count(
        con,
        """
        SELECT count(*) FROM records_normalized r JOIN dict_tac d ON d.tac = r.tac
        WHERE r.brand IS NULL AND d.brand IS NOT NULL
        """,
    )
    filled_model = count(
        con,
        """
        SELECT count(*) FROM records_normalized r JOIN dict_tac d ON d.tac = r.tac
        WHERE r.model IS NULL AND d.model IS NOT NULL
        """,
    )
    con.execute("""
        CREATE OR REPLACE TABLE records_normalized AS
        SELECT r.* REPLACE (
            coalesce(r.brand, d.brand)               AS brand,
            coalesce(r.manufacturer, d.manufacturer) AS manufacturer,
            coalesce(r.model, d.model)               AS model
        ),
        d.release_year,
        CASE WHEN r.brand IS NOT NULL THEN 'text'
             WHEN d.brand IS NOT NULL THEN 'tac:' || d.source END AS brand_source,
        CASE WHEN r.brand IS NULL AND d.brand IS NOT NULL THEN d.purity END AS tac_confidence,
        r.brand IS NOT NULL AND d.brand IS NOT NULL AND r.brand <> d.brand AS brand_conflict,
        (r.brand IS NULL OR r.model IS NULL) AND d.tac IS NOT NULL
            AND coalesce(r.imei_luhn_valid, false) = false AS tac_from_invalid_imei
        FROM records_normalized r
        LEFT JOIN dict_tac d ON d.tac = r.tac
    """)
    conflicts = count(con, "SELECT count(*) FROM records_normalized WHERE brand_conflict")
    unreliable = count(con, "SELECT count(*) FROM records_normalized WHERE tac_from_invalid_imei")

    return {
        "brand_filled_from_tac": filled_brand,
        "model_filled_from_tac": filled_model,
        "brand_conflicts": conflicts,
        "filled_from_invalid_imei": unreliable,
    }
