from pathlib import Path

import pandas as pd
import pytest

from bufferengine.config import reference_dir
from bufferengine.pipeline.reference import tz_for_coords

KNOWN = {
    # iata: (lat, lon, expected IANA zone)
    "PHX": (33.4353, -112.0059, "America/Phoenix"),
    "HNL": (21.3184, -157.9257, "Pacific/Honolulu"),
    "DFW": (32.8968, -97.0380, "America/Chicago"),
    "JFK": (40.6394, -73.7793, "America/New_York"),
    "LAX": (33.9425, -118.4080, "America/Los_Angeles"),
    "ANC": (61.1790, -149.9926, "America/Anchorage"),
    "SJU": (18.4394, -66.0018, "America/Puerto_Rico"),
}


def test_tz_for_known_coordinates():
    lat = pd.Series([v[0] for v in KNOWN.values()])
    lon = pd.Series([v[1] for v in KNOWN.values()])
    assert tz_for_coords(lat, lon) == [v[2] for v in KNOWN.values()]


AIRPORTS_TZ: Path = reference_dir() / "airports_tz.csv"


@pytest.mark.skipif(not AIRPORTS_TZ.exists(), reason="airports_tz.csv not built")
def test_airports_tz_file_complete_and_correct():
    df = pd.read_csv(AIRPORTS_TZ).set_index("iata")
    assert df["timezone"].notna().all()
    assert df.index.is_unique
    for code, (_, _, zone) in KNOWN.items():
        if code in df.index:
            assert df.at[code, "timezone"] == zone
