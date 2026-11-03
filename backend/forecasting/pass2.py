"""
DemandIQ Forecasting — Module 7: Pass 2 (corridor balancer).

The future-conditions counterpart to S6. Pass 1 produced each intersection's
future turning movements independently; Pass 2 enforces LINK CONSERVATION across
the corridor — the upstream intersection's outflow equals the downstream's
approach on every link — and rounds to multiples of 5.

PURE REUSE. It feeds Pass 1's futures straight into the existing, battle-tested
S6 engine `balancer.balance_nchrp` — the SAME algorithm S6 runs on observed
counts, now applied to the forecast. The only new code here is assembling /
validating the corridor link list and threading both periods through; there is
no new balancing logic.
"""

from balancer import balance_nchrp

VALID_DIRECTIONS = ("EB/WB", "NB/SB")


def _normalize_links(links):
    """Validate + normalize corridor links to balance_nchrp's shape. Fail loud on
    a bad direction — resolve_link_volumes silently skips a link whose axis isn't
    the corridor form, which would report a phantom '0 links conserve' instead of
    an error."""
    out = []
    for L in links:
        direction = L.get("direction")
        if direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"link {L.get('fromNum')}→{L.get('toNum')} has invalid direction "
                f"{direction!r} — expected one of {list(VALID_DIRECTIONS)}.")
        out.append({
            "fromNum": int(L["fromNum"]),
            "toNum": int(L["toNum"]),
            "direction": direction,
            "mode": L.get("mode", "Full"),
        })
    return out


def balance_future(futures, links, *, coords=None, method="larger", tol=0.5, max_iter=500):
    """Pass 2 for one period: conserve Pass-1 future TMCs across the corridor.

    Args:
        futures: {int_num: {movement: value}} — Pass 1 output for one period.
        links: corridor links [{fromNum, toNum, direction, mode}] (the project's
            S2 links; mode='None' skips a link from balancing — this is where an
            off-network/cordon leg is excluded, per the Q3 decision).
        coords: {int_num: {lat, lng}} for geographic leg ordering.
        method: 'larger' (default) | 'average' — the S5 link-resolution setting.

    Returns:
        balance_nchrp's result dict: balanced future TMCs (rounded per NCHRP 765 Table 4-8;
        the 5-vph values stay in balanced_unrounded),
        resolved_links, link_conservation, iterations, converged, final_residual,
        qc_flags, movements_review, method.
    """
    out = balance_nchrp(futures, _normalize_links(links), method=method,
                        coords=coords, tol=tol, max_iter=max_iter)
    # Report forecasts at NCHRP 765 precision; keep the 5-vph values for the math.
    out["balanced_unrounded"] = out["balanced"]
    out["balanced"] = {n: {m: round_forecast(v) for m, v in mv.items()}
                       for n, mv in out["balanced"].items()}
    return out


def round_forecast(v):
    """NCHRP 765 Table 4-8 (p. 83): <100 → 10, <1,000 → 50, <10,000 → 100,
    <100,000 → 500, else 1,000. Half rounds up."""
    v = float(v or 0)
    step = 10 if v < 100 else 50 if v < 1000 else 100 if v < 10000 else 500 if v < 100000 else 1000
    return int((v + step / 2) // step * step)


def balance_future_corridor(futures_by_period, links, *, coords=None, method="larger",
                            tol=0.5, max_iter=500):
    """Pass 2 across periods. futures_by_period: {'AM': {...}, 'PM': {...}} →
    {'AM': result, 'PM': result}."""
    return {
        period: balance_future(futures, links, coords=coords, method=method,
                               tol=tol, max_iter=max_iter)
        for period, futures in futures_by_period.items()
    }
