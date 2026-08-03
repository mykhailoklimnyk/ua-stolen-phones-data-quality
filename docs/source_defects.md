# What is wrong with the source, and how each defect was found

🇺🇦 [Читати українською](source_defects.uk.md) · [← README](../README.md)

Every entry here is a measurement against the published files, with the date it
was taken and the number it rests on. This is the working record behind the
pipeline's repair rules: a rule with no defect recorded against it should be
deleted, and a defect with no rule is an open item.

Nothing here is a criticism of the publishers. A register maintained for seven
years across a change of ministry is going to move; the point of writing the
movements down is that a reader of the derived dataset can tell a real change in
the register from an artefact of how it was exported.

---

## The register was frozen for 188 days

**Measured 2026-08-02.** Between 2020-09-28 and 2021-03-29 the portal published
19 revisions that are **byte-identical** to the one from 2020-09-22 — same
`file_hash_sum`, same length, same content. The register did not stand still for
six months; the export did.

| | |
| --- | --- |
| Last file with new content before the freeze | 2020-09-22 |
| Identical republications | 19 |
| Span | 188 days |
| First file with new content after | 2021-03-31 |

This matters twice over. A reader summing "records added per month" across that
window would conclude that phone theft in Ukraine stopped between September and
March. And any lifecycle date inside it is unknowable at better than 188-day
resolution: a phone entered on 2020-11-04 and recovered on 2021-01-10 appears in
neither file, so it appears never to have existed.

It was found by comparing the API's own `file_hash_sum` before downloading —
without that check the 19 files look like ordinary daily snapshots that happened
to contain no changes, which is exactly the wrong conclusion.

## The freeze and the format change are one event

The first file with new content after the freeze, 2021-03-31, is also the first
file that will not parse (see below). The pipeline that produced snapshots up to
2020-09-22 stopped; whatever replaced it in March 2021 emits a different and
structurally invalid document.

Treating these as two separate problems is what made them expensive to find. They
have one cause and one date.

## For seventeen months the files were not JSON

**Measured 2026-08-02 across the whole backfill.** 290 of the 1_776 folded
snapshots needed repairing, and they are not scattered — they are a single
unbroken window:

| Period | Repaired | Read directly |
| --- | --- | --- |
| 2019–2020 | 0 | 297 |
| 2021 | 240 | 0 |
| 2022 | 50 | 71 |
| 2023–2026 | 0 | 1_118 |

First broken file **2021-03-31**, last **2022-08-19**. Both dates sit against a
long silence: the defect appears on the first file after the 188-day freeze, and
the last file carrying it is the first one after the 177-day gap of 2022. The
export was rebuilt twice — the first rebuild broke it, the second fixed it.

Each affected file carries exactly **three** objects standing outside any array:

```text
…,"DTL":""}]{"ID":"4328685",…,"DTL":""}[{"ID":"3016046442393713",…
```

The array closes, a record follows on its own, and a new array opens. Every
reader rejects this — `json.loads` stops at the first one with `Extra data`, and
DuckDB's `read_json` reports malformed JSON. The records themselves are valid;
only the brackets around them are wrong, so the repair joins the fragments into
one array and discards nothing.

Left unhandled this cost **297 of 800** attempted snapshots in the first backfill.

## An empty date makes the whole file unreadable

**Measured 2026-08-02.** In the 2021-03-31 file, record 297_674 carries
`"INSERT_DATE": ""`. A reader that infers types from a sample of the first
20_480 rows has already decided the column is a timestamp, and refuses the file
for a value it considers impossible — not for malformed JSON, and with an error
message that points at the wrong problem.

Reading the whole file before deciding types resolves it and costs about five
seconds. Everything then arrives as text, which is what this pipeline wants:
normalisation happens later, deliberately, with the original kept beside it.

## Subscriber phone numbers travel in DTL

**Measured 2026-08-02.** `DTL` changes shape across the series, and in two of its
three shapes it carries the subscriber's telephone number with nothing in the
field name to say so.

| Era | Shape of DTL | Where the number is |
| --- | --- | --- |
| 2019 | array of structs | `DTL[].NOMER` — 223_354 distinct (record, number) pairs |
| 2021–2026 | plain string | the string **is** the number — 172_258 in one snapshot |

Prefixes in the 2021-03-31 file: 809, 806, 380, 097, 050, 805, 095, 096 — all
Ukrainian mobile ranges. It is present in the live publication too: 6_552 values
in the 2026-08-01 snapshot, 5_849 of them beginning 380.

Length separates them from IMEIs on evidence rather than by assumption: of the
51_500 fifteen-digit DTL values in that snapshot, **30_456 are character-for-
character the record's own IMEI**, and the rest are the second IMEI of a
dual-SIM handset. Nine to thirteen digits is the phone band with a digit of
margin either side.

Numbers are quarantined and never reach the published view. IMEIs stay.

## DTL churns for 2_500 records a day, and means something different each time

**Measured 2026-08-02** on the change logs of 2021-05-03 and 2021-05-04. The
register appears to churn 200× faster after March 2021 — 5 to 15 records a day
changed through 2020, against roughly 2_500 a day afterwards. Comparing the two
days field by field, every field agrees except one:

| Field | Records differing between consecutive days |
| --- | --- |
| ovd, insert_date, nz, imei, nk, dk | 0 |
| **dtl** | 1_285 |

So the churn is one column, and the column is the one with no settled meaning.
Of the 2_485 records that changed on 2021-05-04:

| What DTL became | Records |
| --- | --- |
| Another IMEI — the second handset | 1_157 |
| Nothing, or a subscriber number (quarantined) | 722 |
| **The record's own IMEI, restated** | 546 |
| Something else | 60 |

A field that on Monday holds the second SIM's IMEI, on Tuesday a copy of the
IMEI already in the IMEI column, and on Wednesday a telephone number, is not
carrying information about the phone. It is carrying whatever the export put
there that morning.

Two consequences a reader has to know about. `revision_no` counts these: a
record whose DTL flipped is recorded as revised, though nothing about the phone
changed. And "records changed on date X" is, after March 2021, mostly this.

This was checked for the obvious alternative — that the pipeline was inventing
changes — and it is not: of the 1_285 records marked changed on both days, **0**
have the same content hash on both. The movement is in the source.

## Six revisions are not the register

**Measured 2026-08-02.** Five dates in 2020 (06-19, 06-23, 06-28, 08-05, 08-26)
carry a single revision of **3 bytes**, and 2019-07-15 carries 891 KB against a
normal 293 MB — 2_781 records out of some 912_000.

A relative check cannot catch these: it compares a revision against the largest of
its own day, and on those days every revision is broken. They need an absolute
floor.

## Ids repeat inside a single file

**Measured 2026-08-01 and confirmed 2026-08-02.** The MVS snapshot of 2026-04-30
holds 1_050_227 rows for 1_042_572 distinct ids; 2021-03-31 holds 1_007_909 rows
for 1_000_579. The duplicates are the publisher's, not an artefact of reading.

They are collapsed on `id` and the count is recorded per snapshot, because the
number is the only sign that a file was assembled twice.

## The register stopped publishing on the day of the invasion

**Measured 2026-08-03.** The last file before the longest silence in the series
is dated **2022-02-23**. The full-scale invasion began the following morning.
Publication resumed 177 days later.

That much is a gap like the others in the table below. What sits underneath it is
not.

| | |
| --- | --- |
| Last file before the silence | 2022-02-23, 1_044_944 records |
| First file after | 2022-08-19, 1_044_996 records |
| Records gained across 177 days | **52** |
| Rate over the preceding three weeks | ~120 a day |

A file that gains 52 records in half a year has caught up with nothing, and it
had not: of the 55 records appearing in it for the first time, **every one
carries an `insert_date` in February 2022**. Nothing registered after the
invasion is in it. The real catch-up arrives on 2022-09-16 — 6_065 records in 28
days.

So publication and registration have to be read apart. By the register's own
`insert_date`, registration did not stop. It collapsed, then partly recovered:

| Month | Registered |
| --- | ---: |
| 2022-01 | 4_221 |
| 2022-02 | 3_425 |
| **2022-03** | **223** |
| 2022-04 | 862 |
| 2022-07 | 2_111 |
| 2023-02 | 1_963 |

March 2022 is 5% of January. Five months on, the rate is about half of pre-war,
and there it stays: the 2023 months sit near 1_900 against 4_500 before.

Where the records stopped coming from is the other half of the answer. The twelve
months before 2022-02-23 against the twelve months after:

| Region | Before | After | |
| --- | ---: | ---: | ---: |
| Kherson oblast | 1_087 | **0** | −100% |
| Donetsk oblast | 1_973 | 88 | −96% |
| Luhansk oblast | 985 | 183 | −81% |
| Kyiv city | 14_805 | 3_641 | −75% |
| Kharkiv oblast | 2_064 | 716 | −65% |
| Zakarpattia oblast | 367 | 362 | −1% |
| **All regions** | **55_660** | **20_604** | **−63%** |

The ordering is a map. Kherson, occupied for that entire window, reports exactly
zero. Zakarpattia, furthest west, is unchanged.

What a reader has to take from this: across 2022 a fall in this dataset is not a
fall in phone theft. It is a police service under invasion, an export that
stopped, and for some oblasts no reporting territory at all. Any per-month series
over that period belongs next to `is_measured` in the monthly coverage aggregate
and next to `first_seen_days`, which for the records in question is 177.

## Publication gaps of a month or more

**Measured 2026-08-02** from the revision listing:

| From | To | Days |
| --- | --- | --- |
| 2020-06-28 | 2020-08-05 | 38 |
| 2021-02-22 | 2021-03-25 | 31 |
| 2022-02-23 | 2022-08-19 | 177 |
| 2022-11-29 | 2023-01-26 | 58 |

The 177-day gap starts the day after the last file of February 2022.

A disappearance threshold expressed per day, rather than as a flat share, follows
from this: a snapshot arriving after six months should not be judged by the
standard for consecutive days, and equally no gap however long should license
emptying the register.
