"""Regression tests for the bugs the independent math checkers found (Nov 2026).
Each test failed before its fix.

    .venv-forecasting/bin/python -m pytest tests/forecasting/test_checker_regressions.py -v
"""

import importlib.util
import json
import math

import pytest

from forecasting.contract import Period
from forecasting.od import corridor_groups, forecast_trips
from forecasting.refinement import refine_corridor
from forecasting.trend import uniform_growth
from forecasting.benchmark import build_world, benchmark


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from forecasting.api import router
    app = FastAPI(); app.include_router(router, prefix="/forecast")
    return TestClient(app)


@pytest.fixture(scope="module")
def world():
    return build_world(7, "steady")


def _body(world, **kw):
    b = {"layers": {"base": world["base_layer"].model_dump(mode="json"),
                    "horizon": world["horizon_layer"].model_dump(mode="json")},
         "matches": world["matches"], "balanced_tmcs": {"AM": {str(n): v for n, v in world["counts"].items()}},
         "periods": ["AM"], "links": world["links"],
         "coords": {str(n): c for n, c in world["coords"].items()},
         "count_year": 2025, "horizon_year": 2045}
    b.update(kw)
    return b


# B1 — equal model years: interpolation is undefined (NCHRP 765 §9.2 divides by YH − YB)
def test_equal_model_years_with_analysis_years_raise(world):
    hz = world["base_layer"].model_copy(deep=True)
    with pytest.raises(ValueError, match="same model year"):
        refine_corridor(world["matches"], world["base_layer"], hz, world["counts"], Period.AM,
                        count_year=2025, horizon_year=2045)


# B2 — K must be a number in (0, 1]
@pytest.mark.parametrize("k", [0, -0.2, 1.5, "abc", float("nan")])
def test_bad_peak_hour_factor_is_400(world, k):
    r = _client().post("/forecast/run", json=_body(world, refine_method="difference", peak_hour_factor=k)
                       if k == k else {**_body(world, refine_method="difference"), "peak_hour_factor": "nan"})
    assert r.status_code == 400, r.text


def test_numeric_string_k_is_accepted(world):
    assert _client().post("/forecast/run", json=_body(world, refine_method="difference",
                                                       peak_hour_factor="0.3")).status_code == 200


# B3 — unknown method names are rejected, not silently run as ratio
def test_unknown_refine_method_is_400(world):
    r = _client().post("/forecast/run", json=_body(world, refine_method="ratoi"))
    assert r.status_code == 400 and "ratio" in r.text


# B4 — one analysis year without the other
def test_only_one_year_is_400(world):
    b = _body(world); del b["horizon_year"]
    assert _client().post("/forecast/run", json=b).status_code == 400


# B5 — bad trend / rate inputs are clear 400s
@pytest.mark.parametrize("extra", [
    {"refine_method": "trend", "trend_points": [{"year": 2015}, {"year": 2016, "volume": 5}, {"year": 2017, "volume": 6}]},
    {"refine_method": "rate", "growth_rate_pct": "nan"},
    {"refine_method": "rate", "growth_rate_pct": "inf"},
])
def test_bad_no_model_inputs_are_400(world, extra):
    r = _client().post("/forecast/run", json=_body(world, layers=None, **extra))
    assert r.status_code == 400, r.text


# OD-1 — no coordinates: orient by intersection number (as the balancer does)
def test_no_coords_chain_runs_low_to_high(world):
    order = [3, 2, 1]
    assert corridor_groups(order, world["links"][:2], {})[0][0] == [1, 2, 3]
    counts = {n: world["counts"][n] for n in (1, 2, 3, 4)}
    rr = uniform_growth({n: counts[n] for n in (4, 3, 2, 1)}, Period.AM, 1.0, label="t", annual_rate=0)
    fut, _, summ = forecast_trips(rr, counts, world["links"], {}, method="fratar")
    assert summ[0]["base_fit_worst_pct"] < 1
    assert max(abs(fut[n][m] - counts[n][m]) for n in counts for m in counts[n]) < 5


# OD-4 — order comes from walking the links; coordinates only choose the direction
def test_chain_order_follows_links_not_coordinates():
    links = [{"fromNum": 1, "toNum": 3, "direction": "EB/WB"}, {"fromNum": 3, "toNum": 2, "direction": "EB/WB"}]
    co = {k: {"lat": 0, "lng": k} for k in (1, 2, 3)}
    assert corridor_groups([1, 2, 3], links, co)[0][0] == [1, 3, 2]
    co_rev = {k: {"lat": 0, "lng": -k} for k in (1, 2, 3)}
    assert corridor_groups([1, 2, 3], links, co_rev)[0][0] == [2, 3, 1]


# OD-2 — manual targets on pending legs are used by the trip methods too
def test_trip_methods_use_manual_targets(world):
    matches = json.loads(json.dumps(world["matches"]))
    matches[0]["legs"]["NB"] = {"status": "unmatched", "link_id": None}
    manual = {"1": {"NB": {"inbound": 999, "outbound": 777}}}
    r = _client().post("/forecast/run", json=_body(world, matches=matches, turn_method="fratar",
                                                     manual_targets=manual))
    assert r.status_code == 200, r.text
    am = r.json()["AM"]
    nb_in = sum(am["pass2"]["balanced_unrounded"]["1"][m] for m in ("NBL", "NBT", "NBR"))
    assert nb_in == pytest.approx(999, rel=0.12)          # Fratar evens ends, so near, not exact
    flags = [f["code"] for f in am["pass1"]["meta"]["1"]["flags"]]
    assert "OD-PENDING-MANUAL" in flags and "OD-PENDING-HELD-FLAT" not in flags


# BENCH-1 — the shared main-street error also reaches the corridor end links
def test_shared_error_scenario_touches_end_links():
    a, b = build_world(1000, "steady"), build_world(1000, "shared_error")
    fa = {l.link_id: l.flow.ab[Period.AM] for l in a["horizon_layer"].links}
    fb = {l.link_id: l.flow.ab[Period.AM] for l in b["horizon_layer"].links}
    assert fa["W"] != fb["W"] and fa["E"] != fb["E"]


# BENCH-2 — every method is scored at the same (delivered) precision
def test_benchmark_scores_delivered_values(world):
    from forecasting.benchmark import run_method
    from forecasting.pass2 import round_forecast
    for tm in ("furness", "fratar"):
        fut = run_method(world, "ratio", tm)
        assert all(v == round_forecast(v) for mv in fut.values() for v in mv.values())


# VMT — daily from a PERIOD model needs the peak-hour factor (daily ÷ period ÷ K)
def test_vmt_daily_uses_peak_hour_factor():
    from tests.forecasting.test_vmt import _run
    c = _run(peak_hour_factor=0.25)["corridor"]
    assert c["daily_existing_vmt"] == pytest.approx(1800 * 5 / 0.25)
    c0 = _run()["corridor"]
    assert c0["daily_existing_vmt"] is None and c0["daily_needs_k"] is True


# HPMS — the borrowed-growth search reaches 2 miles east–west too
@pytest.mark.skipif(not all(importlib.util.find_spec(m) for m in ("shapely", "pyproj")), reason="geo stack")
def test_hpms_growth_search_radius_in_longitude(tmp_path):
    from forecasting.adapters.hpms import ingest_hpms
    LNG, LAT = -122.0215, 37.5872
    dlng = 1.8 / (69.172 * math.cos(math.radians(LAT)))

    def sec(r, c, a, f=None):
        return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": c},
                "properties": {"route_id": r, "routename": None, "f_system": 3, "county_id": 1,
                               "through_lanes": 2, "aadt": a, "future_aadt": f,
                               "future_aadt_vd": "2044-01-01" if f else None, "k_factor": None}}
    feats = [sec("EW", [[LNG - .003, LAT], [LNG + .003, LAT]], 20000)] + [
        sec(f"X{i}", [[LNG + dlng, LAT + i * 1e-4], [LNG + dlng + 1e-5, LAT + i * 1e-4]], 5000, 6000)
        for i in range(3)]
    p = tmp_path / "h.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    lk = ingest_hpms(str(p), 2044, [{"num": 1, "lat": LAT, "lng": LNG}]).links[0]
    assert lk.daily_total == pytest.approx(24000)


# EXAM-1 (Examiner forecast track, seeds 600031/600102) — a fuzzy fit has many
# answers; the one reached must not depend on whether the corridor runs E–W or N–S
def test_fuzzy_same_answer_when_corridor_is_rotated(world):
    rot = {"EB": "NB", "NB": "WB", "WB": "SB", "SB": "EB"}
    inv = {v: k for k, v in rot.items()}
    b = _body(world, turn_method="fuzzy", peak_hour_factor=0.3, refine_method="difference")
    # pull the legs apart so the bands bind and the order matters
    for l in b["layers"]["horizon"]["links"]:
        if l["link_id"].startswith("C"):
            l["flow"]["ab"]["AM"] *= 1.25
    r = json.loads(json.dumps(b))
    r["balanced_tmcs"] = {"AM": {i: {rot[m[:2]] + m[2:]: v for m, v in mv.items()}
                                 for i, mv in b["balanced_tmcs"]["AM"].items()}}
    r["links"] = [{**l, "direction": "NB/SB"} for l in b["links"]]
    r["coords"] = {i: {"lat": 37.58 + (c["lng"] + 122.03), "lng": -122.03} for i, c in b["coords"].items()}
    r["matches"] = [{**m, "legs": {rot[d]: v for d, v in m["legs"].items()}} for m in b["matches"]]
    c = _client()
    ew = c.post("/forecast/run", json=b).json()["AM"]["pass2"]["balanced_unrounded"]
    ns = c.post("/forecast/run", json=r).json()["AM"]["pass2"]["balanced_unrounded"]
    for i, mv in ew.items():
        back = {inv[m[:2]] + m[2:]: v for m, v in ns[i].items()}
        for m, v in mv.items():
            assert back[m] == pytest.approx(v, abs=0.01), (i, m)
