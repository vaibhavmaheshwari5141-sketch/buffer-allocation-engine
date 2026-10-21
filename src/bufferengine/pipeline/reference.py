"""Airport reference data: OurAirports coordinates -> IANA time zone via timezonefinder.

OurAirports has no time zone column, so the zone is derived from lat/lon. Airports missing from
OurAirports (by IATA code) fall back to OpenFlights airports.dat coordinates; anything still unmatched
is flagged for manual fix.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from bufferengine.config import reference_dir

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OPENFLIGHTS_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
OPENFLIGHTS_COLS = [
    "id", "name", "city", "country", "iata", "icao", "lat", "lon", "alt_ft", "utc_offset", "dst", "tz_db",
    "type", "source",
]
# Preference when several OurAirports rows share an IATA code.
_TYPE_RANK = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}


def download(url: str, dest: Path, force: bool = False) -> Path:
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def load_ourairports(path: Path | None = None) -> pd.DataFrame:
    path = path or download(OURAIRPORTS_URL, reference_dir() / "ourairports_airports.csv")
    df = pd.read_csv(path, keep_default_na=False, na_values=[""], low_memory=False)
    df = df[df["iata_code"].notna() & (df["iata_code"].str.len() == 3)].copy()
    df["_rank"] = df["type"].map(_TYPE_RANK).fillna(9)
    df = df.sort_values(["iata_code", "_rank"]).drop_duplicates("iata_code")
    return df.rename(
        columns={"iata_code": "iata", "latitude_deg": "lat", "longitude_deg": "lon"}
    )[["iata", "name", "lat", "lon", "iso_country", "iso_region"]]


def load_openflights(path: Path | None = None) -> pd.DataFrame:
    path = path or download(OPENFLIGHTS_URL, reference_dir() / "openflights_airports.dat")
    df = pd.read_csv(path, header=None, names=OPENFLIGHTS_COLS, na_values=["\\N"], keep_default_na=False)
    df = df[df["iata"].notna() & (df["iata"].str.len() == 3)].drop_duplicates("iata")
    return df[["iata", "name", "lat", "lon", "tz_db"]]


def tz_for_coords(lat: pd.Series, lon: pd.Series) -> list[str | None]:
    from timezonefinder import TimezoneFinder

    tf = TimezoneFinder()
    return [
        tf.timezone_at(lng=float(x), lat=float(y)) if pd.notna(y) and pd.notna(x) else None
        for y, x in zip(lat, lon)
    ]


def build_airports_tz(iata_codes: list[str], out_path: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (airports_tz, issues). airports_tz columns: iata, name, lat, lon, timezone, coord_source.

    issues lists codes that needed the OpenFlights fallback or could not be resolved.
    """
    codes = pd.DataFrame({"iata": sorted(set(iata_codes))})
    oa = load_ourairports()
    df = codes.merge(oa, on="iata", how="left")
    df["coord_source"] = df["lat"].notna().map({True: "ourairports", False: None})

    missing = df["lat"].isna()
    if missing.any():
        of = load_openflights().set_index("iata")
        for i in df.index[missing]:
            code = df.at[i, "iata"]
            if code in of.index:
                df.loc[i, ["name", "lat", "lon"]] = of.loc[code, ["name", "lat", "lon"]].values
                df.at[i, "coord_source"] = "openflights"

    df["timezone"] = tz_for_coords(df["lat"], df["lon"])
    issues = df[(df["coord_source"] != "ourairports") | df["timezone"].isna()].assign(
        issue=lambda d: d.apply(
            lambda r: "unresolved: manual fix needed" if pd.isna(r["timezone"]) else "openflights fallback",
            axis=1,
        )
    )[["iata", "name", "coord_source", "timezone", "issue"]]

    out = df[["iata", "name", "lat", "lon", "timezone", "coord_source"]]
    out_path = out_path or reference_dir() / "airports_tz.csv"
    out.to_csv(out_path, index=False)
    return out, issues


def build_from_base(base_path: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build airports_tz.csv for every Origin and Dest in the base table."""
    import duckdb

    from bufferengine.config import base_table_path

    p = (base_path or base_table_path()).as_posix().replace("'", "''")
    codes = duckdb.sql(
        f"SELECT Origin FROM read_parquet('{p}') UNION SELECT Dest FROM read_parquet('{p}')"
    ).fetchall()
    return build_airports_tz([c[0] for c in codes])


if __name__ == "__main__":
    tz, issues = build_from_base()
    print(f"wrote airports_tz.csv: {len(tz)} airports, {tz['timezone'].isna().sum()} without a zone")
    print(issues.to_string(index=False) if len(issues) else "no fallbacks or failures")
