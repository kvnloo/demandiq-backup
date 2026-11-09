"""Forecast Oracle — the textbook answer, re-derived from the public guidance.

Not imported from backend/forecasting: the Examiner checks the tool against an
independent implementation of the same published formulas.

  interpolation   NCHRP 765 §9.2 — model volumes straight-line to the count
                  and horizon years (below 0 → 0)
  ratio           NCHRP 255 p. 49:  F = C × M_H / M_C
  difference      NCHRP 255 p. 50:  F = C + K × (M_H − M_C)   (K = peak hour ÷ period)
  average         NCHRP 255:        F = (ratio + difference) / 2
  in = out        NCHRP 255 p. 133: scale approach and exit totals to their mean
  Furness         NCHRP 255 Ch. 8:  grow today's turns to the leg totals by
                  alternately scaling rows (approaches) and columns (exits)
  rounding        NCHRP 765 Table 4-8 (p. 83): 10 below 100, 50 to 999, 100 to
                  9,999, 500 to 99,999, else 1,000
Stdlib only.
"""

from .nchrp_math import DIRECTION_SPEC
from .forecast_puzzler import COUNT_YEAR, HORIZON, leg_in, leg_out

DIRS = ('NB', 'SB', 'EB', 'WB')
# movement → (approach leg, exit leg); exit leg named by the approach that uses it
_EXIT = {}
for _d in DIRS:
    for _m in DIRECTION_SPEC[{'NB': 'SB', 'SB': 'NB', 'EB': 'WB', 'WB': 'EB'}[_d]]['outflow']:
        _EXIT[_m] = _d


def interp(mb, mf, yb, yf, y):
    return max(0.0, mb + (mf - mb) * (y - yb) / (yf - yb))


def link_target(C, mb, mf, years, method, K):
    """The guideline answer for one directional leg total, or None when the
    method isn't one the oracle re-derives."""
    yb, yf = years
    Mc, Mh = interp(mb, mf, yb, yf, COUNT_YEAR), interp(mb, mf, yb, yf, HORIZON)
    if Mc <= 0:
        return None                                    # zero-base edge rules: not graded here
    ratio = C * Mh / Mc
    diff = C + K * (Mh - Mc)
    if method == 'ratio':
        return ratio
    if method == 'difference':
        return diff if diff >= 0 else ratio            # NCHRP 255 p. 50: negative → ratio
    if method == 'combined':
        return 0.5 * (ratio + diff)
    return None


def leg_targets(world, counts):
    """{(int, leg): (inbound, outbound)} the guideline gives, from the model."""
    flows = {}
    for which in ('base', 'horizon'):
        for l in world['layers'][which]['links']:
            flows.setdefault(l['link_id'], {})[which] = l
    out = {}
    for m in world['matches']:
        i = m['intersection_num']
        for d, leg in m['legs'].items():
            L = flows[leg['link_id']]
            toward = L['base']['to_node'] == i               # ab heads toward this intersection
            def get(which, inbound):
                f = L[which]['flow']
                return f['ab' if inbound == toward else 'ba']['AM']
            vals = []
            for inbound in (True, False):
                C = leg_in(counts, i, d) if inbound else leg_out(counts, i, d)
                vals.append(link_target(C, get('base', inbound), get('horizon', inbound),
                                        world['model_years'], world['link_method'], world['K']))
            out[(i, d)] = tuple(vals)
    return out


def furness(base, rows, cols, iters=500, tol=1e-6):
    """One intersection: base {movement: veh}, rows {approach: target},
    cols {exit leg: target}. Returns {movement: veh}."""
    t_in, t_out = sum(rows.values()), sum(cols.values())
    if t_in > 0 and t_out > 0 and abs(t_in - t_out) > 0.01 * max(t_in, t_out):
        tot = 0.5 * (t_in + t_out)
        rows = {k: v * tot / t_in for k, v in rows.items()}
        cols = {k: v * tot / t_out for k, v in cols.items()}
    x = {m: float(v) for m, v in base.items() if m in _EXIT}
    for _ in range(iters):
        worst = 0.0
        for d in DIRS:                                    # rows: approaches
            ms = DIRECTION_SPEC[d]['approach']
            s = sum(x[m] for m in ms)
            if s > 0:
                for m in ms:
                    x[m] *= rows[d] / s
        for d in DIRS:                                    # columns: exits
            ms = [m for m in x if _EXIT[m] == d]
            s = sum(x[m] for m in ms)
            if s > 0:
                f = cols[d] / s
                worst = max(worst, abs(f - 1))
                for m in ms:
                    x[m] *= f
        if worst < tol:
            break
    return x


def round_forecast(v):
    v = float(v)
    step = 10 if v < 100 else 50 if v < 1000 else 100 if v < 10000 else 500 if v < 100000 else 1000
    return step * int(v / step + 0.5)


def allowed_rounded(v):
    """True if v is a value Table 4-8 rounding can produce."""
    return float(v) == round_forecast(v)
