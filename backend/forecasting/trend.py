"""
DemandIQ Forecasting — growth without a travel model.

  - Historical trend (NCHRP 765 §10.1; FDOT Handbook §5.4; ODOT APM §6.5): fit
    past counts with a straight line and with an exponential curve, keep the
    better fit (higher R²), and grow every approach by it. ODOT discards fits with
    R² below 0.75, so those are flagged.
  - Growth rate the engineer enters: % per year, linear (simple) growth.

Declines are allowed (no floor — user decision); they are flagged, not clamped.
The result has the same shape as refine_corridor's output, so Pass 1 / Pass 2 or
the trip-table methods run unchanged.
"""

import math

from balancer import DIRECTION_SPEC

APPROACHES = ("NB", "SB", "EB", "WB")
OPPOSITE = {"NB": "SB", "SB": "NB", "EB": "WB", "WB": "EB"}
MIN_R2 = 0.75


def _r2(ys, fit):
    m = sum(ys) / len(ys)
    tot = sum((y - m) ** 2 for y in ys)
    return 1.0 if tot == 0 else 1 - sum((y - f) ** 2 for y, f in zip(ys, fit)) / tot


def _line(xs, ys):
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def fit_trend(points, base_year):
    """points: [{year, volume}] (≥ 3 distinct years, volumes > 0).
    Returns {kind, annual_rate, r2, n, years, factor(year)->growth from base_year}."""
    pts = sorted({int(p["year"]): float(p["volume"]) for p in points}.items())
    if len(pts) < 3:
        raise ValueError("historical trend needs counts from at least 3 different years.")
    if any(v <= 0 for _, v in pts):
        raise ValueError("historical counts must be positive.")
    xs, ys = [float(y) for y, _ in pts], [v for _, v in pts]
    a, b = _line(xs, ys)
    lin_r2 = _r2(ys, [a + b * x for x in xs])
    la, lb = _line(xs, [math.log(y) for y in ys])
    exp_r2 = _r2(ys, [math.exp(la + lb * x) for x in xs])
    at_base = a + b * base_year
    if exp_r2 > lin_r2 or at_base <= 0:
        rate, kind, r2 = math.exp(lb) - 1, "exponential", exp_r2
        factor = lambda y: (1 + rate) ** (y - base_year)          # noqa: E731
    else:
        rate, kind, r2 = b / at_base, "linear", lin_r2
        factor = lambda y: 1 + rate * (y - base_year)             # noqa: E731
    return {"kind": kind, "annual_rate": rate, "r2": r2, "n": len(pts),
            "years": [int(xs[0]), int(xs[-1])], "factor": factor}


def uniform_growth(balanced_tmcs, period, factor, *, label, annual_rate, notes=()):
    """refine_corridor-shaped result where every approach and exit total grows
    by the same `factor` (count → horizon)."""
    flags = [{"code": "GROWTH-NOTE", "message": m} for m in notes]
    if annual_rate < 0:
        flags.append({"code": "REFINE-DECLINE",
                      "message": f"traffic falls {annual_rate:.1%}/yr — kept as is; note the reason in the report."})
    elif annual_rate >= 0.03:
        flags.append({"code": "REFINE-HIGH-GROWTH",
                      "message": f"growth {annual_rate:.1%}/yr is high (3%/yr+ gets a second look in DOT practice)."})
    factor = max(factor, 0.0)
    out = []
    for num, counts in balanced_tmcs.items():
        approaches = {}
        for d in APPROACHES:
            c_in = sum(float(counts.get(m, 0) or 0) for m in DIRECTION_SPEC[d]["approach"])
            c_out = sum(float(counts.get(m, 0) or 0) for m in DIRECTION_SPEC[OPPOSITE[d]]["outflow"])
            if c_in == 0 and c_out == 0:
                approaches[d] = {"status": "absent", "link_id": None, "inbound": None, "outbound": None, "flags": []}
                continue

            def one(c):
                return {"count_base": c, "refined": c * factor, "growth_factor": round(factor, 4),
                        "method": label, "status": "refined", "flags": list(flags),
                        "annual_growth_pct": round(annual_rate * 100, 2)}
            approaches[d] = {"status": "bound", "link_id": None,
                             "inbound": one(c_in), "outbound": one(c_out), "flags": []}
        out.append({"intersection_num": num, "period": period.value if hasattr(period, "value") else period,
                    "snapped_node": None, "approaches": approaches})
    return out
