"""Tests for the statewide California (HPMS) adapter — offline, on a small GeoJSON
fixture with the HPMS field names. Needs the geo stack (shapely/pyproj); skips
without it, like the SANDAG fixture tests.

    .venv-forecasting/bin/python -m pytest tests/forecasting/test_hpms_adapter.py -v
"""

import importlib.util
import json

import pytest

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("shapely", "pyproj")),
    reason="geo stack (shapely/pyproj) not installed")

LNG, LAT = -122.0215, 37.5872          # the project intersection
D = 0.003                              # ~1,000 ft of road each way


def _sec(route, coords, aadt, future=None, fsys=3, k=None):
    return {"type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"route_id": route, "routename": None, "f_system": fsys,
                           "county_id": 1, "through_lanes": 4, "aadt": aadt,
                           "future_aadt": future, "future_aadt_vd": "2044-01-01" if future else None,
                           "k_factor": k}}


@pytest.fixture
def hpms_file(tmp_path):
    feats = [
        # N-S road: two sections meeting at the intersection, own forecast (+20% by 2044)
        _sec("ALA_FMT_MAIN ST_P", [[LNG, LAT - D], [LNG, LAT]], 10000, 12000, k=10),
        _sec("ALA_FMT_MAIN ST_P", [[LNG, LAT], [LNG, LAT + D]], 10000, 12000, k=10),
        # E-W road: ONE section passing straight through, no forecast of its own
        _sec("ALA_FMT_ELM AVE_P", [[LNG - D, LAT], [LNG + D, LAT]], 20000),
        # three nearby forecast sections of the same class → borrowed rate +1%/yr
        *[_sec(f"ALA_FMT_X{i}_P", [[LNG + 0.01 * i, LAT + 0.01], [LNG + 0.01 * i + 0.001, LAT + 0.01]],
               5000, 6000) for i in (1, 2, 3)],
    ]
    p = tmp_path / "hpms.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return str(p)


def _layers(path):
    from forecasting.ingest import ingest_networks
    ints = [{"num": 7, "lat": LAT, "lng": LNG}]
    return ints, ingest_networks(ints, [
        {"role": "base", "scenario_year": 2024, "path": path},
        {"role": "horizon", "scenario_year": 2044, "path": path},
    ], region="HPMS_CA")


def test_one_link_per_side_and_stable_ids(hpms_file):
    _, out = _layers(hpms_file)
    ids = {l.link_id for l in out["base"].links}
    assert ids == {"ALA_FMT_MAIN ST_P@7a", "ALA_FMT_MAIN ST_P@7b",
                   "ALA_FMT_ELM AVE_P@7a", "ALA_FMT_ELM AVE_P@7b"}
    assert ids == {l.link_id for l in out["horizon"].links}
    assert all(l.from_node == out["base"].links[0].from_node for l in out["base"].links)


def test_matcher_binds_all_four_legs(hpms_file):
    from forecasting.matcher import match_network_to_legs
    ints, out = _layers(hpms_file)
    legs = match_network_to_legs(ints, out["base"])[0]["legs"]
    assert {d: legs[d]["status"] for d in legs} == {d: "matched" for d in ("NB", "SB", "EB", "WB")}
    assert legs["NB"]["link_id"].startswith("ALA_FMT_MAIN ST_P")
    assert legs["EB"]["link_id"].startswith("ALA_FMT_ELM AVE_P")


def test_growth_own_forecast_and_borrowed(hpms_file):
    _, out = _layers(hpms_file)
    b = {l.link_id: l for l in out["base"].links}
    h = {l.link_id: l for l in out["horizon"].links}
    main, elm = "ALA_FMT_MAIN ST_P@7a", "ALA_FMT_ELM AVE_P@7b"
    assert b[main].daily_total == pytest.approx(10000)
    assert h[main].daily_total == pytest.approx(12000)               # its own forecast
    assert "this road's HPMS forecast" in b[main].source_attributes["growth_source"]
    assert h[elm].daily_total == pytest.approx(20000 * 1.2)          # borrowed 1%/yr × 20 yr
    assert "nearby forecast sections, same road class" in b[elm].source_attributes["growth_source"]
    # K: the section's own 10%, else the 9% default; split evenly by direction
    from forecasting.contract import Period
    assert b[main].flow.ab[Period.AM] == pytest.approx(10000 * 0.10 / 2)
    assert b[elm].flow.ba[Period.PM] == pytest.approx(20000 * 0.09 / 2)


def test_no_road_nearby_fails_loud(hpms_file):
    from forecasting.adapters.hpms import ingest_hpms
    with pytest.raises(ValueError, match="no HPMS road"):
        ingest_hpms(hpms_file, 2024, [{"lat": LAT + 0.05, "lng": LNG + 0.05}])
