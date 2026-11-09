"""
DemandIQ Forecasting — method benchmark on a made-up corridor with a known answer.

Builds a "truth world" for a 4-intersection east–west corridor, then a fake
regional model of it, and checks every link method × turn method against the
truth. Same engine code the app runs; nothing here is special-cased.

Truth world
  - Trips between the corridor ends and the side streets (gravity: big ends,
    smaller side streets, fewer long trips). Assigned → true 2025 turning counts.
  - A 2045 trip table per scenario:
      steady    every street grows 10–30 % (about 1 %/yr)
      develop   a new development at one intersection adds ~900 peak-hour
                trips that mostly stay nearby; existing trips +5 %
      decline   the corridor ends lose 10 %, side streets −5…+5 %
      shared_error  "steady", but the model's error on the main street (level
                and growth) is the same all along the corridor, not link by link
    Trip growth also depends on trip length and has its own noise per trip, so
    it is not an origin × destination product — no method is right by
    construction.
Counts: true 2025 movements ± 5 % count error, then balanced with DemandIQ's own
existing-count balancer (as the engineer would).
Regional model: 2013 and 2045 link volumes for a 4-hour AM period. Each link is
off by its own model error (±25 %, same error in both years — models are wrong
consistently) plus ±8 % extra in 2045. That is why NCHRP post-processing exists.
History (for the trend method): ten years of counts on the corridor growing
1 %/yr ± 3 % — the past, which only matches the future in "steady".

Scores (future turning movements vs truth):
  geh5   share of movements with GEH < 5 (the usual calibration bar)
  ape    mean absolute % error of approach totals

    cd backend && .venv-forecasting/bin/python -m forecasting.benchmark out.json
"""

import json
import math
import random
import sys

from balancer import DIRECTION_SPEC
from forecasting.contract import DirectionalFlow, NetworkLayer, NetworkLink, Period
from forecasting.od import OPPOSITE, _paths, _assign, forecast_trips
from forecasting.pass1 import forecast_corridor
from forecasting.pass2 import balance_future, round_forecast
from forecasting.refinement import refine_corridor
from forecasting.trend import fit_trend, uniform_growth

LINK_METHODS = ["ratio", "difference", "combined", "weighted", "modified_average",
                "nchrp765", "odot", "trend", "rate"]
TURN_METHODS = ["furness", "fratar", "fuzzy"]
SCENARIOS = ["steady", "develop", "decline", "shared_error"]
N_INT = 4
K = 0.30                       # peak hour ÷ 4-hour model period
COUNT_YEAR, HORIZON, MODEL_BASE, MODEL_FUT = 2025, 2045, 2013, 2045
MOVES = [d + t for d in ("NB", "SB", "EB", "WB") for t in "LTR"]


def _geh(m, c):
    return math.sqrt(2 * (m - c) ** 2 / (m + c)) if m + c > 0 else 0.0


def build_world(seed, scenario):
    rng = random.Random(seed)
    nums = list(range(1, N_INT + 1))
    coords = {n: {"lat": 37.58, "lng": -122.03 + 0.005 * n} for n in nums}
    links = [{"fromNum": n, "toNum": n + 1, "direction": "EB/WB", "mode": "Full"} for n in nums[:-1]]
    every = {n: {m: 1 for m in MOVES} for n in nums}
    trips = _paths(nums, "EB/WB", every)
    size = {}
    for o, d, _ in trips:
        for z in (o, d):
            if z not in size:
                end = (z[0] == 1 and z[1] in ("EB", "WB")) or (z[0] == N_INT and z[1] in ("EB", "WB"))
                size[z] = rng.uniform(800, 1200) if end else rng.uniform(100, 450)
    idx = {n: i for i, n in enumerate(nums)}
    base = [size[o] * size[d] / 900 * math.exp(-0.35 * abs(idx[o[0]] - idx[d[0]])) for o, d, _ in trips]
    zones = sorted(size)
    dist = [abs(idx[o[0]] - idx[d[0]]) for o, d, _ in trips]
    # Pair-level growth: longer trips grow a bit faster, every trip has its own
    # noise — not an origin-factor × destination-factor product, so the Fratar
    # method is not right by construction.
    pair = [math.exp(0.06 * (k - 1.5) + rng.gauss(0, 0.12)) for k in dist]
    if scenario in ("steady", "shared_error"):
        g = {z: rng.uniform(1.10, 1.30) for z in zones}
        fut = [b * math.sqrt(g[o] * g[d]) * p for b, (o, d, _), p in zip(base, trips, pair)]
    elif scenario == "decline":
        g = {z: (0.90 if z[1] in ("EB", "WB") else rng.uniform(0.95, 1.05)) for z in zones}
        fut = [b * math.sqrt(g[o] * g[d]) * p for b, (o, d, _), p in zip(base, trips, pair)]
    else:
        # New development on both side streets of one intersection: existing trips +5 %,
        # plus NEW trips that mostly stay near the site (a pattern today's counts don't have).
        dev = rng.choice(nums)
        site = lambda z: z[0] == dev and z[1] in ("NB", "SB")          # noqa: E731
        new = [(size[d] if site(o) else size[o]) * math.exp(-0.6 * k) if (site(o) or site(d)) else 0.0
               for (o, d, _), k in zip(trips, dist)]
        scale = 900 / sum(new)                                          # ≈ 900 new peak-hour trips
        fut = [b * 1.05 * p + n * scale for b, p, n in zip(base, pair, new)]
    true_base, true_fut = _assign(trips, base), _assign(trips, fut)
    for n in nums:
        for m in MOVES:
            true_base[n].setdefault(m, 0.0); true_fut[n].setdefault(m, 0.0)

    raw = {n: {m: max(0, round(v * (1 + rng.gauss(0, 0.05)))) for m, v in true_base[n].items()} for n in nums}
    counts = balance_future(raw, links, coords=coords)["balanced_unrounded"]

    # Regional model: link volumes by direction for each leg, as a 4-hour period
    def leg_in(t, n, d):  return sum(t[n][m] for m in DIRECTION_SPEC[d]["approach"])
    def leg_out(t, n, d): return sum(t[n][m] for m in DIRECTION_SPEC[OPPOSITE[d]]["outflow"])
    bias = {}
    shared = {d: (math.exp(rng.gauss(0, 0.25)), math.exp(rng.gauss(0, 0.08))) for d in ("eb", "wb")}
    def model(v, key, future):
        if scenario == "shared_error" and (key[0].startswith("C") or key[0] in ("W", "E")):
            # the model is wrong the same way all along the main street — its level
            # AND its growth error — instead of link by link (common in practice)
            # end links: inbound W / outbound E run eastbound, the others westbound
            d = key[1] if key[0].startswith("C") else (
                "eb" if (key[0], key[1]) in (("W", "in"), ("E", "out")) else "wb")
            lvl, grow = shared[d]
            b = bias.setdefault(key, lvl * math.exp(rng.gauss(0, 0.03)))
            return v / K * b * (grow * math.exp(rng.gauss(0, 0.02)) if future else 1.0)
        b = bias.setdefault(key, math.exp(rng.gauss(0, 0.25)))
        return v / K * b * (math.exp(rng.gauss(0, 0.08)) if future else 1.0)
    pairs = {}                                   # link_id → (from, to, ab key, ba key, legs)
    for n in nums:
        for d in ("NB", "SB"):                   # side streets: zone node → intersection node
            side_node = n * 100 + (1 if d == "NB" else 2)
            pairs[f"S{n}{d}"] = (side_node, n, (n, d))
        if n == 1:
            pairs["W"] = (1000, 1, (1, "EB"))
        if n == N_INT:
            pairs["E"] = (2000, n, (n, "WB"))
    layers = {}
    for which, truth, year in (("base", true_base, COUNT_YEAR), ("horizon", true_fut, HORIZON)):
        layers[which] = []
    for lid, (fnode, tnode, (n, d)) in pairs.items():
        b_in, b_out = model(leg_in(true_base, n, d), (lid, "in"), False), model(leg_out(true_base, n, d), (lid, "out"), False)
        f_in, f_out = model(leg_in(true_fut, n, d), (lid, "in"), True), model(leg_out(true_fut, n, d), (lid, "out"), True)
        for which, i, o in (("base", b_in, b_out), ("horizon", f_in, f_out)):
            layers[which].append((lid, fnode, tnode, i, o))
    for a, b in zip(nums, nums[1:]):             # corridor links: AB = eastbound
        lid = f"C{a}{b}"
        eb_b, wb_b = leg_in(true_base, b, "EB"), leg_in(true_base, a, "WB")
        eb_f, wb_f = leg_in(true_fut, b, "EB"), leg_in(true_fut, a, "WB")
        layers["base"].append((lid, a, b, model(eb_b, (lid, "eb"), False), model(wb_b, (lid, "wb"), False)))
        layers["horizon"].append((lid, a, b, model(eb_f, (lid, "eb"), True), model(wb_f, (lid, "wb"), True)))

    def back_cast(v25, v45):                     # model years are 2013/2045, counts 2025
        return max(0.1 * v25, v25 - (v45 - v25) * (COUNT_YEAR - MODEL_BASE) / (MODEL_FUT - COUNT_YEAR))
    hz = {r[0]: r for r in layers["horizon"]}
    def mk(rows, year, base_side):
        out = []
        for lid, fn, tn, ab, ba in rows:
            if base_side:
                ab, ba = back_cast(ab, hz[lid][3]), back_cast(ba, hz[lid][4])
            xy = (float(fn), 0.0), (float(tn), 0.0)
            out.append(NetworkLink(link_id=lid, from_node=fn, to_node=tn, geometry=list(xy),
                                   from_node_xy=xy[0], to_node_xy=xy[1], lanes=2,
                                   flow=DirectionalFlow(ab={Period.AM: ab}, ba={Period.AM: ba})))
        return NetworkLayer(region="BENCH", source_crs_epsg=4326, crs_epsg=4326, scenario_year=year, links=out)
    base_layer, hor_layer = mk(layers["base"], MODEL_BASE, True), mk(layers["horizon"], MODEL_FUT, False)

    leg_link = {}
    for lid, (fnode, tnode, (n, d)) in pairs.items():
        leg_link[(n, d)] = lid
    for a, b in zip(nums, nums[1:]):
        leg_link[(b, "EB")] = f"C{a}{b}"; leg_link[(a, "WB")] = f"C{a}{b}"
    matches = [{"intersection_num": n, "snapped_node": n,
                "legs": {d: {"status": "matched", "link_id": leg_link[(n, d)]} for d in ("NB", "SB", "EB", "WB")}}
               for n in nums]
    tot = sum(sum(v.values()) for v in true_base.values())
    history = [{"year": y, "volume": tot * (1.01 ** (y - COUNT_YEAR)) * (1 + rng.gauss(0, 0.03))}
               for y in range(2015, 2026)]
    return dict(nums=nums, coords=coords, links=links, counts=counts, truth=true_fut,
                base_layer=base_layer, horizon_layer=hor_layer, matches=matches, history=history)


def run_method(w, link_m, turn_m, band=0.05):
    tmcs = w["counts"]
    if link_m == "trend":
        t = fit_trend(w["history"], COUNT_YEAR)
        rr = uniform_growth(tmcs, Period.AM, t["factor"](HORIZON), label="historical trend", annual_rate=t["annual_rate"])
    elif link_m == "rate":
        rr = uniform_growth(tmcs, Period.AM, 1 + 0.01 * (HORIZON - COUNT_YEAR), label="growth rate entered", annual_rate=0.01)
    else:
        rr = refine_corridor(w["matches"], w["base_layer"], w["horizon_layer"], tmcs, Period.AM,
                             peak_hour_factor=K, method=link_m, count_year=COUNT_YEAR, horizon_year=HORIZON)
    if turn_m == "furness":
        fut, _ = forecast_corridor(rr, tmcs)
        fut = balance_future(fut, w["links"], coords=w["coords"])["balanced_unrounded"]
    else:
        fut, _, _ = forecast_trips(rr, tmcs, w["links"], w["coords"], method=turn_m, band=band)
    # score what the engineer gets: every method through the same final rounding
    return {n: {m: round_forecast(v) for m, v in mv.items()} for n, mv in fut.items()}


def score(fut, truth):
    gehs, apes = [], []
    for n, mv in truth.items():
        for m in MOVES:
            gehs.append(_geh(float(fut.get(n, {}).get(m, 0) or 0), mv[m]))
        for d in ("NB", "SB", "EB", "WB"):
            t = sum(mv[m] for m in DIRECTION_SPEC[d]["approach"])
            f = sum(float(fut.get(n, {}).get(m, 0) or 0) for m in DIRECTION_SPEC[d]["approach"])
            if t > 0:
                apes.append(abs(f - t) / t)
    return {"geh5": sum(g < 5 for g in gehs) / len(gehs), "ape": sum(apes) / len(apes)}


def benchmark(seeds=20, scenarios=SCENARIOS, link_methods=LINK_METHODS, turn_methods=TURN_METHODS):
    rows = []
    for sc in scenarios:
        for s in range(seeds):
            w = build_world(1000 + s, sc)
            for lm in link_methods:
                for tm in turn_methods:
                    r = score(run_method(w, lm, tm), w["truth"])
                    rows.append({"scenario": sc, "seed": s, "link": lm, "turn": tm, **r})
    summary = {}
    for r in rows:
        k = (r["scenario"], r["link"], r["turn"])
        summary.setdefault(k, []).append(r)
    out = [{"scenario": k[0], "link": k[1], "turn": k[2],
            "geh5": round(sum(x["geh5"] for x in v) / len(v), 4),
            "ape": round(sum(x["ape"] for x in v) / len(v), 4), "runs": len(v)} for k, v in summary.items()]
    return {"seeds": seeds, "summary": out}


LINK_LABEL = {"ratio": "Ratio", "difference": "Difference", "combined": "Average (NCHRP 255)",
              "weighted": "Weighted growth", "modified_average": "Modified average",
              "nchrp765": "Auto — NCHRP 765", "odot": "Auto — ODOT tree",
              "trend": "Historical trend (no model)", "rate": "Growth rate entered (no model)"}
TURN_LABEL = {"furness": "NCHRP standard", "fratar": "Trip table — Fratar", "fuzzy": "Trip table — fuzzy"}
SCENARIO_LABEL = {"steady": "Steady growth (~1 %/yr)", "develop": "New development (+900 trips at one intersection)",
                  "decline": "Decline (ends −10 %)", "shared_error": "Steady, model wrong the same way along the corridor"}


def report_tables(res):
    """HTML tables: share of movements within GEH 5 for every combination, per scenario."""
    by = {(r["scenario"], r["link"], r["turn"]): r for r in res["summary"]}
    html = []
    for sc in SCENARIOS:
        cells = [by[(sc, lm, tm)]["geh5"] for lm in LINK_METHODS for tm in TURN_METHODS if (sc, lm, tm) in by]
        if not cells:
            continue
        lo, hi = min(cells), max(cells)
        best = max((by[k] for k in by if k[0] == sc), key=lambda r: r["geh5"])
        rows = []
        for lm in LINK_METHODS:
            tds = []
            for tm in TURN_METHODS:
                r = by.get((sc, lm, tm))
                if r is None:
                    tds.append("<td></td>"); continue
                t = (r["geh5"] - lo) / (hi - lo) if hi > lo else 1
                bg = f"rgba(30,227,154,{0.08 + 0.55 * t:.2f})"
                tds.append(f'<td style="background:{bg}"><b>{r["geh5"]:.0%}</b><small> ±{r["ape"]:.0%}</small></td>')
            rows.append(f"<tr><th>{LINK_LABEL[lm]}</th>{''.join(tds)}</tr>")
        html.append(f"""<h3>{SCENARIO_LABEL[sc]}</h3>
<p class="mu">Best: {LINK_LABEL[best['link']]} + {TURN_LABEL[best['turn']]} — {best['geh5']:.0%} of movements within GEH 5.</p>
<table class="bench"><tr><th></th>{''.join(f'<th>{TURN_LABEL[t]}</th>' for t in TURN_METHODS)}</tr>{''.join(rows)}</table>""")
    return "\n".join(html)


if __name__ == "__main__":
    res = benchmark(seeds=int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    path = sys.argv[1] if len(sys.argv) > 1 else "benchmark.json"
    with open(path, "w") as f:
        json.dump(res, f, indent=1)
    if len(sys.argv) > 3:                     # optional: fill the methods guide's results block
        guide = open(sys.argv[3]).read()
        a, b = "<!-- BENCH -->", "<!-- /BENCH -->"
        i, j = guide.index(a) + len(a), guide.index(b)
        with open(sys.argv[3], "w") as f:
            f.write(guide[:i] + "\n" + report_tables(res) + "\n" + guide[j:])
    for sc in SCENARIOS:
        best = sorted((r for r in res["summary"] if r["scenario"] == sc), key=lambda r: -r["geh5"])
        print(sc, [(r["link"], r["turn"], r["geh5"], r["ape"]) for r in best[:5]])
