# Rotation reconciliation

Every flight in the Phase 1 base table (`aa_base_2025_2026.parquet`, **1,641,071** rows) is in exactly one category
in `data/processed/rotations.parquet`. The counts below are produced by `build_rotations.reconciliation()`, which
raises an error if they do not add up, and are re-checked by the pytest `test_real_reconciliation_adds_up_to_base_total`
(plus synthetic cases in `tests/test_rotations.py`). The same table is in `notebooks/02_rotation_building.ipynb` and
`data/processed/rotation_reconciliation.csv`.

## Flights by category

Priority when several conditions apply: cancelled, diverted, missing tail, no valid UTC times (`other`), then chain position.

| Category | Flights | % | Meaning |
|---|---:|---:|---|
| usable_segment | 1,408,848 | 85.849 | Operated leg in a segment of 2 or more linked legs (363,041 segments) |
| single_leg_segment | 191,021 | 11.640 | Operated leg whose segment has one leg (kept, `usable = false`) |
| cancelled | 35,245 | 2.148 | Cancelled flight |
| diverted | 5,957 | 0.363 | Diverted flight |
| missing_tail | 0 | 0.000 | No tail number and not cancelled or diverted |
| other | 0 | 0.000 | No valid scheduled UTC times |
| **Total** | **1,641,071** | **100.000** | Equals the base table |

**Memo.** 11 flights have no tail number. All 11 are cancelled, so they are counted under `cancelled` by the priority order
(hence `missing_tail` = 0). They are not lost.

## Why flights are not in a usable segment

Chains end for the reasons below (legs that start or end a segment; one leg can be both). Counts of rejected pairs are in
`rotation_breaks.parquet`.

| Reason | Pairs in `rotation_breaks` | Notes |
|---|---:|---|
| cancelled | 41,574 | Next flight cancelled 18,566; previous cancelled 16,695; both 6,313 |
| swap_suspect | 33,461 | Next scheduled departure before previous scheduled arrival (tail swap) |
| airport_mismatch | 11,990 | Previous destination differs from next origin |
| diverted | 6,433 | Next 4,216; previous 2,200; both 17 |
| long_rest (not a break) | n/a | 488,691 segment starts after more than 300 minutes of scheduled ground time (overnight); starts a new operating day |

## Old v1.0 rule versus new v1.1 segment rule (tail-days)

A tail-day is one aircraft on one local FlightDate (478,938 of them have a tail number and 1,641,060 flights).

| Rule | Tail-days | Legs |
|---|---:|---:|
| **Old v1.0:** 2+ operated legs after cancelled legs are **removed**, no diverted leg, every pair continuous | **405,613** | 1,494,403 |
| **New v1.1:** tail-days with **at least one usable segment** | **384,708** | 1,408,848 |
| **New v1.1:** tail-days that are **one single usable segment** (closest like-for-like with the old rule) | **306,913** | 1,162,136 |
| New v1.1: usable segments (a tail-day can hold several) | 363,041 | 1,408,848 |

The new rule keeps **5.7% fewer legs** than the old one (1,408,848 versus 1,494,403). It is stricter by design: cancelled flights
and tail swaps now break chains rather than being skipped or flagged, and `usable` applies to a segment, not a whole tail-day.
(The Phase 1 audit reported 405,622 tail-days for the relaxed definition; the Phase 2 reimplementation gives 405,613.)
