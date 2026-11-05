"""Regression tests for the pre-launch QC findings (backend, Nov 2026).
Each failed before its fix. Script-style (run_tests.sh) and pytest-compatible.

    python test_qc_backend_fixes.py
"""

import io
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import http.client

os.environ.setdefault("ANTHROPIC_API_KEY", "placeholder-not-used-by-tested-endpoints")

from fastapi.testclient import TestClient
from openpyxl import load_workbook

import app as app_module
from app import app
import balancer as B
from excel_export import build_workbook
from line_diagram import _segment_label_for_pair, corridor_cols

client = TestClient(app)
MVS = ['EBL', 'EBT', 'EBR', 'WBL', 'WBT', 'WBR', 'NBL', 'NBT', 'NBR', 'SBL', 'SBT', 'SBR']
LEAKS = ("Traceback", "/Users/", "site-packages", ".py", "Error(", "KeyError", "TypeError")


def corridor(n, seed=0, vmax=1500):
    """n linked 4-leg intersections west→east with random counts (the QC fixture)."""
    rng = random.Random(seed)
    ints, am, pm = [], {}, {}
    for i in range(1, n + 1):
        ints.append({"num": i, "name": f"Int {i}", "lat": 32.7, "lng": -117.1 + i * 0.01})
        am[str(i)] = {m: rng.randint(0, vmax) for m in MVS}
        pm[str(i)] = {m: rng.randint(0, vmax) for m in MVS}
    links = [{"fromNum": i, "toNum": i + 1, "direction": "EB/WB", "mode": "Full"} for i in range(1, n)]
    return {"intersections": ints, "unbalanced_volumes": {"AM": am, "PM": pm}, "links": links}


def _assert_plain_400(r, label):
    assert r.status_code == 400, f"{label}: expected 400, got {r.status_code} {r.text[:200]}"
    detail = r.json()["detail"]
    assert isinstance(detail, str) and not any(x in detail for x in LEAKS), f"{label}: {detail!r}"


# ── 1. Empty period / linked intersection without counts ────────────────────
def test_empty_period_is_skipped():
    p = corridor(3)
    p["unbalanced_volumes"]["PM"] = {}          # AM-only project
    r = client.post("/balance-nchrp", json=p)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["balanced"]["PM"] == {} and len(body["balanced"]["AM"]) == 3


def test_linked_intersection_without_counts_is_400():
    p = corridor(3)
    del p["unbalanced_volumes"]["AM"]["2"]
    r = client.post("/balance-nchrp", json=p)
    _assert_plain_400(r, "missing linked counts")
    assert "Intersection 2" in r.json()["detail"] and "AM" in r.json()["detail"]


# ── 2. Validation at the trust boundary ─────────────────────────────────────
def _bad_balance_payloads():
    def mut(fn):
        p = corridor(2)
        fn(p)
        return p
    am1 = lambda p: p["unbalanced_volumes"]["AM"]["1"]
    cases = {
        "negative": mut(lambda p: am1(p).update(EBT=-500)),
        "too big": mut(lambda p: am1(p).update(EBT=1e9)),
        "bool": mut(lambda p: am1(p).update(EBT=True)),
        "string": mut(lambda p: am1(p).update(EBT="lots")),
        "dict": mut(lambda p: am1(p).update(EBT={"value": 5})),
        "num abc": mut(lambda p: p["intersections"][0].update(num="abc")),
        "num null": mut(lambda p: p["intersections"][0].update(num=None)),
        "num list": mut(lambda p: p["intersections"][0].update(num=[1])),
        "num 1.7": mut(lambda p: p["intersections"][0].update(num=1.7)),
        "duplicate num": mut(lambda p: p["intersections"][1].update(num=1)),
        "lat text": mut(lambda p: p["intersections"][0].update(lat="north")),
        "AM list": mut(lambda p: p["unbalanced_volumes"].update(AM=[1, 2])),
        "AM key abc": mut(lambda p: p["unbalanced_volumes"]["AM"].update(abc={"EBT": 5})),
        "AM unlisted 99": mut(lambda p: p["unbalanced_volumes"]["AM"].update({"99": {"EBT": 5}})),
        "movements list": mut(lambda p: p["unbalanced_volumes"]["AM"].update({"1": [1, 2]})),
        "link no fromNum": mut(lambda p: p["links"][0].pop("fromNum")),
        "link fromNum x": mut(lambda p: p["links"][0].update(fromNum="x")),
        "link to 99": mut(lambda p: p["links"][0].update(toNum=99)),
        "self link": mut(lambda p: p["links"].append({"fromNum": 1, "toNum": 1, "direction": "EB/WB"})),
        "two axes": mut(lambda p: p["links"].append({"fromNum": 1, "toNum": 2, "direction": "NB/SB"})),
        "leg linked twice": mut(lambda p: (p["intersections"].append({"num": 3, "lat": 32.7, "lng": -117.07}),
                                           p["unbalanced_volumes"]["AM"].update({"3": {"EBT": 5, "WBT": 5}}),
                                           p["links"].append({"fromNum": 1, "toNum": 3, "direction": "EB/WB"}))),
        "no volumes": mut(lambda p: p.pop("unbalanced_volumes")),
        "tolerance -1": mut(lambda p: p.update(tolerance=-1)),
        "tolerance 0": mut(lambda p: p.update(tolerance=0)),
        "max_iter -5": mut(lambda p: p.update(max_iterations=-5)),
    }
    raw = {   # JSON tokens json.dumps can't produce from a dict mutation
        "NaN volume": json.dumps(corridor(2)).replace('"EBT": ', '"EBT": NaN, "x": ', 1),
        "Infinity volume": json.dumps(corridor(2)).replace('"EBT": ', '"EBT": Infinity, "x": ', 1),
        "max_iter Infinity": json.dumps(corridor(2))[:-1] + ', "max_iterations": Infinity}',
        "tolerance NaN": json.dumps(corridor(2))[:-1] + ', "tolerance": NaN}',
    }
    return cases, raw


def test_balance_rejects_bad_input_with_plain_400():
    cases, raw = _bad_balance_payloads()
    for label, p in cases.items():
        _assert_plain_400(client.post("/balance-nchrp", json=p), label)
    for label, body in raw.items():
        r = client.post("/balance-nchrp", content=body, headers={"content-type": "application/json"})
        _assert_plain_400(r, label)
    r = client.post("/balance-nchrp", json=_bad_balance_payloads()[0]["negative"])
    assert "Intersection 1 AM EBT" in r.json()["detail"], r.json()["detail"]


def _geo(n):
    return {"num": n, "name": f"Main St & {n} Ave", "active_legs": ["EB", "WB", "NB", "SB"],
            "streets": {"EW": "Main St", "NS": f"{n} Ave"}}


def test_diagram_routes_reject_bad_input_with_plain_400():
    cell = {"intersection": _geo(1), "u_vols": {"EBT": 10}, "b_vols": {"EBT": 10}}
    corr = {"cells": [cell, {**cell, "intersection": _geo(2)}], "corridor_dir": "EB/WB",
            "resolved_links": {"AM": [], "PM": []}}
    cases = [
        ("/intersection-diagram", {"intersection": _geo(1), "b_vols": {"EBT": "lots"}}),
        ("/intersection-diagram", {"intersection": {"num": 1}, "approaches": "x"}),
        ("/export-diagram-grid", {"cells": [{**cell, "u_vols": [1]}]}),
        ("/export-diagram-grid", {"cells": [cell] * 201}),
        ("/corridor-diagram", {**corr, "b_vols": [1]}),
        ("/corridor-diagram", {**corr, "b_vols": {"AM": [1]}}),
        ("/corridor-diagram", {**corr, "cols": 0}),
        ("/corridor-diagram", {**corr, "resolved_links": {"AM": [{"fromNum": "x"}], "PM": []}}),
        ("/corridor-diagram", {**corr, "cells": [{**cell, "intersection": {**_geo(1), "num": "abc"}}]}),
    ]
    for path, body in cases:
        _assert_plain_400(client.post(path, json=body), f"{path} {str(body)[:60]}")
    nan = '{"intersection": %s, "b_vols": {"EBT": NaN}}' % json.dumps(_geo(1))
    _assert_plain_400(client.post("/intersection-diagram", content=nan,
                                  headers={"content-type": "application/json"}), "NaN b_vols")


def _export_state(name="Main St & 1st Ave", **project):
    return {
        "project": {"name": "QC", **project},
        "intersections": [{
            "num": 1, "name": name,
            "approaches": {d: {"exists": True, "lanes": [{"movement": "LTR"}]} for d in ("EB", "WB", "NB", "SB")},
            "volumes": {"AM": {"EBT": {"value": 100}}},
            "balanced": {"AM": {"EBT": 105}},
        }],
        "balancing": {"overrides": {"AM": {}, "PM": {}}},
        "analysis": {"lastRun": {}},
    }


def test_excel_routes_reject_bad_input_with_plain_400():
    s = _export_state
    cases = [
        ("/excel", {"results": "abc"}),
        ("/excel", {"results": [1]}),
        ("/excel", {"results": [{"intersections": "x"}]}),
        ("/excel", {"results": [{"intersections": [{"volumes": [1]}]}]}),
        ("/excel", {"results": [{"intersections": [{"volumes": {"AM": [1]}}]}]}),
        ("/excel", {"results": [{"intersections": [{"volumes": {"AM": {"EBT": {"v": 1}}}}]}]}),
        ("/export-excel", {**s(), "project": "abc"}),
        ("/export-excel", {**s(), "project": {"name": 5}}),
        ("/export-excel", {**s(), "intersections": "x"}),
        ("/export-excel", {**s(), "intersections": [1]}),
        ("/export-excel", {**s(), "balancing": [1]}),
        ("/export-excel", {**s(), "analysis": {"lastRun": [1]}}),
        ("/export-excel", {**s(), "intersections": [{**s()["intersections"][0], "approaches": {"EB": {"lanes": "LTR"}}}]}),
    ]
    for path, body in cases:
        _assert_plain_400(client.post(path, json=body), f"{path} {str(body)[:80]}")
    mixed = s()
    mixed["intersections"].append({**mixed["intersections"][0], "num": "2"})
    assert client.post("/export-excel", json=mixed).status_code == 200


# ── 5. Formula injection + control characters ───────────────────────────────
def _no_formulas(xlsx):
    wb = load_workbook(io.BytesIO(xlsx))           # formulas kept (data_only=False)
    cells = [c for ws in wb.worksheets for row in ws.iter_rows() for c in row]
    return [c.coordinate for c in cells if c.data_type == 'f'], cells


def test_excel_writes_formula_like_text_as_literal():
    evil = '=HYPERLINK("http://evil.example","click")'
    r = client.post("/export-excel", json=_export_state(name=evil))
    assert r.status_code == 200, r.text[:200]
    formulas, cells = _no_formulas(r.content)
    assert not formulas, f"formula cells: {formulas}"
    assert any(c.value == evil for c in cells), "name text should be kept verbatim"

    r = client.post("/excel", json={"results": [{"source_file": "+cmd", "intersections": [
        {"name": "=1+1", "notes": "@SUM(A1)", "volumes": {"AM": {"EBT": 5}}}]}]})
    assert r.status_code == 200, r.text[:200]
    formulas, _ = _no_formulas(r.content)
    assert not formulas, f"formula cells: {formulas}"


def test_excel_strips_control_characters():
    r = client.post("/excel", json={"results": [{"intersections": [{"name": "a\u0000b\u0007c"}]}]})
    assert r.status_code == 200, r.text[:200]
    r = client.post("/export-excel", json=_export_state(name="Main\u0000St"))
    assert r.status_code == 200, r.text[:200]
    _, cells = _no_formulas(r.content)
    assert any(c.value == "MainSt" for c in cells)


# ── 11. Excel overrides numeric + readable dates ─────────────────────────────
def test_excel_header_has_readable_dates():
    st = _export_state(client="City of Oakwood", analyst="A. Engineer", date="2026-11-05")
    st["analysis"]["lastRun"] = {"timestamp": "2026-11-05T21:12:00.000Z"}
    st["timezone"] = "America/New_York"
    ws = load_workbook(io.BytesIO(build_workbook(st)))["Provenance"]
    header = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(1, 14)}
    assert header["Client"] == "City of Oakwood" and header["Analyst"] == "A. Engineer"
    assert header["Date"] == "Nov 5, 2026", header["Date"]
    assert header["Run at"] == "Nov 5, 2026 4:12 PM EST", header["Run at"]


# ── 4. Limits ───────────────────────────────────────────────────────────────
def test_body_size_cap_is_413():
    body = b'{"x": "' + b"a" * (app_module.MAX_BODY_BYTES + 10) + b'"}'
    r = client.post("/balance-nchrp", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 413, r.status_code


def test_extract_limits_and_all_rejected_is_400():
    files = [("files", (f"f{i}.pdf", b"x", "application/pdf")) for i in range(app_module.MAX_EXTRACT_FILES + 1)]
    _assert_plain_400(client.post("/extract", files=files), "too many files")
    r = client.post("/extract", files=[("files", ("a.exe", b"x", "application/octet-stream")),
                                       ("files", ("noext", b"x", "application/octet-stream"))])
    _assert_plain_400(r, "all rejected")
    assert "a.exe" in r.json()["detail"]
    big = b"0" * (app_module.MAX_EXTRACT_FILE_BYTES + 1)
    r = client.post("/extract", files=[("files", ("big.csv", big, "text/csv"))])
    _assert_plain_400(r, "too large")
    assert "larger than" in r.json()["detail"]


# ── 6. Corridor segment labels show balanced flow, both directions ──────────
def test_corridor_labels_use_balanced_flow_both_directions():
    p = corridor(4, seed=7)
    out = client.post("/balance-nchrp", json=p).json()
    b = {int(k): v for k, v in out["balanced"]["AM"].items()}
    resolved = out["resolved_links"]["AM"]
    cons = {(c["fromNum"], c["toNum"], c["direction"]): c["upstream_outflow"]
            for c in out["link_conservation"]["AM"]}
    for a in (1, 2, 3):
        text, _ = _segment_label_for_pair(a, a + 1, resolved, "EB/WB", b)
        assert f"EB {a}→{a + 1}: {int(round(cons[(a, a + 1, 'EB')]))} veh" in text, text
        assert f"WB {a + 1}→{a}: {int(round(cons[(a + 1, a, 'WB')]))} veh" in text, text

    cells = [{"intersection": _geo(n), "u_vols": p["unbalanced_volumes"]["AM"][str(n)]} for n in (1, 2, 3, 4)]
    r = client.post("/corridor-diagram", json={
        "cells": cells, "corridor_dir": "EB/WB", "cols": 3,
        "resolved_links": out["resolved_links"], "b_vols": out["balanced"]})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["segment_counts"] == {"AM": 3, "PM": 3}, r.json()["segment_counts"]
    assert corridor_cols(4, 3) == 4 and r.json()["am"]["width"] == 4 * 600


# ── 8. SOURCE-OUTLIER: σ floored at 10% of the median ───────────────────────
def _eb(totals):
    return {i: {"EBL": 0, "EBT": t, "EBR": 0} for i, t in enumerate(totals, start=1)}


def test_outlier_ignores_normal_variation_but_flags_typo():
    assert B.source_outlier_flags(_eb([822, 820, 825, 818, 745]), period="AM") == []
    flags = B.source_outlier_flags(_eb([1200, 1210, 1190, 1205, 12]), period="AM")
    assert [f["int"] for f in flags] == [5], flags
    # identical peers (MAD = 0) used to skip the check entirely and hide the typo
    flags = B.source_outlier_flags(_eb([1200, 1200, 1200, 1200, 12]), period="AM")
    assert [f["int"] for f in flags] == [5], flags


# ── 9. Convergence honours the requested tolerance (vehicles) ───────────────
def test_converged_only_when_residual_within_tolerance():
    seen_unconverged = False
    for seed in range(6):
        out = client.post("/balance-nchrp", json={**corridor(5, seed=seed), "tolerance": 0.01}).json()
        for period in ("AM", "PM"):
            for n, conv in out["converged"][period].items():
                res = out["final_residual"][period][n]
                assert conv == (res <= 0.01), f"seed {seed} {period} int {n}: converged={conv} residual={res}"
                seen_unconverged |= not conv
    assert seen_unconverged, "fixture should include a residual above 0.01 veh"
    out = client.post("/balance-nchrp", json={**corridor(5, seed=0), "tolerance": 5}).json()
    assert all(out["converged"]["AM"].values())


# ── 10. Docs hidden behind APP_PASSWORD ─────────────────────────────────────
def test_docs_disabled_when_password_set():
    code = ("from fastapi.testclient import TestClient; import app; c = TestClient(app.app); "
            "print([c.get(p).status_code for p in ('/docs', '/redoc', '/openapi.json')])")
    env = {**os.environ, "APP_PASSWORD": "pw"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.abspath(__file__)))
    codes = json.loads(out.stdout.strip().splitlines()[-1])
    assert all(c in (401, 404) for c in codes), codes


# ── 3. Heavy routes don't block the event loop ──────────────────────────────
def test_health_stays_fast_during_heavy_request():
    import uvicorn
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        cells = [{"intersection": _geo(n), "u_vols": {"EBT": 10}, "b_vols": {"EBT": 10}} for n in range(1, 25)]
        heavy = json.dumps({"cells": cells}).encode()
        done = threading.Event()

        def fire():
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
            c.request("POST", "/export-diagram-grid", body=heavy, headers={"content-type": "application/json"})
            assert c.getresponse().status == 200
            done.set()

        threading.Thread(target=fire, daemon=True).start()
        time.sleep(0.3)
        worst = 0.0
        while not done.is_set():
            t = time.time()
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            c.request("GET", "/health")
            assert c.getresponse().status == 200
            worst = max(worst, time.time() - t)
            time.sleep(0.1)
        assert worst < 0.5, f"/health took {worst:.2f}s during a heavy render"
    finally:
        server.should_exit = True


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  {t.__name__}: PASS")
    print(f"test_qc_backend_fixes: {len(tests)} passed")
