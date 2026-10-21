# D2 audit evidence: index

All tables and figures here are produced by `python -m bufferengine.pipeline.d2_evidence` (also run by `run_all`) from the cleaned
dataset `aa_clean_v1.0`, the Phase 2 rotation tables and the raw monthly BTS files. Figures are PNG at 200 dpi, single-panel and about 3.9 inches wide with 8 pt text, so they stay readable at half page width.
Rates are percentages. "Operated" means neither cancelled nor diverted. The late-arrival rate is the share of operated flights with `ArrDel15 = 1`.

For the treatment applied to each issue see `data/processed/dq_treatment_log.csv`; for the summary of who or what is under-represented see
`representativeness_summary.md`.

## 3. Quality evidence

| Topic | Table (`tables/`) | Figure (`figures/`) |
|---|---|---|
| Missing values by column, structural versus unexpected | `missing_by_column.csv` | `q_missing_by_column.png` |
| Duplicates (flight key, tail and key, flight_id, exact rows, same aircraft on two flights at once) | `duplicates.csv` | |
| Outliers with before and after counts (arrival and departure delay, taxi times, elapsed versus scheduled, distance) | `outliers_before_after.csv`, `delay_before_after_summary.csv` | `q_arr_delay_original.png` and `q_arr_delay_capped.png` (before and after), `q_taxi_out.png`, `q_taxi_in.png`, `q_elapsed_vs_scheduled.png`, `q_distance.png` |
| Time conflicts (the flights behind `dq_time_conflict`) | `time_conflicts.csv` | |

## 4. Representativeness and bias

| Brief item | Table (`tables/`) | Figure (`figures/`) |
|---|---|---|
| a) AA share of all reporting-carrier flights per month (raw files, all carriers), and AA versus all-carrier rates | `aa_share_by_month.csv` | `r_aa_share_by_month.png`, `r_aa_vs_all_delay_rate.png` |
| b) Share of flights by airport (top hubs versus small airports), hour of day, month, day of week | `origin_airport_share.csv`, `origin_airport_groups.csv`, `by_hour.csv`, `by_month.csv`, `by_month_of_year.csv`, `by_day_of_week.csv` | `r_airport_concentration.png`, `r_hour_of_day.png`, `r_month_share.png`, `r_weekday_share.png` |
| c) Delay and cancellation rates by those groups | the same tables (columns `cancelled_pct`, `diverted_pct`, `arr_del15_pct`) | `r_hour_of_day.png`, `r_rates_by_month.png`, `r_rates_by_weekday.png` |
| d) Share of tail-days that are usable (by hub and by month) and the tail-swap share | `usable_by_month.csv`, `usable_by_airport_group.csv`, `usable_flights_by_airport_group.csv`, `swaps_by_month.csv`, `swaps_by_airport_group.csv` | `r_usable_by_month.png`, `r_swaps_by_month.png`, `r_usable_by_airport_group.png`, `r_swaps_by_airport_group.png` |
| e) Non-US origins or destinations (domestic-service check) | `country_check.csv`, `non_50_state_airports.csv`, `state_codes.csv` | |
| f) January 2026 storm days versus normal days | `storm_vs_normal.csv` | `r_storm_vs_normal.png` |
| g) Months absent from the test window | `split_coverage_by_month_of_year.csv`, `split_by_month.csv` | `r_split_coverage.png` |

Notes on definitions: the airport groups are ranks by number of departures (top 10, 11 to 30, other); "usable tail-day" means a tail-day with at least one chain of two or more
linked operated legs (see `docs/data_contract.md`, section a); the swap rate is swap-suspect pairs per 100 candidate pairs (swap pairs plus linked turns).

## Safety and licences

`safety_scan.md` is the public-repo safety scan. Licence wording is in `data/reference/source_log.csv` and `DATA_NOTICE.md`.
