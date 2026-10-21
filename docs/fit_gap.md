# Fit-gap: what the project needs versus what BTS provides

Confirmed against the Phase 1 audit (`notebooks/01_data_audit.ipynb`, run on 1,641,071 AA flights,
2025-01-01 to 2026-08-31). Each gap has one **final fill decision**:

| Decision | Meaning |
|---|---|
| **BTS as-is** | Real data used unchanged |
| **Derived** | Computed from real data (and public reference data) by a documented rule |
| **Public reference** | Free public dataset added |
| **Assumption** | An adjustable parameter, labelled as an assumption in code, files and the UI |
| **Synthetic (stress only)** | Generated, used only in stress scenarios and always labelled |
| **Out of scope** | Not needed for this project, or not obtainable |

## Fit-gap table

| # | Need | In BTS? | Audit evidence | Fit | Final fill decision |
|---|---|---|---|---|---|
| 1 | Flight schedule: carrier, flight number, origin, dest, scheduled times, distance, block time | Yes (`CRSDepTime`, `CRSArrTime`, `CRSElapsedTime`, `Distance`, …) | No duplicates; no invalid scheduled times or distances; complete on all rows | Fits | **BTS as-is** |
| 2 | Outcomes: departure and arrival delay, cancelled, diverted | Yes | No missing timing values on operated flights; ArrDel15 consistent with ArrDelay on 100% of rows | Fits | **BTS as-is** |
| 3 | Observed propagated delay, to validate the simulator | Partly: `LateAircraftDelay` | Populated for 59.0% of late arrivals; mean 39.4 min; 46.3% of all late minutes. Only for ArrDelay >= 15 (100% of cases, none below) | Partial: censored below 15 min | **BTS as-is**, validated in aggregate and by hour / leg index, not flight by flight |
| 4 | Aircraft rotation: which aircraft flies which legs, in order | Only as flown (`Tail_Number`) | 478,938 tail-days. Phase 2: 1,408,848 legs (85.8%) are in 363,041 usable segments of 2+ legs; 191,021 are single-leg segments; cancelled and diverted flights break chains | Partial: as-flown, not as-planned | **Derived** (Phase 2): operated legs only, ordered by UTC departure, linked when airport and time are consistent; operating day = no rest over 300 min |
| 5 | Planned rotation fidelity (tail swaps) | No | 33,461 pairs (3.1% of candidate pairs) have the next departure scheduled before the previous arrival, so the tail changed after planning; concentrated at ORD (6.5%), DFW (5.6%), PHL, LAX, MIA; peaks in summer | Gap, cannot be fixed from BTS | **Derived, counted and broken**: swap pairs end a chain and are written to `rotation_breaks`; no synthetic repair. The optimiser assumes planned = operated rotation (limitation) |
| 6 | Airport time zones, to compare local BTS times | No | 137 airports; 0 left without a zone | Gap | **Public reference**: OurAirports lat/lon → IANA zone with `timezonefinder`; OpenFlights fallback (needed for PBI) |
| 7 | UTC times and scheduled ground time | No | UTC block time matches BTS `CRSElapsedTime` for 99.99994% of flights; the one mismatch is a BTS DST error on 2025-11-02 | Gap, closed | **Derived** |
| 8 | Minimum turnaround time (MTT) | No | Median scheduled ground 60 min; under pressure (inbound and outbound both late) the actual ground time has a 10th percentile of 42 min overall, 46-48 at high-volume stations and 37-39 elsewhere | Gap | **Assumption**: 10th percentile of actual ground time on time-pressured **train** turns by station and time-of-day band, fallback to station class then global, floor 25 min, cap 60 min; sensitivity at p05 / p10 / p20 (zero-buffer share 5.7% / 7.7% / 13.9%) |
| 9 | Planned ground time and buffer = max(0, scheduled ground time - MTT) | No | Median planned buffer 14 min (mean 26); 7.7% of turns have zero buffer, 50.9% under 15 min; 6.9% are scheduled below MTT | Gap, closed given the MTT assumption | **Derived from the MTT assumption** (scheduled ground time itself is **derived** from BTS schedule and UTC conversion) |
| 10 | Schedule-only predictors: hour, day, month, route, distance, airport | Yes | Complete | Fits | **BTS as-is** (`crs_dep_min`, `DayOfWeek`, `Month`, …) |
| 11 | Congestion proxy (scheduled departures per airport-hour) | Not directly | Computable from the schedule, known before departure | Gap, closable | **Derived** from the BTS schedule |
| 12 | Pre-departure weather forecast | No: BTS only has `WeatherDelay` after the fact | 86% of cancellations are weather-coded, so weather matters; no forecast is available | Gap | **Out of scope** for v1 (outcome fields can't be features). Real storm days stay in the data; synthetic storms are used for stress tests |
| 13 | Storm and disruption stress scenarios | No | A real storm occurred 24–28 Jan 2026 (up to 67% of flights cancelled in a day) | Gap | **Synthetic (stress only)**, labelled `scenario != 'base'`; the January 2026 event is a real-data cross-check |
| 14 | Calendar effects (holidays) | No | Thanksgiving 2025 is the lowest-volume day (1,251 flights) | Minor gap | **Public reference** (US federal holidays), optional feature |
| 15 | Aircraft type or fleet group | No | Tail numbers present, type not | Gap | **Out of scope** for v1 (optional later enrichment from the public FAA registry) |
| 16 | International and regional legs | No: BTS is domestic reporting carriers only | Unexplained chain breaks (1.5% of pairs) concentrate at hubs: DFW, MIA, CLT, ORD, PHL | Gap, cannot be filled | **Out of scope**; affected tail-days dropped from rotations |
| 17 | Cost of a buffer minute and of a delay minute | No | n/a | Gap | **Assumption**: adjustable values in the manifest, clearly labelled, with sensitivity analysis |
| 18 | Daily buffer budget and per-turn maximum | No | n/a | Gap | **Assumption**: default 500 min/day, varied on a budget grid |
| 19 | Crew, passenger connections, maintenance | No | n/a | Gap | **Out of scope** |

## Corrections after the audit

- **Phase 2 update.** Planned ground time, MTT and planned buffer moved from gap to derived / assumption (rows 7-9). Swaps are now
  counted and break chains instead of being flagged (row 5).
- **Rotations are not "BTS as-is".** The charter-level plan could suggest the BTS tail sequence is the
  planned rotation. The audit shows it is the *as-flown* sequence: 3.0% of continuous pairs conflict with
  the schedule. Rotations are therefore **derived**, and swap-affected turns are flagged.
- **Time zones are derived, not downloaded.** OurAirports has coordinates but no IANA zone, so zones come
  from `timezonefinder`. One airport (PBI) needed the OpenFlights fallback because OurAirports now lists
  Palm Beach under a different code.
- **The 76.43% on-time benchmark (D1 charter, Cirium) is context only.** BTS domestic mainline gives
  74.29% for 2025; scope and definition differences are discussed in the audit notebook (section 4).
- **`LateAircraftDelay` is only a partial validation target** because it is censored below 15 minutes.
