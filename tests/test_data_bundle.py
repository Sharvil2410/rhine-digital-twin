"""Smoke tests for the static data bundle the dashboard client loads.

They guard the contract between the Python build scripts and index.html:
the JS data file must parse as JSON after its assignment prefix, every
station the page expects must be present with the fields the panels read,
the borders layer must be valid line GeoJSON, and every local file the page
references must exist in the repository.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIONS_JS = ROOT / "data" / "stations_data.js"
BORDERS = ROOT / "data" / "borders.geojson"
INDEX = ROOT / "index.html"

EXPECTED_STATIONS = {"kaub", "bonn", "duisburg_ruhrort", "wesel"}
REQUIRED_STATION_FIELDS = {
    "key", "name", "pegelonline_uuid", "lat", "lon", "rhine_km", "insitu_wl_daily",
}


@pytest.fixture(scope="module")
def bundle():
    text = STATIONS_JS.read_text(encoding="utf-8")
    match = re.match(r"\s*window\.DASHBOARD_DATA\s*=\s*", text)
    assert match, "stations_data.js must start with 'window.DASHBOARD_DATA ='"
    body = text[match.end():].rstrip().rstrip(";")
    return json.loads(body)


def test_bundle_has_generation_stamp(bundle):
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", bundle["generated_utc"])


def test_all_four_stations_present(bundle):
    keys = {s["key"] for s in bundle["stations"]}
    assert keys == EXPECTED_STATIONS


@pytest.mark.parametrize("field", sorted(REQUIRED_STATION_FIELDS))
def test_station_fields(bundle, field):
    for station in bundle["stations"]:
        assert field in station, f"{station.get('key')} lacks {field}"


def test_station_coordinates_are_on_the_lower_rhine(bundle):
    for s in bundle["stations"]:
        assert 49.5 < s["lat"] < 52.0 and 6.0 < s["lon"] < 8.5, s["key"]
        assert 500 < s["rhine_km"] < 900, s["key"]


def test_daily_series_are_sorted_date_value_pairs(bundle):
    for s in bundle["stations"]:
        series = s["insitu_wl_daily"]
        assert len(series) > 100, s["key"]
        dates = [d for d, _ in series]
        assert dates == sorted(dates), f"{s['key']} series not chronological"
        assert all(isinstance(v, (int, float)) for _, v in series), s["key"]


def test_borders_geojson_is_line_geometry():
    gj = json.loads(BORDERS.read_text(encoding="utf-8"))
    assert gj["type"] == "FeatureCollection"
    assert gj["features"], "borders.geojson has no features"
    for f in gj["features"]:
        assert f["geometry"]["type"] in {"LineString", "MultiLineString"}


def test_local_files_referenced_by_index_exist():
    html = INDEX.read_text(encoding="utf-8")
    refs = set(re.findall(r'(?:src|href)="((?:data|docs)/[^"#?]+)"', html))
    assert refs, "index.html references no local data files, check the regex"
    missing = [r for r in refs if not (ROOT / r).exists()]
    assert not missing, f"index.html references missing files: {missing}"
