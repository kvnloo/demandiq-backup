"""
DemandIQ Forecasting — corridor trip-table (origin–destination) methods.

Alternative to Pass 1 (per-intersection Furness) + Pass 2 (corridor balancing).
Instead of growing each intersection on its own and reconciling afterwards, it
works with TRIPS through the corridor: "enters at the west end, leaves on 3rd
Ave northbound". Every intersection then agrees with its neighbours by
construction, because each trip is counted once at every movement it uses.

  1. Base trip table: estimate the trips that reproduce the balanced existing
     turning counts (multiplicative fitting from an even seed = the most-spread,
     maximum-entropy table consistent with the counts; Van Zuylen & Willumsen
     1980, NCHRP 765 §6.11). The counts are already balanced, so they are fit
     exactly — no growth gives back today's counts.
  2. Future trip table, one of:
       - FRATAR: scale the base table so each entry point sends, and each exit
         point receives, its forecast total (iterative growth-factor / Fratar
         method; NCHRP 765 §6.4 applies the same idea at one intersection).
       - FUZZY: start from the Fratar table, then adjust it so every approach
         and exit total at every intersection (main-street legs too) lands
         within ±b of its own forecast — each
         forecast is treated as "about right" (a fuzzy band, as PTV Visum's
         TFlowFuzzy treats counts), so conflicting leg forecasts are settled
         by the trips instead of by a separate balancing pass.
  3. Assign: future turning movement = sum of the future trips that use it.

Scope (shortcut: one straight corridor or isolated intersections; a grid or a
branching network needs path choice — upgrade by adding a path builder per
network): intersections joined by links must form one straight chain with a
single corridor direction (EB/WB or NB/SB). Trips follow the only path along the
chain. U-turns are not trips (they pass through from the base, as in Pass 1).

PURE PYTHON — no geo deps.
"""

from balancer import DIRECTION_SPEC

OPPOSITE = {"NB": "SB", "SB": "NB", "EB": "WB", "WB": "EB"}
# approach heading → {turn: exit heading}
TURN_EXIT = {"NB": {"L": "WB", "T": "NB", "R": "EB"}, "SB": {"L": "EB", "T": "SB", "R": "WB"},
             "EB": {"L": "NB", "T": "EB", "R": "SB"}, "WB": {"L": "SB", "T": "WB", "R": "NB"}}
EXIT_TURN = {a: {h: t for t, h in m.items()} for a, m in TURN_EXIT.items()}
TOL = 0.002          # converged when every constraint is within 0.2 % of its band
MAX_ITER = 2000


def _sum(vols, movements):
    return sum(float(vols.get(m, 0) or 0) for m in movements)


# ── Corridor topology ─────────────────────────────────────────────────────────
def corridor_groups(nums, links, coords):
    """Split intersections into straight chains. Returns [(ordered nums, axis)],
    axis 'EB/WB' | 'NB/SB' | None (single intersection). Raises ValueError for
    shapes the trip methods can't route (branching, mixed directions, cycles)."""
    adj = {n: set() for n in nums}
    axis_of = {}
    for L in links:
        a, b = int(L["fromNum"]), int(L["toNum"])
        if a not in adj or b not in adj:
            continue
        if L.get("mode") == "None":
            continue                         # excluded link = the corridor breaks here
        adj[a].add(b); adj[b].add(a)
        axis_of[frozenset((a, b))] = "EB/WB" if L["direction"] in ("EB/WB", "EB", "WB") else "NB/SB"
    seen, groups = set(), []
    for n in nums:
        if n in seen:
            continue
        comp, stack = [], [n]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x); comp.append(x); stack.extend(adj[x] - seen)
        if len(comp) == 1:
            groups.append(([n], None)); continue
        if any(len(adj[x]) > 2 for x in comp) or sum(len(adj[x]) for x in comp) != 2 * (len(comp) - 1):
            raise ValueError("the trip-table methods need each corridor to be one straight chain of "
                             "intersections (no branches or loops) — use the NCHRP standard method here.")
        axes = {axis_of[frozenset((a, b))] for a in comp for b in adj[a]}
        if len(axes) != 1:
            raise ValueError("the trip-table methods need one corridor direction (all links EB/WB or all NB/SB).")
        axis = axes.pop()
        # Walk the chain along its links; coordinates (else intersection numbers,
        # the balancer's fallback) only decide which end is west / south.
        order, prev = [min(x for x in comp if len(adj[x]) == 1)], None
        while len(order) < len(comp):
            nxt = next(iter(adj[order[-1]] - {prev}))
            prev = order[-1]; order.append(nxt)
        ax = "lng" if axis == "EB/WB" else "lat"
        a_, b_ = coords.get(order[0], {}).get(ax), coords.get(order[-1], {}).get(ax)
        if a_ is not None and b_ is not None and abs(a_ - b_) > 1e-9:
            if a_ > b_:
                order.reverse()
        elif order[0] > order[-1]:
            order.reverse()
        groups.append((order, axis))
    return groups


def _paths(order, axis, counts):
    """Every feasible trip in one chain → list of (origin, dest, [(num, movement)])."""
    fwd, back = ("EB", "WB") if axis == "EB/WB" else ("NB", "SB")
    first, last = order[0], order[-1]

    def has_leg(num, heading_in):
        spec = DIRECTION_SPEC[heading_in]
        c = counts.get(num, {})
        return _sum(c, spec["approach"]) > 0 or _sum(c, DIRECTION_SPEC[OPPOSITE[heading_in]]["outflow"]) > 0

    def external_in(num):     # headings a trip can ENTER with at this intersection
        hs = [h for h in ("NB", "SB", "EB", "WB") if axis is None or h not in (fwd, back)]
        if axis is not None:
            if num == first: hs.append(fwd)
            if num == last: hs.append(back)
        return [h for h in hs if has_leg(num, h)]

    def external_out(num):    # headings a trip can LEAVE with here
        hs = [h for h in ("NB", "SB", "EB", "WB") if axis is None or h not in (fwd, back)]
        if axis is not None:
            if num == first: hs.append(back)
            if num == last: hs.append(fwd)
        return [h for h in hs if has_leg(num, OPPOSITE[h])]

    idx = {n: i for i, n in enumerate(order)}
    out = []
    for o in order:
        for hin in external_in(o):
            for d in order:
                for hout in external_out(d):
                    if o == d:
                        if hout == OPPOSITE[hin]:
                            continue              # U-turn
                        path = [(o, hin + EXIT_TURN[hin][hout])]
                    else:
                        run = fwd if idx[d] > idx[o] else back
                        if hin == OPPOSITE[run] or hout == OPPOSITE[run]:
                            continue              # would need a U-turn
                        step = 1 if run == fwd else -1
                        path = [(o, hin + EXIT_TURN[hin][run])]
                        path += [(order[k], run + "T") for k in range(idx[o] + step, idx[d], step)]
                        path.append((d, run + EXIT_TURN[run][hout]))
                    if all(float(counts.get(n, {}).get(m, 0) or 0) > 0 for n, m in path):
                        out.append(((o, hin), (d, hout), path))
    return out


# ── Fitting ───────────────────────────────────────────────────────────────────
def fit(x, members, bands, *, tol=TOL, max_iter=MAX_ITER):
    """Multiplicative fitting: scale the trips in each constraint until its sum
    lies inside [lo, hi]. members: {cid: [trip idx]}; bands: {cid: (lo, hi)}.
    With lo == hi it is exact (Furness/Fratar/ME2). Returns (x, iters, worst)."""
    x = list(x)
    worst = 0.0
    for it in range(1, max_iter + 1):
        worst = 0.0
        for cid, ks in members.items():
            lo, hi = bands[cid]
            v = sum(x[k] for k in ks)
            if v <= 0:
                continue
            f = lo / v if v < lo else hi / v if v > hi else 1.0
            if f != 1.0:
                worst = max(worst, abs(f - 1))
                for k in ks:
                    x[k] *= f
        if worst < tol:
            return x, it, worst
    return x, max_iter, worst


def _assign(trips, x):
    vol = {}
    for (_, _, path), v in zip(trips, x):
        for n, m in path:
            vol.setdefault(n, {})
            vol[n][m] = vol[n].get(m, 0.0) + v
    return vol


def _targets(rr, base, manual=None):
    """Forecast approach (inbound) and exit (outbound) totals per leg from the
    link step; pending legs take the engineer's manual target, else are held
    flat at the count (as Pass 1 does)."""
    tin, tout, flags = {}, {}, []
    manual = manual or {}
    for d, leg in (rr["approaches"] or {}).items():
        if leg.get("status") == "absent":
            continue
        if leg.get("status") == "bound":
            tin[d], tout[d] = leg["inbound"]["refined"], leg["outbound"]["refined"]
        elif d in manual:
            tin[d], tout[d] = float(manual[d]["inbound"]), float(manual[d]["outbound"])
            flags.append({"code": "OD-PENDING-MANUAL", "message": f"{d} leg used the engineer's manual target."})
        else:
            tin[d] = _sum(base, DIRECTION_SPEC[d]["approach"])
            tout[d] = _sum(base, DIRECTION_SPEC[OPPOSITE[d]]["outflow"])
            flags.append({"code": "OD-PENDING-HELD-FLAT",
                          "message": f"{d} leg has no forecast — held at today's volume."})
    return tin, tout, flags


def forecast_trips(refinement_results, base_tmcs, links, coords, *, method="fratar", band=0.05,
                   manual_targets=None):
    """Trip-table forecast for one period.

    refinement_results: link-step output (refine_corridor or uniform_growth).
    base_tmcs: {num: {movement: vol}} balanced existing counts.
    method: 'fratar' | 'fuzzy'.  band: fuzzy tolerance as a fraction (0.05 = ±5 %).
    Returns (futures {num: {movement: vol}}, meta {num: {...}}, summary).
    """
    if method not in ("fratar", "fuzzy"):
        raise ValueError(f"unknown trip method {method!r}")
    if not 0 <= band < 0.5:
        raise ValueError("fuzzy band must be between 0 and 50 %")
    rr_by = {r["intersection_num"]: r for r in refinement_results}
    nums = [n for n in rr_by if n in base_tmcs]
    futures, meta, summary = {}, {}, []
    for order, axis in corridor_groups(nums, links, coords):
        counts = {n: base_tmcs[n] for n in order}
        trips = _paths(order, axis, counts)
        if not trips:
            for n in order:
                futures[n] = {}; meta[n] = {"flags": [{"code": "OD-NO-TRIPS", "message": "no counted movements."}]}
            continue

        # 1. base trip table from the counts
        members, bands = {}, {}
        for k, (_, _, path) in enumerate(trips):
            for n, m in path:
                members.setdefault((n, m), []).append(k)
        b0 = 0.0      # counts are already balanced: fit them exactly; fuzziness is for the forecast targets
        for cid in members:
            c = float(counts[cid[0]].get(cid[1], 0) or 0)
            bands[cid] = (c * (1 - b0), c * (1 + b0))
        base_x, it0, w0 = fit([1.0] * len(trips), members, bands)
        base_flags = []
        if w0 >= 0.01:
            base_flags.append({"code": "OD-BASE-FIT", "message": (
                f"today's counts disagree between neighbouring intersections (up to {w0:.0%}), so no "
                "trip table matches them exactly — balance the existing counts first for a clean fit.")})

        # 2. future trip table
        tin, tout, flags = {}, {}, list(base_flags)
        for n in order:
            ti, to, fl = _targets(rr_by[n], counts[n], (manual_targets or {}).get(n))
            tin[n], tout[n] = ti, to
            flags += fl
        fm, fb = {}, {}
        # Fratar on the entry/exit totals
        for k, (o, d, _) in enumerate(trips):
            fm.setdefault(("O",) + o, []).append(k)
            fm.setdefault(("D",) + d, []).append(k)
        o_tot = {c: tin[c[1]].get(c[2], 0.0) for c in fm if c[0] == "O"}
        # destination (num, heading) leaves on the leg whose APPROACH is the opposite heading
        d_tot = {c: tout[c[1]].get(OPPOSITE[c[2]], 0.0) for c in fm if c[0] == "D"}
        so, sd = sum(o_tot.values()), sum(d_tot.values())
        if so > 0 and sd > 0 and abs(so - sd) > 0.01 * max(so, sd):
            flags.append({"code": "OD-ENDS-EVENED", "message": (
                f"trips entering ({so:.0f}) and leaving ({sd:.0f}) differed — both set to "
                f"{(so + sd) / 2:.0f}, keeping each end's share.")})
        t = 0.5 * (so + sd)
        for c, v in o_tot.items():
            fb[c] = (v * t / so,) * 2 if so else (0.0, 0.0)
        for c, v in d_tot.items():
            fb[c] = (v * t / sd,) * 2 if sd else (0.0, 0.0)
        fut_x, it1, w1 = fit(base_x, fm, fb)
        if method == "fuzzy":
            # Start from the Fratar table, then pull every leg (main street included)
            # to within ±band of its own forecast. A Fratar table already inside every
            # band is kept as is, so the band only matters where the legs disagree.
            fm, fb = {}, {}
            for k, (_, _, path) in enumerate(trips):
                for n, m in path:
                    a = m[:2]
                    fm.setdefault(("IN", n, a), []).append(k)
                    fm.setdefault(("OUT", n, TURN_EXIT[a][m[2]]), []).append(k)
            for c in fm:
                kind, n, h = c
                v = tin[n].get(h, 0.0) if kind == "IN" else tout[n].get(OPPOSITE[h], 0.0)
                fb[c] = (v * (1 - band), v * (1 + band))
            fut_x, it2, w1 = fit(fut_x, fm, fb)
            it1 += it2
        vol = _assign(trips, fut_x)
        if w1 >= TOL:
            flags.append({"code": "OD-NOT-CONVERGED", "message": (
                f"the future trip table is still {w1:.1%} off its targets — the targets disagree; "
                "check legs with very different growth.")})
        for n in order:
            fut = {m: vol.get(n, {}).get(m, 0.0) for m in counts[n] if not m.endswith("U")}
            for m, v in counts[n].items():
                if m.endswith("U"):
                    fut[m] = float(v or 0)        # U-turns pass through, as in Pass 1
            futures[n] = fut
            meta[n] = {"period": rr_by[n]["period"], "converged": w1 < TOL, "iterations": it1,
                       "residual": round(w1, 4), "flags": [f for f in flags]}
        summary.append({"intersections": order, "axis": axis, "trips": len(trips), "method": method,
                        "band_pct": round(band * 100 if method == "fuzzy" else 0, 1),
                        "base_fit_worst_pct": round(w0 * 100, 2), "future_fit_worst_pct": round(w1 * 100, 2),
                        "base_trips_total": round(sum(base_x)), "future_trips_total": round(sum(fut_x))})
    return futures, meta, summary
