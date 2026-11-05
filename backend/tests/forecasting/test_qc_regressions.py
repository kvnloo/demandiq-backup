"""Regressions from the pre-launch forecasting QC (F1–F4 + /forecast API).

Each test is named for the QC item it pins. Run from backend/:
    .venv-forecasting/bin/python -m pytest tests/forecasting/test_qc_regressions.py -q
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forecasting.api import router
from forecasting.pass2 import balance_future, round_forecast
from forecasting.refinement import _nchrp765_pick, _refine_one

app = FastAPI()
app.include_router(router, prefix="/forecast")
client = TestClient(app)

# Balanced in both axes, so identical counts at every intersection conserve links.
TMC = {"NBL": 60, "NBT": 300, "NBR": 40, "SBL": 50, "SBT": 280, "SBR": 35,
       "EBL": 55, "EBT": 480, "EBR": 35, "WBL": 50, "WBT": 450, "WBR": 45}
NUMS = (1, 2, 3)
COORDS = {n: {"lat": 32.75, "lng": -117.10 + 0.005 * n} for n in NUMS}
LINKS = [{"fromNum": n, "toNum": n + 1, "direction": "EB/WB", "mode": "Full"} for n in NUMS[:-1]]


def _no_model(**kw):
    body = {"balanced_tmcs": {"AM": {n: dict(TMC) for n in NUMS}}, "periods": ["AM"],
            "links": [dict(L) for L in LINKS], "coords": COORDS, "count_year": 2025,
            "horizon_year": 2045, "refine_method": "rate", "growth_rate_pct": 1.0,
            "turn_method": "furness"}
    body.update(kw)
    return body


# ── Item 1: SANDAG file picked server-side by model year ─────────────────────
@pytest.fixture
def sandag_dir(tmp_path, monkeypatch):
    import forecasting.ingest as ing
    for y in (2025, 2050):
        (tmp_path / f"hwyLoad_{y}.shp").write_bytes(b"")
    (tmp_path / "hwyLoad.shp").write_bytes(b"")          # undated file is not a model year
    monkeypatch.setattr(ing, "SANDAG_DATA_DIR", tmp_path)
    monkeypatch.setattr("forecasting.api.sandag_years", ing.sandag_years)
    return tmp_path


def test_item1_model_years_endpoint_lists_sandag_files(sandag_dir):
    r = client.get("/forecast/model-years")
    assert r.status_code == 200
    assert r.json()["SANDAG"] == [2025, 2050]


def test_item1_sandag_unknown_year_is_400_listing_years(sandag_dir):
    r = client.post("/forecast/ingest-match", json={
        "region": "SANDAG", "intersections": [{"num": 1, "lat": 32.75, "lng": -117.1}],
        "networks": [{"role": "base", "scenario_year": 2031}]})
    assert r.status_code == 400
    assert "available model years: [2025, 2050]" in r.json()["detail"]
    assert str(sandag_dir) not in r.json()["detail"]          # never leak the server path


def test_item1_sandag_year_resolves_to_dated_file(sandag_dir, monkeypatch):
    import forecasting.ingest as ing
    seen = []
    monkeypatch.setitem(ing.ADAPTERS, "SANDAG", lambda path, year, pts, **kw: seen.append(path))
    ing.ingest_networks([{"lat": 1, "lng": 1}], [{"role": "base", "scenario_year": 2050}])
    assert seen == [sandag_dir / "hwyLoad_2050.shp"]


# ── Item 2: bbox read returns exactly what a full read does ──────────────────
def test_item2_sandag_bbox_read_identical_to_full_read(synthetic_sandag_shp, project_points_near, monkeypatch):
    import geopandas as gpd
    from forecasting.adapters.sandag import ingest_sandag
    real_read, calls = gpd.read_file, []

    def spy(path, **kw):
        calls.append(kw)
        return real_read(path, **kw)

    monkeypatch.setattr(gpd, "read_file", spy)
    fast = ingest_sandag(synthetic_sandag_shp, 2025, project_points_near)
    assert calls and calls[-1].get("bbox"), "SANDAG must read only a bbox around the project"
    monkeypatch.setattr(gpd, "read_file", lambda path, **kw: real_read(path))   # full read
    full = ingest_sandag(synthetic_sandag_shp, 2025, project_points_near)
    assert fast.model_dump() == full.model_dump()


# ── Item 3: Table 4-8 applied once, to the balanced unrounded values ─────────
def test_item3_el_cajon_54th_rounds_once():
    # QC hand-check, El Cajon Blvd & 54th St AM, ratio: the Pass-1 futures.
    p1 = {"NBL": 70.2, "NBT": 275.8, "NBR": 42.0, "SBL": 43.1, "SBT": 299.0, "SBR": 33.6,
          "EBL": 49.2, "EBT": 490.5, "EBR": 44.3, "WBL": 55.6, "WBT": 450.0, "WBR": 35.4}
    res = balance_future({1: p1}, [], coords={1: {"lat": 32.756, "lng": -117.09}})
    # Double rounding gave NBL 80, NBR 50, SBL 50, SBR 40, EBR 50.
    assert res["balanced"][1] == {"NBL": 70, "NBT": 300, "NBR": 40, "SBL": 40, "SBT": 300, "SBR": 30,
                                  "EBL": 50, "EBT": 500, "EBR": 40, "WBL": 60, "WBT": 450, "WBR": 40}
    for m, v in res["balanced"][1].items():
        assert v == round_forecast(res["balanced_unrounded"][1][m])
        assert res["balanced_unrounded"][1][m] == pytest.approx(p1[m], abs=0.5)


def test_item3_uniform_growth_furness_equals_fratar():
    # +1 %/yr × 20 yr = exactly ×1.20 everywhere: both turn methods must agree.
    out = {}
    for tm in ("furness", "fratar"):
        r = client.post("/forecast/run", json=_no_model(turn_method=tm))
        assert r.status_code == 200, r.text
        out[tm] = r.json()["AM"]["pass2"]["balanced"]
    for n in map(str, NUMS):
        assert out["furness"][n] == {m: round_forecast(v * 1.2) for m, v in TMC.items()}
        assert sum(out["furness"][n].values()) == sum(out["fratar"][n].values())
    assert sum(out["furness"]["2"].values()) == 2290      # was 2,310 with double rounding


# ── Item 4: a straight-line decline to zero is an error, never silent zeros ──
def test_item4_linear_rate_decline_to_zero_is_400():
    r = client.post("/forecast/run", json=_no_model(growth_rate_pct=-5))
    assert r.status_code == 400
    assert "-5.0%/yr for 20 years removes all traffic" in r.json()["detail"]


def test_item4_moderate_decline_kept_and_flagged():
    r = client.post("/forecast/run", json=_no_model(growth_rate_pct=-2))
    assert r.status_code == 200
    am = r.json()["AM"]
    assert am["pass2"]["balanced_unrounded"]["1"]["EBT"] == pytest.approx(480 * 0.6, rel=0.01)
    codes = {f["code"] for f in am["refinement"][0]["approaches"]["EB"]["inbound"]["flags"]}
    assert "REFINE-DECLINE" in codes


def test_item4_model_extrapolated_below_zero_is_flagged_in_plain_words():
    # Model 1000 (2025) → 400 (2050), run to 2075: horizon model volume −200.
    out = _refine_one(400, 1000, 400, peak_hour_factor=None, method="ratio",
                      growth_factor_max=4.0, years=(2025, 2050, 2025, 2075))
    msg = [f["message"] for f in out["flags"] if f["code"] == "REFINE-EXTRAPOLATED-BELOW-ZERO"]
    assert msg and "2075" in msg[0] and "unreliable" in msg[0]


# ── Item 5: the extrapolation flag names the model's last year ───────────────
def test_item5_extrapolation_flag_plain_words():
    from tests.forecasting.test_api import _run_payload
    body = _run_payload()
    body["layers"]["horizon"]["scenario_year"] = 2050
    body.update(count_year=2025, horizon_year=2075)
    r = client.post("/forecast/run", json=body)
    assert r.status_code == 200, r.text
    flags = r.json()["AM"]["refinement"][0]["approaches"]["NB"]["flags"]
    assert any("Model only goes to 2050; 2075 is extrapolated" in f["message"] for f in flags)


# ── Item 9: bad input is a 400 in plain words, never a 500 ───────────────────
@pytest.mark.parametrize("tm", ["fratar", "fuzzy"])
def test_item9_trip_table_all_zero_counts_is_400(tm):
    zero = {n: {m: 0 for m in TMC} for n in NUMS}
    r = client.post("/forecast/run", json=_no_model(turn_method=tm, balanced_tmcs={"AM": zero}))
    assert r.status_code == 400
    assert "no counted volumes" in r.json()["detail"]


def test_item9_link_to_unknown_intersection_is_400():
    r = client.post("/forecast/run", json=_no_model(
        links=[{"fromNum": 3, "toNum": 9, "direction": "EB/WB"}]))
    assert r.status_code == 400
    assert "intersection 9" in r.json()["detail"]


@pytest.mark.parametrize("field,value", [
    ("manual_targets", [1]), ("manual_targets", {"a": 1}),
    ("manual_targets", {"1": {"NB": {"inbound": -5, "outbound": 10}}}),
    ("coords", [1]), ("coords", {"a": {}}),
    ("balanced_tmcs", {"AM": {"a": {}}}),
    ("balanced_tmcs", {"AM": {"1": {"EBT": -100}}}),
    ("links", [1]),
])
def test_item9_bad_optional_fields_are_400(field, value):
    for tm in ("furness", "fuzzy"):
        r = client.post("/forecast/run", json=_no_model(turn_method=tm, **{field: value}))
        assert r.status_code == 400, (field, value, tm, r.status_code)


def test_item9_ingest_match_intersection_without_num_is_400():
    r = client.post("/forecast/ingest-match", json={
        "intersections": [{"lat": 32.75, "lng": -117.1}],
        "networks": [{"role": "base", "scenario_year": 2025}]})
    assert r.status_code == 400
    assert "num" in r.json()["detail"]


def test_item9_trend_points_after_count_year_is_400():
    pts = [{"year": y, "volume": 1000 + 10 * i} for i, y in enumerate((2020, 2030, 2040))]
    r = client.post("/forecast/run", json=_no_model(refine_method="trend", trend_points=pts))
    assert r.status_code == 400
    assert "2025 or earlier" in r.json()["detail"]


def test_item9_text_growth_rate_plain_message():
    r = client.post("/forecast/run", json=_no_model(growth_rate_pct="abc"))
    assert r.status_code == 400
    assert "could not convert" not in r.json()["detail"]
    assert "% per year" in r.json()["detail"]


# ── Item 12: NCHRP 765 Table 6-2 col. 12, as printed (≤ / ≥ at the limits) ────
def test_item12_nchrp765_boundaries_match_printed_table():
    K = 1.0
    # MR > 1 (model grows): RATIO ≥ 2.0 → DIFF.  count 200, base 100 → R = 2.0
    ratio, diff = 200 * 1.5, 200 + 50
    assert _nchrp765_pick(200, 100, 150, K, ratio, diff)[1] == "difference"
    # MR > 1: RATIO ≤ 0.5 → MRATIO.  R = 0.5
    assert _nchrp765_pick(50, 100, 150, K, 75, 100)[1] == "ratio (NCHRP 765, weighted)"
    # MR < 1: RATIO ≤ 1.0 → RATIO.  R = 1.0
    assert _nchrp765_pick(100, 100, 80, K, 80, 80)[1] == "ratio"
    # between the limits → RAf = average(MRATIO, DIFF)
    assert _nchrp765_pick(150, 100, 150, K, 225, 200)[1] == "average of ratio and difference"


# ── Item 8: forecast workbook — numbers equal what F4 shows ──────────────────
def test_item8_forecast_export_matches_run():
    import io
    from openpyxl import load_workbook
    run = client.post("/forecast/run", json=_no_model(growth_rate_pct=-1)).json()
    snap = {"project": {"name": "QC corridor"},
            "intersections": [{"num": n, "name": f"Int {n} St", "balanced": {"AM": dict(TMC)}} for n in NUMS],
            "forecasting": {"results": run, "run": {
                "baseYear": 2025, "horizonYear": 2045, "region": "SANDAG",
                "settings": {"refineMethod": "rate", "turnMethod": "furness", "growthRate": -1}}}}
    r = client.post("/forecast/export-excel", json=snap)
    assert r.status_code == 200
    assert "QC_corridor_forecast.xlsx" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[:2] == ["Forecast Summary", "Forecast TMCs"]
    summary = {row[0]: row[1] for row in wb["Forecast Summary"].iter_rows(values_only=True) if row[0]}
    assert summary["Forecast (horizon) year"] == 2045
    assert summary["How each road grows"] == "Growth rate entered (no model)"
    assert any("REFINE-DECLINE" == row[2] for row in wb["Forecast Summary"].iter_rows(values_only=True))
    rows = list(wb["Forecast TMCs"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == 3 * 12
    fut = run["AM"]["pass2"]["balanced"]
    for n, _, p, mv, ex, fu, _, d, _, note in rows:
        assert p == "AM" and ex == TMC[mv] and fu == fut[str(n)][mv]
        assert d < 0 and note == "decline"           # −1 %/yr × 20: every movement declines


def test_item8_forecast_export_without_results_is_400():
    assert client.post("/forecast/export-excel", json={"project": {"name": "x"}}).status_code == 400
