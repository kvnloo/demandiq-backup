"""Regression tests for balancer bugs found by the independent math checker (Nov 2026).
Each failed before its fix. Script-style (run_tests.sh) and pytest-compatible.

    python test_balancer_regressions.py
"""

import copy
import json
import math
import os

import balancer as B

M = B.MOVEMENTS
LINK = [{"fromNum": 1, "toNum": 2, "direction": "EB/WB"}]
CO = {1: {"lat": 0, "lng": 0.0}, 2: {"lat": 0, "lng": 0.01}}


def full(v=100, **kw):
    d = {m: v for m in M if not m.endswith("U")}
    d.update(kw)
    return d


def test_coincident_coords_match_number_order():
    # BAL-1: identical coordinates must fall back to number order in EVERY step,
    # exactly as coords=None does (one shared orientation rule).
    vols = {1: full(EBT=400, WBT=50), 2: full(EBT=150, WBT=300)}
    same = {1: {"lat": 0, "lng": -117.0}, 2: {"lat": 0, "lng": -117.0}}
    r_same = B.balance_nchrp(copy.deepcopy(vols), LINK, coords=same)
    r_none = B.balance_nchrp(copy.deepcopy(vols), LINK, coords=None)
    assert r_same["balanced"] == r_none["balanced"]
    assert any(f["check"] == "GEOMETRY-FALLBACK" for f in r_same["qc_flags"])


def test_downstream_uturn_counts_as_arriving_on_the_link():
    # BAL-2: an EBU at int 2 arrived from int 1 on the EB link, like EBL/EBT/EBR.
    vols = {1: full(), 2: full(EBU=50)}
    r = B.balance_nchrp(copy.deepcopy(vols), LINK, coords=CO)
    eb = next(c for c in r["link_conservation"] if (c["fromNum"], c["toNum"], c["direction"]) == (1, 2, "EB"))
    b2 = r["balanced"][2]
    assert eb["downstream_inflow"] == sum(b2.get(k, 0) for k in ("EBL", "EBT", "EBR", "EBU"))
    assert abs(eb["delta"]) <= 0.5 or any(f["check"].startswith("NCHRP-LINK") for f in r["qc_flags"])


def test_reconciliation_never_invents_counted_zero_movements():
    # BAL-3: int 2's EB approach was counted as zero; reconciliation must not
    # create EBL out of nothing, and the shortfall must be visible.
    vols = {1: full(), 2: full(EBL=0, EBT=0, EBR=0)}
    r = B.balance_nchrp(copy.deepcopy(vols), LINK, coords=CO)
    assert all(r["balanced"][2][k] == 0 for k in ("EBL", "EBT", "EBR"))
    assert any(f["check"].startswith("NCHRP-LINK") for f in r["qc_flags"])


def geh(a, b):
    return math.sqrt(2 * (a - b) ** 2 / (a + b))


def test_corridor_through_not_inflated_at_t_intersections():
    # BAL-4: four Examiner tier-D puzzles (frozen from the harness seeds) where
    # adjacent corridor counts disagree around a T-intersection. The corridor
    # through used to absorb the whole disagreement (seed 34093 PM int 7 WBT:
    # key 60, raw 50, balanced 210). Now it moves only with its proportional
    # share: GEH vs its own count < 5 (the standard GEH acceptance line) and
    # vs the answer key <= 10 (the Examiner's FAIL line; what is left is the
    # upward bias of 'larger' link resolution), and links still close.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "through_inflation_cases.json")
    with open(path) as f:
        cases = json.load(f)
    for c in cases:
        raw = {int(n): v for n, v in c["raw"].items()}
        coords = {int(n): v for n, v in c["coords"].items()}
        r = B.balance_nchrp(raw, c["links"], method=c["method"], coords=coords)
        got = r["balanced"][c["int"]][c["movement"]]
        count = raw[c["int"]][c["movement"]]
        msg = f"{c['source']} int {c['int']} {c['movement']}: key {c['key']}, raw {count}, balanced {got}"
        assert geh(got, count) < 5, msg
        assert geh(got, c["key"]) <= 10, msg
        assert all(abs(e["delta"]) <= 0.5 for e in r["link_conservation"] if e["mode"] == "Full"), c["source"]


if __name__ == "__main__":
    for t in (test_coincident_coords_match_number_order,
              test_downstream_uturn_counts_as_arriving_on_the_link,
              test_reconciliation_never_invents_counted_zero_movements,
              test_corridor_through_not_inflated_at_t_intersections):
        t()
    print("test_balancer_regressions: 4 passed")
