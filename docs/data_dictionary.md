# Data dictionary — AA base table

File: `data/interim/aa_base_2025_2026.parquet` (built by `src/bufferengine/pipeline/build_base.py`).
One row per scheduled AA mainline flight (domestic, as reported to BTS), Jan 2025 – Aug 2026. No rows
are dropped except non-AA carriers. Sorted by FlightDate, Tail_Number, CRSDepTime, flight number.

**Source:** BTS = Bureau of Transportation Statistics, *Reporting Carrier On-Time Performance
(1987-present)*, monthly PREZIP files; descriptions follow the BTS field definitions.
**Derived** = computed in `build_base.py` from BTS fields.

**Feature?** says whether the column may be used as a prediction feature. **YES** only for values known
before departure (schedule and calendar). **NO** for anything observed during or after the flight. The
target is `ArrDel15`. Outcome fields remain in the table for labels, validation of the propagation
simulator (`LateAircraftDelay`) and evaluation, never as model inputs.

Times are **local** clock times at the airport named (origin for departures, destination for
arrivals), as hhmm integers (`705` = 07:05, `2400` = midnight at the end of the day).

| Column | Type | Description | Source | Feature? |
|---|---|---|---|---|
| Year | int16 | Calendar year of FlightDate | BTS | YES |
| Quarter | int8 | Quarter (1–4) | BTS | YES |
| Month | int8 | Month (1–12) | BTS | YES |
| DayofMonth | int8 | Day of month | BTS | YES |
| DayOfWeek | int8 | Day of week, 1 = Monday … 7 = Sunday | BTS | YES |
| FlightDate | date | Scheduled departure date, local at origin | BTS | YES (via calendar features; also split key) |
| Reporting_Airline | string | Carrier code; always `AA` after filtering | BTS | NO (constant) |
| Tail_Number | string | Aircraft registration that **operated** the flight; trimmed, upper-cased, empty → null | BTS, normalised | NO as a direct feature: as-flown, can reflect delay-driven tail swaps. Used as the rotation key |
| Flight_Number_Reporting_Airline | int32 | Marketing flight number | BTS | YES (schedule identifier; high cardinality) |
| OriginAirportID | int32 | BTS stable airport ID of origin | BTS | YES |
| Origin | string | Origin airport IATA code | BTS | YES |
| OriginCityName | string | Origin city, state | BTS | YES (redundant with Origin) |
| OriginState | string | Origin state code | BTS | YES |
| DestAirportID | int32 | BTS stable airport ID of destination | BTS | YES |
| Dest | string | Destination airport IATA code | BTS | YES |
| DestCityName | string | Destination city, state | BTS | YES (redundant with Dest) |
| DestState | string | Destination state code | BTS | YES |
| CRSDepTime | int16 | Scheduled departure time, local hhmm (CRS = computer reservation system) | BTS | YES |
| DepTime | int16 (nullable) | Actual gate departure time, local hhmm | BTS | NO (actual) |
| DepDelay | float64 | Actual minus scheduled departure, minutes; negative = early | BTS | NO (outcome) |
| DepDel15 | int8 (nullable) | 1 if DepDelay >= 15 | BTS | NO (outcome) |
| DepTimeBlk | string | Scheduled departure hour block, e.g. `0600-0659` (from CRSDepTime) | BTS | YES |
| TaxiOut | float64 | Taxi-out minutes | BTS | NO (actual) |
| WheelsOff | int16 (nullable) | Wheels-off time, local hhmm | BTS | NO (actual) |
| WheelsOn | int16 (nullable) | Wheels-on time, local hhmm | BTS | NO (actual) |
| TaxiIn | float64 | Taxi-in minutes | BTS | NO (actual) |
| CRSArrTime | int16 | Scheduled arrival time, local hhmm at destination | BTS | YES |
| ArrTime | int16 (nullable) | Actual gate arrival time, local hhmm | BTS | NO (actual) |
| ArrDelay | float64 | Actual minus scheduled arrival, minutes; negative = early | BTS | NO (outcome) |
| ArrDelayMinutes | float64 | max(ArrDelay, 0) | BTS | NO (outcome) |
| ArrDel15 | int8 (nullable) | 1 if ArrDelay >= 15. **Prediction target** | BTS | NO (target) |
| Cancelled | int8 | 1 if cancelled | BTS | NO (outcome; used as a filter) |
| CancellationCode | string | A = carrier, B = weather, C = National Air System, D = security | BTS | NO (outcome) |
| Diverted | int8 | 1 if diverted | BTS | NO (outcome; used as a filter) |
| CRSElapsedTime | float64 | Scheduled block time, minutes (gate to gate) | BTS | YES |
| ActualElapsedTime | float64 | Actual block time, minutes | BTS | NO (actual) |
| AirTime | float64 | Airborne minutes | BTS | NO (actual) |
| Distance | float64 | Great-circle distance between airports, miles | BTS | YES |
| CarrierDelay | float64 | Minutes of arrival delay attributed to the carrier (late arrivals only) | BTS | NO (outcome) |
| WeatherDelay | float64 | Minutes attributed to extreme weather | BTS | NO (outcome) |
| NASDelay | float64 | Minutes attributed to the National Airspace System | BTS | NO (outcome) |
| SecurityDelay | float64 | Minutes attributed to security | BTS | NO (outcome) |
| LateAircraftDelay | float64 | Minutes attributed to the previous flight with the same aircraft arriving late (propagated delay). Validation target for the simulator | BTS | NO (outcome) |
| crs_dep_min | int16 | CRSDepTime as minutes after local midnight (2400 → 1440) | Derived | YES |
| crs_arr_min | int16 | CRSArrTime as minutes after local midnight (2400 → 1440) | Derived | YES |

## Notes

- **Delay-cause columns** are populated only when ArrDelay >= 15 and the flight operated; they are
  null otherwise (see the audit, section 2). For causes, null means "not a late arrival", not "unknown".
- **Nullable integers** (`DepTime`, `ArrDel15`, …) are null for cancelled flights, and arrival fields are
  null for diverted flights.
- **Coverage:** BTS on-time data covers **domestic** flights only. AA international legs (e.g. to Europe
  or Latin America) are absent, which breaks some aircraft chains; see `docs/fit_gap.md`.
- **Mainline only:** regional flights branded American Eagle are reported under their operating
  carriers (e.g. MQ, OH, YX), so `Reporting_Airline = AA` gives mainline only.
- Schedule-derived features computed later (e.g. leg position in the day, planned ground time) are
  allowed in principle, but anything derived from the **as-flown** tail sequence can leak delay-driven
  swaps; Phase 3 must decide how to build them from schedule information only.
