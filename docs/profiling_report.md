# Profiling report — measured on live data

🇺🇦 [Читати українською](profiling_report.uk.md) · [← README](../README.md)

**Date of measurement:** 2026-08-01
**Sources:** National Police `30b67898-1968-4d99-8058-298b56f22bff` (322 MB, updated
10:33 the same day), Ministry of Internal Affairs `5c6c156f-21ee-42cd-8da3-dcde6828be97`
(293 MB, frozen 2026-04-30)
**Tool:** DuckDB 1.5.5 over the raw JSON, with no intermediate conversion

Every number below comes from the live files. Where a measurement disproved the
assumption it started from, that is said outright: a disproved hypothesis saves
the next attempt at least as much as a confirmed one.

---

## 1. What was disproved

### 1.1. "Two IMEIs in one field (dual-SIM) — they must be split" — not confirmed

Records with 28–32 digits in `imei`: **0**. No splitting logic is written.

### 1.2. "`id` is unreliable for cross-source matching" — too cautious

`id` agrees between the Ministry and the Police on **1_048_567 of 1_050_227**
Ministry rows (99.84%). It is a working key, not a trap. The composite
`cross_key` of IMEI + NK + DK is unnecessary.

---

## 2. Volume and keys

| Metric | Police (2026-08-01) | Ministry (2026-04-30) |
|---|---:|---:|
| Rows | 1 062 509 | 1 050 227 |
| Distinct `id` | 1 062 509 | 1 042 572 |
| Duplicate `id` | **0** | **7 655** |

The Police publication is the cleaner one. Deduplication inside a snapshot is
needed only for Ministry revisions.

The Police schema is confirmed: the same eight fields in lower case,
`additionalProperties: false`, and no status field in either version.

**Lost semantics recovered.** The new Police JSON Schema dropped the field
`title` annotations; the archived Ministry schema keeps them, and it is the only
place where the meaning of `nz`, `nk`, `dk` and `dtl` is written down. Fixed in
[`src/wantedmt/config.py`](../src/wantedmt/config.py) (`FIELD_TITLES`).

---

## 3. Fill rates and dates

| Field | Empty |
|---|---:|
| `imei` | 0.000% |
| `ovd` | 0.001% |
| `nz` | 0.009% |
| `dk` | 9.333% |
| `nk` | 9.341% |
| `dtl` | **93.122%** |

`imei` is always populated — the scenario "cross-matching is impossible because
the IMEI is empty" does not arise.

`dtl` is empty in 93% of records, so as an enumeration of circumstances it is
unusable. The remaining 7% (38 270 distinct values) hold a mixture: country of
manufacture (`СТРАНА ИЗГОТОВИТЕЛЬ: КНДР.` — 2145), circumstances
(`Крадіжка` / `крадіжка` / `КРАЖА МОБ. ТЕЛЕФОНА`), and repeats of the brand.

**Dates: the format is impeccable, the values are not.** ISO-8601 violations:
**0** in `insert_date` and `dk`. And yet:

- `min(dk)` = **`0201-07-02`** (year 201)
- `min(insert_date)` = `1900-01-12`; ~383 records predate 1985, and 1985 itself
  carries a suspicious spike of 778
- 2559 records with an **empty** `insert_date`
- the lag `insert_date − dk`: median 1 day, p90 20 days, but the range runs from
  **−35 094** to **+665 069** days

This is the headline finding about dates: `format: "date-time"` in a JSON Schema
is an annotation, not a check, and it guarantees nothing about content. Range
validity has to be proved separately.

---

## 4. How the register moves

The register grows almost monotonically: 1 050 227 (April) → 1 062 509 (August),
+12 282 in about three months. Records disappear rarely.

That governs how `disappeared_at` is treated: disappearance is the **only**
closure signal the source offers (there is no status field), but it also covers
a corrected mistake, a reclassification and a failed export. So a snapshot that
loses more than 2% of the register is recorded in the manifest yet is **not
allowed to close anything**.

Separately: 14 of the 2384 Ministry revisions are smaller than half the normal
size (the smallest is 0.9 MB against 293 MB). A truncated file is more dangerous
than an empty one — it parses and it looks plausible. The floor of 700 000 rows
stands against exactly this.

---

## 5. IMEI

| Metric | Value |
|---|---:|
| 15 digits | 1 038 230 (97.7%) |
| 14 digits (no check digit) | 16 610 |
| 16 digits | 2 696 |
| 17–18 digits | 1 188 |
| ≤13 digits | ~3 500 |
| **Passing Luhn (among 15-digit values)** | **866 861 (83.49%)** |
| All digits identical | 31 |
| Starting with `00` | 206 |
| No digits at all | 14 |

**16.5% of length-valid IMEIs fail the check digit** — roughly 171 000 records
with a typo or a re-stamped identifier. One of the principal quality dimensions
of this dataset.

Junk in the field is rare and small: the most frequent non-digit characters are
`/` (12), `А` (11), `З` (11), `S` (9).

**TAC:** 75 011 distinct prefixes, very heavily concentrated:

| Records covered | TACs required |
|---|---:|
| 50% | 1 813 |
| 80% | 6 483 |
| 95% | 20 286 |

A dictionary of some 20 000 TACs covers 95% of the register, which makes
cross-validating "brand from NZ against brand from TAC" entirely practical.

RBI distribution: `35` — 775 668, `86` — 246 324, `01` — 25 693.

---

## 6. NZ (brand / model) — the main quality work

| Metric | Value |
|---|---:|
| Distinct spellings | 110 415 |
| After upper-casing | 105 621 |
| After collapsing whitespace | 101 537 |
| Share containing Cyrillic | 27.66% |

Normalising case and whitespace removes only 8% of the variation; the rest is
semantic.

**Look-alike characters were measured, and they are expensive:**

| Latin | Records | Cyrillic substitute | Records | Loss |
|---|---:|---|---:|---:|
| `XIAOMI` | 49 042 | `XІAOMІ` | 9 687 | 16.5% |
| `APPLE IPHONE` | 29 892 | `APPLE ІPHONE` | 9 389 | 23.9% |
| `HUAWEI` | 18 373 | `HUAWEІ` | 6 693 | 26.7% |
| `MEIZU` | 9 556 | `MEІZU` | 3 040 | 24.1% |

So up to a quarter of a brand's records hide behind one substituted `I` → `І`.

**The trap the brief does not see.** A naive "homoglyph fix: Cyrillic → Latin"
step **breaks transliterations**: `САМСУНГ` (27 844 records) turns into
`CAMCУHГ`, which matches nothing. The substitution may be applied **only to
strings that mix alphabets**; wholly Cyrillic ones (`САМСУНГ`, `НОКИА` — 25 023,
`СОНИЭРИКСОН` — 3 516) belong in the alias dictionary as words of their own.
Implemented in [`normalize/text.py`](../src/wantedmt/normalize/text.py).

The top spellings cover the market densely: `SAMSUNG` 116 037, `NOKIA` 51 403,
`XIAOMI` 49 042, `APPLE IPHONE` 29 892, `LENOVO` 27 270.

---

## Reproducing

```bash
uv run wantedmt profile --source npu
```

The queries behind this report are kept in
[`docs/profiling_queries.sql`](profiling_queries.sql).
