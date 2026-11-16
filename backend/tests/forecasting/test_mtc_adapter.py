"""MTC (Bay Area) adapter on a tiny synthetic TM1 shapefile — offline.

    .venv-forecasting/bin/python -m pytest tests/forecasting/test_mtc_adapter.py -v
"""
import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("geopandas", "pyproj")),
    reason="geo stack not installed")

LAT, LNG = 37.5872, -122.0215


@pytest.fixture
def mtc_dir(tmp_path, monkeypatch):
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString
    t = Transformer.from_crs(4326, 26910, always_xy=True)
    x, y = t.transform(LNG, LAT)
    ends = {11: (x, y + 150), 12: (x, y - 150), 13: (x + 150, y), 14: (x - 150, y), 99: (x + 40, y + 40)}
    rows = []
    def add(a, b, ft, am, lanes=2):
        pa, pb = ((x, y) if a == 10 else ends[a]), ((x, y) if b == 10 else ends[b])
        rows.append(dict(A=a, B=b, FT=ft, LANES=lanes, DISTANCE=0.03, CITYNAME="Fremont",
                         VOLEA_TOT=10.0, VOLAM_TOT=am, VOLMD_TOT=100.0, VOLPM_TOT=am, VOLEV_TOT=20.0,
                         geometry=LineString([pa, pb])))
    for n in (11, 12, 13, 14):
        add(10, n, 7, 1000.0)          # leaving the intersection
        add(n, 10, 7, 800.0)           # entering it
    add(99, 10, 6, 5000.0)             # centroid connector — must be dropped
    for year in (2015, 2050):
        d = tmp_path / str(year); d.mkdir()
        gpd.GeoDataFrame(rows, crs=26910).to_file(d / "network_links.shp")
    import forecasting.adapters.mtc as mtc
    monkeypatch.setattr(mtc, "MTC_DATA_DIR", tmp_path)
    return tmp_path


def test_pairs_merged_dummies_dropped_and_matched(mtc_dir):
    from forecasting.ingest import ingest_networks
    from forecasting.matcher import match_network_to_legs
    from forecasting.contract import Period
    ints = [{"num": 1, "lat": LAT, "lng": LNG}]
    out = ingest_networks(ints, [{"role": "base", "scenario_year": 2015},
                                 {"role": "horizon", "scenario_year": 2050}], region="MTC")
    b = out["base"]
    assert sorted(l.link_id for l in b.links) == ["10-11", "10-12", "10-13", "10-14"]
    lk = b.links[0]
    assert lk.from_node == 10 and lk.outbound_from(10)[Period.AM] == 1000.0
    assert lk.inbound_to(10)[Period.AM] == 800.0
    assert lk.daily_total == pytest.approx((10 + 1000 + 100 + 1000 + 20) + (10 + 800 + 100 + 800 + 20))
    legs = match_network_to_legs(ints, b)[0]["legs"]
    assert all(v["status"] == "matched" for v in legs.values())


def test_missing_year_lists_available(mtc_dir):
    from forecasting.adapters.mtc import ingest_mtc
    with pytest.raises(ValueError, match=r"available model years: \[2015, 2050\]"):
        ingest_mtc(None, 2035, [{"lat": LAT, "lng": LNG}])


@pytest.fixture
def mtc_long_link(tmp_path, monkeypatch):
    """One two-way arterial link 1,200 m long; the intersection sits 10 m (33 ft) off its
    middle, ~1,970 ft from either end -- the Union City / Oakwood case (sweep item 5)."""
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString
    x, y = Transformer.from_crs(4326, 26910, always_xy=True).transform(LNG, LAT)
    north, south = (x + 10, y + 600), (x + 10, y - 600)
    def row(a, b, pa, pb, am):
        return dict(A=a, B=b, FT=7, LANES=2, DISTANCE=0.75, CITYNAME="Union City",
                    VOLEA_TOT=10.0, VOLAM_TOT=am, VOLMD_TOT=100.0, VOLPM_TOT=am, VOLEV_TOT=20.0,
                    geometry=LineString([pa, pb]))
    rows = [row(21, 22, north, south, 700.0),   # southbound
            row(22, 21, south, north, 500.0)]   # northbound
    for year in (2015, 2050):
        d = tmp_path / str(year); d.mkdir()
        gpd.GeoDataFrame(rows, crs=26910).to_file(d / "network_links.shp")
    import forecasting.adapters.mtc as mtc
    monkeypatch.setattr(mtc, "MTC_DATA_DIR", tmp_path)
    return tmp_path


def test_intersection_mid_link_snaps_onto_the_link(mtc_long_link):
    from forecasting.ingest import ingest_networks
    from forecasting.matcher import match_network_to_legs
    approaches = {"NB": {}, "SB": {}, "EB": {"exists": False}, "WB": {"exists": False}}
    ints = [{"num": 1, "lat": LAT, "lng": LNG, "approaches": approaches}]
    out = ingest_networks(ints, [{"role": "base", "scenario_year": 2015},
                                 {"role": "horizon", "scenario_year": 2050}], region="MTC")
    m = match_network_to_legs(ints, out["base"])[0]
    assert m["snap_distance_ft"] < 40, m["flags"]
    legs = m["legs"]
    assert legs["NB"]["status"] == "matched" and legs["NB"]["inbound"]["AM"] == 500.0
    assert legs["SB"]["status"] == "matched" and legs["SB"]["inbound"]["AM"] == 700.0
    # the two halves add up to the whole link
    assert sum(l.length_mi for l in out["base"].links) == pytest.approx(0.75)
    assert {l.link_id for l in out["base"].links} == {l.link_id for l in out["horizon"].links}
