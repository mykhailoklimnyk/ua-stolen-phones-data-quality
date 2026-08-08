# UA Stolen & Lost Phones — Data Quality Edition

[![CI](https://github.com/mykhailoklimnyk/ua-stolen-phones-data-quality/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mykhailoklimnyk/ua-stolen-phones-data-quality/actions/workflows/ci.yml)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21781640.svg)](https://doi.org/10.5281/zenodo.21781640)
[![data as of](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fmykhailoklimnyk%2Fua-stolen-phones-data-quality%2Fmain%2Fmanifest.json&query=%24.as_of&label=data%20as%20of&color=0e9f64&cacheSeconds=3600)](manifest.json)
[![records](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fmykhailoklimnyk%2Fua-stolen-phones-data-quality%2Fmain%2Fmanifest.json&query=%24.records.total&label=records&color=2563eb&cacheSeconds=3600)](manifest.json)
[![closed](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fmykhailoklimnyk%2Fua-stolen-phones-data-quality%2Fmain%2Fmanifest.json&query=%24.records.closed&label=closed&color=6b7280&cacheSeconds=3600)](manifest.json)
[![data](https://img.shields.io/badge/data-CC%20BY%204.0-blue)](LICENSE)
[![code](https://img.shields.io/badge/code-MIT-blue)](LICENSES/MIT.txt)

A normalised, deduplicated and quality-checked derivative of the Ukrainian open
dataset **«Інформація про викрадені, втрачені мобільні телефони»** (Information
on stolen and lost mobile phones), published by the **National Police of
Ukraine** on [data.gov.ua](https://data.gov.ua/dataset/30b67898-1968-4d99-8058-298b56f22bff)
under CC BY 4.0.

<a href="https://trofey.app"><img src="docs/assets/trofey-logo.svg" alt="Trofey" width="28" align="left" hspace="8"></a>
Produced by the [Trofey](https://trofey.app) project — a Ukrainian marketplace
analytics service that publishes what it measures.
🇺🇦 [Читати українською](README.uk.md)

> **This is not an official registry.** It is not affiliated with, endorsed by,
> or operated on behalf of any government body. The absence of a device from
> this dataset certifies nothing about that device — see
> [Limitations](#limitations).

---

## Why this exists

The source publication is one JSON file that is overwritten on every update. It
carries **eight string fields and no record status**. Two consequences follow,
and together they are the whole reason for this project:

1. **A record's lifecycle is invisible.** There is no "found" or "closed" flag.
   The only observable signal that a case ended is that the record stops
   appearing in the file — which no single download can show you.
2. **The field semantics were dropped.** The current publisher's JSON Schema
   removed the `title` annotations the archived predecessor carried, leaving
   eight three-letter abbreviations (`nz`, `nk`, `dk`, `dtl`) with no
   documentation anywhere.

This edition reconstructs the history from the portal's own revision archive,
restores the lost semantics, and normalises the two fields that carry the actual
information about the device: the free-text brand/model string and the IMEI.

## What it adds

| | Source | This edition |
|---|---|---|
| Record history | none — file is overwritten | `first_seen`, `last_seen`, `disappeared_at`, `revision_no` over 1 776 folded snapshots since 2019-07-15 |
| Field meaning | undocumented abbreviations | recovered from the archived MVS schema |
| Brand / model | one free-text field, 110 415 distinct spellings | resolved `brand` + `model`, with script-mixing repaired |
| IMEI | raw string, mixed lengths | 15-digit `imei_norm`, `tac`, `rbi`, Luhn validity, repair flag |
| Dates | ISO-shaped but reaching year 0201 | validated, implausible values nulled **and counted** |
| Duplicates | 7 655 repeated ids in the MVS publication | deduplicated per snapshot |
| Quality | not reported | per-build DQ report, blocking on structural defects |

## What it took, and what came out

Seven years of the register, rebuilt from the portal's own revision archive.
Every figure here comes from `wantedmt volume` and the store itself, so it is
restated after any rerun rather than going stale in prose.

| | |
| --- | --- |
| Revisions processed | **1 825** (2019-07-15 … 2026-08-01) |
| Rows read | **1 844 634 671** |
| Published by the portal | **536.05 GB** |
| Downloaded | 522.98 GB |
| Not fetched — the hash was already known | 12.60 GB |
| **Kept on disk** | **0.49 GB** — 0.092% of the source |
| Snapshots needing structural repair | 290 |

The derived dataset is roughly a thousand times lighter than the files it came
from, and any past day still rebuilds from the parquet change log without
fetching anything.

### What the source could not say

| | |
| --- | --- |
| Records ever seen | **1 186 719** |
| Present in the current publication | 1 062 497 |
| **Closed — gone from the register** | **124 222** |
| Left and came back | 21 514 |

The closed records are the point. They are absent from the file the portal
serves today, so without this history nothing can be said about them — not that
they existed, not when they stopped.

### Resolution

| | Source | This edition |
| --- | --- | --- |
| **Brand** | free text, 110 415 spellings | **99.7%** |
| **Model** | free text | **96.9%** |
| Region of the registering unit | free text | 99.6% |
| IMEI usable | — | 99.6% |
| IMEI passing the Luhn check | — | 83.6% |

Brand and model come from a field where sellers wrote whatever they liked:
`SAMSUNG`, `САМСУНГ`, `XІAOMІ` with Cyrillic look-alikes, and 126 451 records
saying nothing but the manufacturer's name. Where the text names no model, the
IMEI does — a TAC identifies the handset under the GSMA standard, and that is
what carries model resolution from 52.7% to 96.9%.

## Measured facts

Everything below is measured on live data, not assumed. Full numbers and the
queries behind them — every document exists in both languages:

| Document | English | Українською |
| --- | --- | --- |
| How the dataset was built | [methodology.md](docs/methodology.md) | [methodology.uk.md](docs/methodology.uk.md) |
| What is wrong with the source | [source_defects.md](docs/source_defects.md) | [source_defects.uk.md](docs/source_defects.uk.md) |
| Profiling report | [profiling_report.md](docs/profiling_report.md) | [profiling_report.uk.md](docs/profiling_report.uk.md) |

- **83.6%** of full-length IMEIs pass the Luhn check — the rest carry a typo or
  an altered identifier
- Up to **26.7%** of a brand's records hide behind a single Cyrillic look-alike
  character (`HUAWEІ` vs `HUAWEI`, `XІAOMІ` vs `XIAOMI`)
- **83 344** distinct TAC prefixes
- Dates are 100% ISO-8601 in *form*, yet `dk` reaches back to year **0201**
- The register was **frozen for 188 days** in 2020-2021: the portal reissued one
  file nineteen times, and counting those as readings would say phone theft
  stopped for a winter
- Publication stopped on **2022-02-23**, the day before the full-scale invasion,
  and resumed 177 days later with nothing registered after February in it.
  Registration itself fell to **5%** of January in March 2022; Kherson oblast
  reports **zero** records for the twelve months that followed

## Install and run

```bash
uv sync

uv run wantedmt timeline                 # discover every revision of both sources
uv run wantedmt backfill                 # fold them chronologically, oldest first
uv run wantedmt normalize                # rebuild IMEI + brand/model outputs
uv run wantedmt dq                        # run checks, render the report
uv run wantedmt unmatched --out docs/unmatched.md   # weekly review queue
uv run wantedmt export --out data/export  # publishable parquet + aggregates
uv run wantedmt lookup-export --out data/lookup   # NOT publishable, see below
```

`lookup-export` writes the two projections behind the free IMEI check on
trofey.app: one row per wanted number, and the full TAC dictionary. They are
deliberately outside `manifest.json` and outside every release — that dictionary
includes the MIT-licensed catalogue we may use but may not redistribute. The
files go to one database and nowhere else.

Daily operation folds only what is new:

```bash
uv run wantedmt watch --source npu    # one API call: is the portal ahead of us?
uv run wantedmt daily --source npu && uv run wantedmt normalize && uv run wantedmt dq --publish
```

`watch` exits 0 when there is a day to fold and 3 when there is not, which is
what [.github/workflows/watch.yml](.github/workflows/watch.yml) reads. Its
schedule is measured rather than assumed: of 89 National Police revisions,
66 landed in **10:30–10:39 UTC** and 10:30–15:00 covers 98.9% — and the day of
the week makes no difference, so the checks run at weekends too.

## How it works

```
data.gov.ua CKAN                    ┌──────────────┐
  resource_show ──► revision index  │  1 776 daily │
                                    │   snapshots  │
                                    └──────┬───────┘
                                           │ one at a time, chronological
                                    ┌──────▼───────┐
                                    │  fold (SCD2) │  first_seen / last_seen /
                                    │              │  disappeared_at / revision_no
                                    └──────┬───────┘
                                           │ raw file deleted immediately
                                    ┌──────▼───────┐
                                    │  normalize   │  IMEI · TAC · brand · model
                                    └──────┬───────┘
                                    ┌──────▼───────┐
                                    │  DQ + export │  report → R2 → trofey.app
                                    └──────────────┘
```

**No raw layer is kept, by design.** Storing all 1 776 snapshots would cost ~45 GB
of parquet whose only purpose is to re-derive the very same history. Each
snapshot is folded in and deleted. The trade is explicit: changing the fold logic
means re-downloading rather than reprocessing from disk. Bandwidth is cheap and
repeatable; cold storage of a CC-BY dataset is not worth its keep.

## Trust model for `disappeared_at`

Disappearance is the only closure signal the register offers, and it is treated
as evidence rather than truth. A record can vanish because the phone was found —
or because someone fixed a typo, reclassified the case, or the export failed.

Two guards, both measured rather than guessed:

- a snapshot that drops **more than 2%** of the register is recorded but is
  **never allowed to close anything** (the register grew monotonically over the
  measured period; a mass drop is a publication failure, not mass recovery);
- disappearance is only computed **within one publisher's series**. Across the
  MVS→NPU handover an absence means "migrated", not "found", and applying it
  would stamp a fake mass closure on the handover date.

## Limitations

- **Not real time, and not authoritative.** The dataset reflects what the
  publisher exported, when they exported it. A device missing from it may still
  be stolen; a device present in it may already have been returned.
- **No personal data.** Every field describes a device or a police unit. Nothing
  is added that would change that, and nothing here identifies people.
- **`disappeared_at` is a proxy**, not a confirmed case closure — see above.
- **Failed Luhn checks are reported, never repaired.** Rewriting a digit to
  satisfy a checksum would invent data.
- **TAC coverage is partial.** `TAC_UNKNOWN` is a legitimate third state, not
  "invalid" — every genuinely new model is unknown to free TAC dictionaries for
  a year or two.

## Licence and attribution

- **Data** — [CC BY 4.0](LICENSE), inherited from the source. The root `LICENSE`
  is the verbatim legal code, which is what makes GitHub name the licence
  instead of shrugging at it.
- **Code** — [MIT](LICENSES/MIT.txt), covering the pipeline in `src/wantedmt`.

Attribution is required by the source licence, and it is owed to **both** this
edition and the original publisher. Link to the specific dataset, not merely to
data.gov.ua:

> Trofey. *UA Stolen & Lost Phones — Data Quality Edition.*
> <https://doi.org/10.5281/zenodo.21781640>
>
> Derived from «Інформація про викрадені, втрачені мобільні телефони»,
> Національна поліція України, data.gov.ua, CC BY 4.0 —
> <https://data.gov.ua/dataset/30b67898-1968-4d99-8058-298b56f22bff>
>
> Historical coverage additionally derived from the archived publication of the
> Міністерство внутрішніх справ України, CC BY 4.0 —
> <https://data.gov.ua/dataset/5c6c156f-21ee-42cd-8da3-dcde6828be97>

Machine-readable citation metadata: [`CITATION.cff`](CITATION.cff).

## Third-party data

TAC labelling for codes the register cannot resolve on its own comes from
[MoazEb/tac-database](https://github.com/MoazEb/tac-database) under the MIT
Licence — see [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt). Those rows
enrich published records; the list is not redistributed as a dictionary.

## Who publishes this

[Trofey](https://trofey.app) — a Ukrainian service that measures the second-hand
electronics market and publishes what it measures.

- Site — [trofey.app](https://trofey.app)
- How the market figures are produced —
  [trofey.app/methodology](https://trofey.app/methodology)
- Bot — [@trofey_app_bot](https://t.me/trofey_app_bot)

This dataset is released for its own sake, not as a lead magnet: it is CC BY 4.0,
rebuildable from this repository alone, and depends on nothing Trofey operates.
