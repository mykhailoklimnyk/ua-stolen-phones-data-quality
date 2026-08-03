# How this dataset was built

🇺🇦 [Читати українською](methodology.uk.md) · [← README](../README.md)

Figures measured on the completed backfill, 2026-08-02. Every number here comes
from `wantedmt volume` and the store itself rather than from notes, so it can be
restated after any rerun instead of going stale in prose.

---

## The series

The portal keeps one revision per upload, reachable through `resource_show` →
`resource_revisions`. That is what makes history possible at all: the original
brief assumed the resource is overwritten in place and that a record's past could
only be accumulated going forward.

| | |
| --- | --- |
| Revisions in the series | 1_825 |
| Covering | 2019-07-15 … 2026-08-01 (2_574 days) |
| Folded | 1_776 — 1_700 from the Ministry, 76 from the National Police |
| Recognised as republished bytes | 41 |
| Refused as not the register | 7 |
| Failed | 1 (2021-08-30) |
| Files needing structural repair | 290 |

## Weight: published, moved, kept

Three different questions, three different answers, and quoting any one of them
as "the size of the dataset" would be wrong in a different way.

| | |
| --- | --- |
| Published by the portal | **536.05 GB** |
| Downloaded | 522.98 GB |
| Not fetched — the hash was already known | 12.60 GB |
| **Kept on disk** | **0.49 GB** |
| Kept ÷ published | **0.092%** |

Kept is 160 MB of store and 334 MB of parquet change log. The derived dataset is
roughly a thousand times lighter than seven years of source files, and any past
day can still be reconstructed from the change log without fetching anything.

Rows read: 1_844_634_671. Of those, 10_963_346 were dropped as ids repeating
inside a single file — the publisher's duplication, counted rather than assumed
away, because the count is the only sign a file was assembled twice.

## What the history adds

| | |
| --- | --- |
| Records ever seen | **1_186_719** |
| Present in the current publication | 1_062_497 |
| **Closed — gone from the register** | **124_222** |
| Disappeared and came back | 21_514 |

The 124_222 closed records are the point of the exercise. They are absent from
the file the portal serves today, so without this history nothing can be said
about them — not that they existed, not when they stopped. A further 21_514
records left and returned; read from the current file alone they look like rows
that never moved.

## Dates are readings, not events

Every lifecycle date here is the date the register was **read**. A record entered
somewhere between the previous reading and the one that first showed it, and the
distance between those two is not always a day:

| | |
| --- | --- |
| Observations — days the register was actually read | 1_773 |
| Longest gap between two observations | 190 days |
| Snapshots recorded but not trusted to close records | 52 |

Republished bytes are excluded from the observation calendar on purpose. Between
2020-09-28 and 2021-03-29 the portal served the file of 2020-09-22 nineteen more
times; counted as readings they would say the register was checked all winter and
never moved.

Each record therefore carries the width of its own uncertainty —
`first_seen_days`, `disappeared_days` — and a NULL there means unbounded rather
than zero. The monthly series carries `is_measured` for the same reason: a month
with no reading reports that it was not measured, instead of reporting no thefts.

## What was refused, and why

Nothing was dropped quietly. Each exclusion is recorded in `snapshots` with its
reason, and the counts above add up to the 1_825 revisions.

- **7 not the register.** Five 2020 dates carry a single 3-byte revision;
  2019-07-15 carries 891 KB against a normal 293 MB; 2021-10-21 arrives
  byte-for-byte as the listing promises and holds 556_969 records against a
  register of 1_028_000. These are recorded as skipped rather than failed —
  retrying a bad publication means halting on the same file for ever.
- **41 republications.** Recognised by the hash the listing states, so they cost
  no download at all.
- **1 failure.** 2021-08-30 was lost to a file-locking error during its repair
  and left behind deliberately: snapshots are full listings, so folding it after
  the run had moved on would overwrite the present rather than fill its gap. The
  observation calendar simply does not include that day.
- **52 suspect snapshots.** Recorded in full, including how many records were
  absent, but not permitted to close anything. The MVS→NPU handover is among
  them: a change of ministry is not evidence that a million phones were found.

## Personal data

418_636 subscriber telephone numbers were found across the series and are held
in `quarantine_personal`, a table the published view is not built from. See
[source_defects.md](source_defects.md) for where they hide — the field name
announces them in only one of the three shapes DTL takes.

The published view carries no column that could hold them, and a blocking check
asserts it against `information_schema` rather than against a query someone has
to remember to write.
