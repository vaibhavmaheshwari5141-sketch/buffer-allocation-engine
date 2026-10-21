# Data notice

This project processes **public data**. This notice says where the data come from, under which wording each source is published (as recorded on the access date), and what we do and do not claim. It is not legal advice.

## What the data are

- The flight records derive from the **public Bureau of Transportation Statistics (BTS) "Reporting Carrier On-Time Performance (1987-present)" monthly files**, January 2025 to August 2026. From the files for all reporting carriers we keep the rows of one carrier (American Airlines, code AA, mainline) and 43 columns.
- Airport coordinates come from OurAirports (and one airport from OpenFlights) and are turned into time zones with the `timezonefinder` library.
- The cleaned dataset (`aa_clean_v1.0.parquet`) is a **derived** dataset: the BTS values are unchanged, and we add flags, UTC times, a split label and a capped delay column. The raw BTS files are not redistributed in this repository.

## What we claim and do not claim

- We **claim no ownership** of the source data. Our own work is the code and documentation, released under the MIT licence in `LICENSE`.
- The dataset is provided **for academic use** (a university analytics capstone). It is a simulation on public data and is **not connected to any airline system**.
- **No personal data are included.** The records describe scheduled and flown flights. There is no passenger, crew or customer information. `Tail_Number` is the aircraft registration mark of the plane that flew the flight.
- Airline and airport names, and the flights themselves, belong to their respective owners. Nothing here is endorsed by, or an official statement of, any airline or agency.

## Sources and their licence wording

The wording below is copied from `data/reference/source_log.csv`, where each quote was recorded verbatim from the official page or repository on the access date. Where that column says "not found on page" we found no explicit statement and do not guess one.

### 1. BTS Reporting Carrier On-Time Performance (1987-present), monthly pre-zipped files

- **URL:** https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{YEAR}_{MONTH}.zip
- **What we used:** 20 monthly files, January 2025 to August 2026 (11,730,351 rows, all carriers); only Reporting_Airline = AA rows and 43 columns kept (1,641,071 rows)
- **Access date:** 2026-10-21
- **Licence or terms text, verbatim:** not found on page
- **Licence URL:** (none)
- **Notes:** Source: download_log.csv (logged_at_utc). No licence, terms-of-use, copyright or public-domain statement was found on the pages we could open (the download page, the TranStats home page and the TranStats database-info page). Pages on bts.gov returned 'Access Denied' to automated requests on 2026-10-21 and were not checked. A web search surfaced a data.gov catalogue record for a related BTS dataset that points to https://www.usa.gov/government-works; that record is not the TranStats download and we do not rely on it. The files are published by the US Bureau of Transportation Statistics; we state no licence for them.

### 2. BTS TranStats download page and field definitions

- **URL:** https://www.transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ
- **What we used:** Field names and definitions (also https://transtats.bts.gov/DatabaseInfo.asp?QO_VQ=EFD) and the manual-download route if the pre-zipped URL pattern stopped working (it was not needed)
- **Access date:** 2026-10-21
- **Licence or terms text, verbatim:** not found on page
- **Licence URL:** (none)
- **Notes:** Page text was searched for licence, terms, public domain, copyright, disclaimer and citation wording on 2026-10-21; none found on the download page, the TranStats home page or the database-info page.

### 3. OurAirports airports.csv (open-data downloads)

- **URL:** https://davidmegginson.github.io/ourairports-data/airports.csv
- **What we used:** Latitude and longitude of the 137 airports in the data (136 matched by IATA code) to derive time zones; ISO country code for the domestic-service check in docs/d2
- **Access date:** 2026-10-21
- **Licence or terms text, verbatim:** "This is free and unencumbered software released into the public domain. Anyone is free to copy, modify, publish, use, compile, sell, or distribute this software, either in source code form or as a compiled binary, for any purpose, commercial or non-commercial, and by any means." (first paragraphs of the LICENSE file in the davidmegginson/ourairports-data repository, identified by GitHub as The Unlicense; the file ends "For more information, please refer to <https://unlicense.org>")
- **Licence URL:** https://github.com/davidmegginson/ourairports-data/blob/main/LICENSE
- **Notes:** The repository README says the data is documented at https://ourairports.com/data/, but that page returned HTTP 500 on 2026-10-21, so a statement specific to the data (as opposed to the repository licence file, which speaks of 'software') could not be verified. A search found secondary sources calling OurAirports public domain; not quoted here. OurAirports now lists Palm Beach as DJT, so PBI is not matched by IATA code.

### 4. OpenFlights airports.dat

- **URL:** https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat
- **What we used:** Coordinates of one airport (PBI) that OurAirports does not list under its IATA code; everything else came from OurAirports
- **Access date:** 2026-10-21
- **Licence or terms text, verbatim:** "The OpenFlights Airport, Airline, Plane and Route Databases are made available under the Open Database License. Any rights in individual contents of the database are licensed under the Database Contents License. In short, these mean that you are welcome to use the data as you wish, if and only if you both acknowledge the source and and license any derived works made available to the public with a free license as well." (sic, 'and and'). Same page: "Airport data derived OurAirports and DAFIF, as well as route data from Airline Route Mapper, is in the public domain." (sic)
- **Licence URL:** https://openflights.org/data.php
- **Notes:** Quoted from the 'Licensing and disclaimer' section of the OpenFlights data page. One row of data/reference/airports_tz.csv (PBI, column coord_source = openflights) uses this source. We acknowledge OpenFlights as the source; whether a single coordinate pair triggers the share-alike wording was not assessed (not legal advice).

### 5. timezonefinder library (version 9.1.0) and its timezone boundary data (timezonefinder-data 4.2026.5)

- **URL:** https://github.com/jannikmi/timezonefinder
- **What we used:** Library call timezone_at(lat, lng) to turn airport coordinates into IANA time zone names for 137 airports (data/reference/airports_tz.csv)
- **Access date:** 2026-10-21
- **Licence or terms text, verbatim:** Library (README of the installed package): "`timezonefinder` is licensed under the MIT license." The installed LICENSE file begins "The MIT License (MIT) Copyright (c) 2016 Jannik Michelfeit Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction...". Data (same README): "The data ships in the separate `timezonefinder-data` distribution and is licensed under the ODbL license, following the base dataset from evansiroky/timezone-boundary-builder." The installed data package's DATA_LICENSE file is titled "Open Database License (ODbL) v1.0". Base dataset (README of evansiroky/timezone-boundary-builder): "The outputted data is licensed under the Open Data Commons Open Database License (ODbL)."
- **Licence URL:** https://github.com/jannikmi/timezonefinder/blob/master/LICENSE ; https://github.com/jannikmi/timezonefinder/blob/master/packages/timezonefinder-data/DATA_LICENSE ; https://github.com/evansiroky/timezone-boundary-builder
- **Notes:** Quotes are from the metadata and licence files of the packages installed in .venv and from the GitHub page of timezone-boundary-builder, read on 2026-10-21. The boundary data derives from OpenStreetMap. We use the data only to compute a zone name per airport; our output (airports_tz.csv) is a small derived table. ODbL attribution and share-alike obligations for such derived tables were not assessed (not legal advice); we cite the source.

## What could not be verified

- **BTS:** no licence, terms-of-use or public-domain statement was found on the BTS pages we could open, and bts.gov blocked automated access. We therefore state no licence for the BTS files. Please consult BTS directly before reuse beyond academic purposes.
- **OurAirports:** the data documentation page (ourairports.com/data) was unavailable (HTTP 500) when checked. The licence text we quote is the repository's LICENSE file.
- **OpenFlights and timezonefinder data (ODbL):** we did not assess what the attribution and share-alike terms mean for the small derived table `data/reference/airports_tz.csv`. We acknowledge the sources above.

## Attribution

If you reuse the derived data, please credit: the US Bureau of Transportation Statistics (Reporting Carrier On-Time Performance), OurAirports, OpenFlights (one airport), and the `timezonefinder` / timezone-boundary-builder projects. The file `data/data_manifest.json` records the data version (`data-v1.0`) and checksums.

