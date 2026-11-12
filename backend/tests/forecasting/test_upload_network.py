"""The engineer's own model file: upload → check columns → ingest → run.

The equivalence tests push a synthetic network in the SANDAG hwyLoad schema
(tests/fixtures/samples/network, built by make_samples.py) through the upload
path and require the same matches and the same /forecast/run numbers as the
built-in SANDAG adapter reading the same files.
"""

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("pyogrio")
pytest.importorskip("geopandas")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from forecasting import upload  # noqa: E402
from forecasting.api import router  # noqa: E402
from forecasting.ingest import ingest_networks  # noqa: E402
from forecasting.matcher import match_network_to_legs  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "samples"
FIX = SAMPLES / "network"
NET = json.loads((SAMPLES / "expected.json").read_text())["network"]
app = FastAPI()
app.include_router(router, prefix="/forecast")
client = TestClient(app)

# The two SAMPLE BLVD intersections — node coordinates of the synthetic network.
INTX = NET["intersections"]
COUNTS = {"NBL": 60, "NBT": 300, "NBR": 40, "SBL": 50, "SBT": 280, "SBR": 35,
          "EBL": 55, "EBT": 480, "EBR": 35, "WBL": 50, "WBT": 450, "WBR": 45}
SHP_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


@pytest.fixture(autouse=True)
def _private_upload_root(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "UPLOAD_ROOT", tmp_path / "uploads")


def _sandag_parts(year, skip=()):
    stem = FIX / f"sandag_{year}" / f"hwyLoad_{year}"
    return [("files", (stem.name + e, open(f"{stem}{e}", "rb").read()))
            for e in SHP_PARTS if e not in skip]


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def _post(files, intersections=INTX):
    import json
    return client.post("/forecast/upload-network", files=files,
                       data={"intersections": json.dumps(intersections)})


# ── Validation ────────────────────────────────────────────────────────────────
def test_zip_upload_ok_and_reports_columns():
    r = _post([("files", ("network_2025.zip", (FIX / "renamed_2025.zip").read_bytes()))])
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["format"] == "Shapefile" and b["feature_count"] == NET["feature_count"]
    assert b["crs"].endswith("2230") or "2230" in b["crs"]
    assert b["intersections_outside"] == []
    lng0, lat0, lng1, lat1 = b["bounds_4326"]
    assert lng0 < INTX[0]["lng"] < lng1 and lat0 < INTX[0]["lat"] < lat1
    f = {x["name"]: x for x in b["fields"]}
    assert f["AM_AB_VOL"]["type"] == "number" and f["STREET"]["type"] == "text"
    assert f["STREET"]["sample"]
    assert (upload.UPLOAD_ROOT / b["upload_id"] / "layer.shp").exists()


def test_model_drop_zip_is_read_as_sandag():
    """model-drop/<year>.zip layout: <year>/hwyLoad.* inside one zip."""
    r = _post([("files", ("2035.zip", (FIX / "model_drop_2035.zip").read_bytes()))])
    assert r.status_code == 200, r.text
    assert r.json()["schema"] == "SANDAG" and r.json()["feature_count"] == NET["feature_count"]


def test_parts_upload_ok_and_flags_outside_intersections():
    far = INTX + [{"num": 9, "lat": 37.8, "lng": -122.27}]   # Oakland
    r = _post(_sandag_parts(2025), far)
    assert r.status_code == 200, r.text
    assert r.json()["intersections_outside"] == [9]
    assert r.json()["schema"] == "SANDAG"


def test_missing_prj_is_400():
    r = _post(_sandag_parts(2025, skip=(".prj",)))
    assert r.status_code == 400 and ".prj" in r.json()["detail"]
    assert not any(upload.UPLOAD_ROOT.iterdir())       # nothing left behind


def test_not_a_line_layer_is_400(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point
    p = tmp_path / "nodes.geojson"
    gpd.GeoDataFrame({"id": [1]}, geometry=[Point(-117.08, 32.76)], crs=4326).to_file(p, driver="GeoJSON")
    r = _post([("files", ("nodes.geojson", p.read_bytes()))])
    assert r.status_code == 400 and "road lines" in r.json()["detail"]


def test_geojson_and_gpkg_ok(tmp_path):
    import geopandas as gpd
    g = gpd.read_file(FIX / "sandag_2025" / "hwyLoad_2025.shp")
    g.to_crs(4326).to_file(tmp_path / "n.geojson", driver="GeoJSON")
    g.to_file(tmp_path / "n.gpkg", driver="GPKG")
    for name in ("n.geojson", "n.gpkg"):
        r = _post([("files", (name, (tmp_path / name).read_bytes()))])
        assert r.status_code == 200, r.text
        assert r.json()["schema"] == "SANDAG"


def test_garbage_file_is_400():
    r = _post([("files", ("network.shp", b"not a shapefile at all")),
               ("files", ("network.shx", b"x")), ("files", ("network.dbf", b"x")),
               ("files", ("network.prj", b"x"))])
    assert r.status_code == 400
    r = _post([("files", ("network.zip", b"PK\x03\x04 truncated"))])
    assert r.status_code == 400 and "zip" in r.json()["detail"]
    r = _post([("files", ("avgload5period.net", b"\x00\x01cube"))])
    assert r.status_code == 400 and "Cube" in r.json()["detail"]


def test_zip_member_paths_are_ignored(tmp_path):
    parts = [(f"../../../../evil{e}" if e == ".shp" else f"/abs/x/hwyLoad_2025{e}",
              (FIX / "sandag_2025" / f"hwyLoad_2025{e}").read_bytes()) for e in SHP_PARTS]
    parts.append(("../../escape.txt", b"nope"))
    r = _post([("files", ("n.zip", _zip(parts)))])
    assert r.status_code == 200, r.text
    d = upload.UPLOAD_ROOT / r.json()["upload_id"]
    assert sorted(p.name for p in d.iterdir()) == sorted(["meta.json"] + [f"layer{e}" for e in SHP_PARTS])
    assert not list(tmp_path.rglob("evil*")) and not list(tmp_path.rglob("escape.txt"))


def test_oversize_upload_is_413(monkeypatch):
    os.environ.setdefault("ANTHROPIC_API_KEY", "placeholder-not-used-by-tested-endpoints")
    import app as app_module
    monkeypatch.setattr(app_module, "MAX_NETWORK_UPLOAD_BYTES", 50_000)
    c = TestClient(app_module.app)
    r = c.post("/api/forecast/upload-network",
               files=[("files", ("n.zip", b"\0" * 60_000))])
    assert r.status_code == 413
    # …but the 500 MB network cap is this route's only: a 30 MB network is not cut at 20 MB.
    monkeypatch.setattr(app_module, "MAX_NETWORK_UPLOAD_BYTES", 500 * 1024 * 1024)
    big = _zip([("n.shp", os.urandom(1024) * (21 * 1024))])
    assert len(big) > app_module.MAX_BODY_BYTES
    r = c.post("/api/forecast/upload-network", files=[("files", ("n.zip", big))])
    assert r.status_code == 400, r.status_code       # read, then rejected as an incomplete shapefile


def test_upload_ids_cannot_name_paths():
    for bad in ("../../etc", "/etc/passwd", "a" * 21, None, 7):
        with pytest.raises(upload.UploadError):
            upload.resolve(bad)


def test_expired_upload_is_cleaned_and_reported(monkeypatch):
    uid = _post(_sandag_parts(2025)).json()["upload_id"]
    monkeypatch.setattr(upload, "TTL_S", -1)
    with pytest.raises(upload.UploadError, match="expired"):
        upload.resolve(uid)
    assert not (upload.UPLOAD_ROOT / uid).exists()


# ── Column guess ──────────────────────────────────────────────────────────────
def _fields(path):
    import pyogrio
    info = pyogrio.read_info(path)
    return [{"name": n, "type": "number" if t.startswith(("int", "float")) else "text"}
            for n, t in zip(info["fields"], info["dtypes"])]


def test_guess_sandag_schema_full_file_columns():
    g = upload.guess_mapping(_fields(FIX / "sandag_2025" / "hwyLoad_2025.shp"))
    assert g["schema"] == "SANDAG"
    m = g["mapping"]
    assert (m["am_ab"], m["am_ba"], m["pm_ab"], m["pm_ba"]) == ("ab_am_flow", "ba_am_flow", "ab_pm_flow", "ba_pm_flow")
    assert m["connector_field"] == "fc" and m["connector_values"] == [10]
    assert set(g["confidence"].values()) == {"high"}


def test_guess_mtc_schema():
    names = ["A", "B", "DISTANCE", "LANES", "FT", "CITYNAME", "VOLEA_TOT", "VOLAM_DA", "VOLAM_TOT",
             "VOLMD_TOT", "VOLPM_DA", "VOLPM_TOT", "VOLEV_TOT", "VOL24HR_TOT", "TOLLAM_DA"]
    g = upload.guess_mapping([{"name": n, "type": "text" if n == "CITYNAME" else "number"} for n in names])
    assert g["schema"] == "MTC"
    assert g["mapping"]["am_ab"] == "VOLAM_TOT" and "am_ba" not in g["mapping"]
    assert g["mapping"]["connector_values"] == [6]


def test_guess_heuristics_unknown_mpo():
    r = _post([("files", ("n.zip", (FIX / "renamed_2025.zip").read_bytes()))])
    b = r.json()
    assert b["schema"] is None
    m = b["mapping"]
    assert (m["am_ab"], m["am_ba"], m["pm_ab"], m["pm_ba"]) == ("AM_AB_VOL", "AM_BA_VOL", "PM_AB_VOL", "PM_BA_VOL")
    assert (m["from_node"], m["to_node"], m["name"], m["link_id"]) == ("ANODE", "BNODE", "STREET", "LINKNO")
    assert (m["daily"], m["length_mi"], m["lanes_ab"], m["lanes_ba"]) == ("ADT", "MILES", "LANES_AB", "LANES_BA")
    assert m["connector_field"] == "CLASSNAME" and m["connector_values"] == ["Zone Connector"]
    assert b["confidence"]["am_ab"] == "high" and b["confidence"]["lanes_ab"] == "low"


def test_guess_one_way_heuristic():
    names = ["ANODE", "BNODE", "AM_VOL", "PM_VOL", "MD_VOL", "STREETNAME"]
    m = upload.guess_mapping([{"name": n, "type": "text" if n == "STREETNAME" else "number"} for n in names])["mapping"]
    assert (m["am_ab"], m["pm_ab"], m["name"]) == ("AM_VOL", "PM_VOL", "STREETNAME")
    assert "am_ba" not in m


def test_check_mapping_rejects_unknown_columns():
    with pytest.raises(upload.UploadError, match="isn't in"):
        upload.check_mapping({"am_ab": "../etc"}, ["AM"])
    with pytest.raises(upload.UploadError, match="morning"):
        upload.check_mapping({}, ["AM"])


# ── Equivalence with the built-in SANDAG path ────────────────────────────────
def _upload_spec(role, year, files):
    b = _post(files).json()
    return {"role": role, "scenario_year": year, "upload_id": b["upload_id"], "mapping": b["mapping"]}


def _strip(layer):
    """Layer fields the engine reads (provenance/labels differ by design)."""
    return [(l["link_id"], l["from_node"], l["to_node"], [tuple(map(lambda v: round(v, 9), c)) for c in l["geometry"]],
             round(l["length_mi"], 9), l["lanes"], l["name"], round(l["daily_total"], 6),
             {p: l["flow"]["ab"][p] for p in ("AM", "PM")}, {p: l["flow"]["ba"][p] for p in ("AM", "PM")})
            for l in sorted(layer["links"], key=lambda l: l["link_id"])]


def _strip_matches(ms):
    out = []
    for m in ms:
        m = {**m, "unbound_links": [{k: v for k, v in u.items() if k != "functional_class"} for u in m["unbound_links"]]}
        m["legs"] = {d: {**leg, "inbound": leg["inbound"] and {p: leg["inbound"][p] for p in ("AM", "PM")}}
                     for d, leg in m["legs"].items()}
        out.append(m)
    return out


def _run(ingest_body):
    return client.post("/forecast/run", json={
        "layers": ingest_body["layers"], "matches": ingest_body["matches"],
        "balanced_tmcs": {"AM": {1: COUNTS, 2: COUNTS}, "PM": {1: COUNTS, 2: COUNTS}},
        "links": [{"fromNum": 1, "toNum": 2, "direction": "EB/WB"}],
        "coords": {i["num"]: {"lat": i["lat"], "lng": i["lng"]} for i in INTX},
        "periods": ["AM", "PM"], "count_year": 2026, "horizon_year": 2045,
        "refine_method": "difference", "peak_hour_factor": 0.35,
    })


def _builtin():
    nets = [{"role": "base", "scenario_year": 2025, "path": FIX / "sandag_2025" / "hwyLoad_2025.shp"},
            {"role": "horizon", "scenario_year": 2050, "path": FIX / "sandag_2050" / "hwyLoad_2050.shp"}]
    layers = ingest_networks(INTX, nets, region="SANDAG")
    return {"layers": {r: l.model_dump(mode="json") for r, l in layers.items()},
            "matches": match_network_to_legs(INTX, layers["base"])}


def _connector_ids(layer):
    return sorted(l["link_id"] for l in layer["links"] if l["name"] == "TAZ Connector")


@pytest.mark.parametrize("keep_connectors", [True, False])
@pytest.mark.parametrize("files", ["sandag_parts", "renamed_zip"])
def test_upload_path_equals_builtin_sandag(files, keep_connectors):
    if files == "sandag_parts":
        nets = [_upload_spec("base", 2025, _sandag_parts(2025)), _upload_spec("horizon", 2050, _sandag_parts(2050))]
    else:
        nets = [_upload_spec(r, y, [("files", (f"n{y}.zip", (FIX / f"renamed_{y}.zip").read_bytes()))])
                for r, y in (("base", 2025), ("horizon", 2050))]
    if keep_connectors:   # the built-in SANDAG adapter keeps zone connectors
        for n in nets:
            n["mapping"].pop("connector_field")
    r = client.post("/forecast/ingest-match", json={"region": "UPLOAD", "intersections": INTX, "networks": nets})
    assert r.status_code == 200, r.text
    up, built = r.json(), _builtin()
    assert up["layers"]["base"]["region"] == "UPLOAD"
    for role in ("base", "horizon"):
        if keep_connectors:
            assert _strip(up["layers"][role]) == _strip(built["layers"][role])
        else:   # the guessed filter drops exactly the connectors; nothing else changes
            assert _connector_ids(up["layers"][role]) == [] and _connector_ids(built["layers"][role])
            assert _strip(up["layers"][role]) == [l for l in _strip(built["layers"][role]) if l[6] != "TAZ Connector"]
    assert _strip_matches(up["matches"]) == _strip_matches(built["matches"])
    assert all(leg["status"] == "matched" for m in up["matches"] for leg in m["legs"].values())

    ru, rb = _run(up), _run(built)
    assert ru.status_code == rb.status_code == 200, (ru.text, rb.text)
    for p in ("AM", "PM"):
        assert ru.json()[p]["pass2"]["balanced"] == rb.json()[p]["pass2"]["balanced"]
        assert ru.json()[p]["pass2"]["balanced_unrounded"] == rb.json()[p]["pass2"]["balanced_unrounded"]
        assert ru.json()[p]["vmt"] == rb.json()[p]["vmt"]
        assert ru.json()[p]["refinement"] == rb.json()[p]["refinement"]
    # Both uploads are deleted once ingested; a re-pull says to upload again.
    for n in nets:
        assert not (upload.UPLOAD_ROOT / n["upload_id"]).exists()
    r = client.post("/forecast/ingest-match", json={"region": "UPLOAD", "intersections": INTX, "networks": nets})
    assert r.status_code == 400 and "upload it again" in r.json()["detail"]


def test_upload_without_node_columns_derives_nodes():
    """No node / link-id columns: links are joined by end point and still match."""
    nets = []
    for role, y in (("base", 2025), ("horizon", 2050)):
        spec = _upload_spec(role, y, _sandag_parts(y))
        for k in ("from_node", "to_node", "link_id"):
            spec["mapping"].pop(k)
        nets.append(spec)
    r = client.post("/forecast/ingest-match", json={"region": "UPLOAD", "intersections": INTX, "networks": nets})
    assert r.status_code == 200, r.text
    ru, rb = _run(r.json()), _run(_builtin())
    for p in ("AM", "PM"):
        assert ru.json()[p]["pass2"]["balanced"] == rb.json()[p]["pass2"]["balanced"]


def test_one_way_records_are_paired(tmp_path):
    """MTC-style one-way records (one volume column per row) pair into AB/BA links."""
    import geopandas as gpd
    import pandas as pd
    g = gpd.read_file(FIX / "sandag_2025" / "hwyLoad_2025.shp")
    fwd = g.assign(A=g.from_node, B=g.to_node, AMVOL=g.ab_am_flow, PMVOL=g.ab_pm_flow)
    rev = g.assign(A=g.to_node, B=g.from_node, AMVOL=g.ba_am_flow, PMVOL=g.ba_pm_flow,
                   geometry=g.geometry.reverse())
    one = gpd.GeoDataFrame(pd.concat([fwd, rev])[["A", "B", "AMVOL", "PMVOL", "link_name", "geometry"]], crs=g.crs)
    one = one[(one.AMVOL > 0) | (one.PMVOL > 0)]
    one.to_file(tmp_path / "oneway.gpkg", driver="GPKG")
    b = _post([("files", ("oneway.gpkg", (tmp_path / "oneway.gpkg").read_bytes()))]).json()
    assert "am_ba" not in b["mapping"] and b["mapping"]["am_ab"] == "AMVOL"
    nets = [{"role": "base", "scenario_year": 2025, "upload_id": b["upload_id"], "mapping": b["mapping"]}]
    r = client.post("/forecast/ingest-match", json={"region": "UPLOAD", "intersections": INTX, "networks": nets})
    assert r.status_code == 200, r.text
    built = _builtin()
    inbound = lambda body: [{d: leg["inbound"] and (leg["inbound"]["AM"], leg["inbound"]["PM"])  # noqa: E731
                             for d, leg in m["legs"].items()} for m in body["matches"]]
    assert inbound(r.json()) == inbound(built)


# ── Local-only: the real clipped SANDAG files, kept outside the repo ─────────
from count_agent import THIRD_PARTY_DIR  # noqa: E402

RAW = THIRD_PARTY_DIR / "backend" / "tests" / "forecasting" / "fixtures"


@pytest.mark.skipif(not (RAW / "renamed_2025.zip").exists(),
                    reason="raw SANDAG clip not in THIRD_PARTY_DIR (third-party file, local-only)")
def test_real_sandag_clip_still_uploads():
    for name, schema in (("renamed_2025.zip", None), ("renamed_2050.zip", None)):
        r = _post([("files", (name, (RAW / name).read_bytes()))])
        assert r.status_code == 200, r.text
        assert r.json()["feature_count"] == 147 and r.json()["schema"] is schema
        assert r.json()["mapping"]["connector_values"] == ["Zone Connector"]
