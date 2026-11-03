"""Trip-table (Fratar / fuzzy), no-model growth (trend / rate) and the benchmark.

    .venv-forecasting/bin/python -m pytest tests/forecasting/test_od_trend.py -v
"""

import pytest

from forecasting.benchmark import build_world, benchmark
from forecasting.contract import Period
from forecasting.od import corridor_groups, forecast_trips
from forecasting.trend import fit_trend, uniform_growth


@pytest.fixture(scope="module")
def world():
    return build_world(7, "steady")


@pytest.mark.parametrize("method", ["fratar", "fuzzy"])
@pytest.mark.parametrize("factor", [1.0, 1.2, 0.9])
def test_trip_methods_reproduce_uniform_growth(world, method, factor):
    rr = uniform_growth(world["counts"], Period.AM, factor, label="t", annual_rate=0.0)
    fut, meta, summary = forecast_trips(rr, world["counts"], world["links"], world["coords"], method=method)
    for n, mv in world["counts"].items():
        for m, v in mv.items():
            if v > 50:
                assert fut[n][m] == pytest.approx(v * factor, rel=0.06 if method == "fuzzy" else 0.01)
    assert summary[0]["trips"] > 0 and summary[0]["intersections"] == [1, 2, 3, 4]


def test_corridor_must_be_a_chain():
    links = [{"fromNum": 1, "toNum": 2, "direction": "EB/WB"}, {"fromNum": 1, "toNum": 3, "direction": "EB/WB"},
             {"fromNum": 1, "toNum": 4, "direction": "EB/WB"}]
    with pytest.raises(ValueError, match="straight chain"):
        corridor_groups([1, 2, 3, 4], links, {})
    assert corridor_groups([1, 2], [], {}) == [([1], None), ([2], None)]


def test_trend_picks_better_fit():
    lin = fit_trend([{"year": y, "volume": 1000 + 20 * (y - 2015)} for y in range(2015, 2026)], 2025)
    assert lin["kind"] == "linear" and lin["annual_rate"] == pytest.approx(20 / 1200)
    exp = fit_trend([{"year": y, "volume": 1000 * 1.05 ** (y - 2015)} for y in range(2015, 2026)], 2025)
    assert exp["kind"] == "exponential" and exp["annual_rate"] == pytest.approx(0.05)
    with pytest.raises(ValueError, match="3 different years"):
        fit_trend([{"year": 2020, "volume": 1}, {"year": 2021, "volume": 2}], 2025)


def test_api_runs_without_a_model(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from forecasting.api import router
    app = FastAPI(); app.include_router(router, prefix="/forecast")
    tm = {str(n): v for n, v in world["counts"].items()}
    body = {"balanced_tmcs": {"AM": tm}, "periods": ["AM"], "links": world["links"],
            "coords": {str(n): c for n, c in world["coords"].items()}, "count_year": 2025, "horizon_year": 2045,
            "refine_method": "rate", "growth_rate_pct": -0.5, "turn_method": "fratar"}
    r = TestClient(app).post("/forecast/run", json=body)
    assert r.status_code == 200, r.text
    am = r.json()["AM"]
    v = world["counts"][1]["EBT"]
    assert am["pass2"]["balanced_unrounded"]["1"]["EBT"] == pytest.approx(v * 0.9, rel=0.01)   # −0.5 %/yr × 20
    assert am["trips"][0]["method"] == "fratar" and am["vmt"] is None
    body["refine_method"] = "trend"; body["trend_points"] = [{"year": 2020, "volume": 9}]
    assert TestClient(app).post("/forecast/run", json=body).status_code == 400


def test_benchmark_smoke():
    res = benchmark(seeds=1, scenarios=["develop"], link_methods=["ratio", "trend"])
    assert len(res["summary"]) == 6
    assert all(0 <= r["geh5"] <= 1 and r["ape"] >= 0 for r in res["summary"])
