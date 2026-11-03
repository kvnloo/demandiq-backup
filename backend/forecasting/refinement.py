"""
DemandIQ Forecasting — Module 5: NCHRP 255 link/approach refinement.

Refines model forecasts against observed counts at the LINK/approach level (not
turn level — that's Pass 1). For each matched leg, per period, it combines the
balanced base-year count with the model's base and future directional volumes to
produce a refined future approach total (inbound = row target) and exit total
(outbound = column target) for Pass 1.

PURE PYTHON — no geo deps (geo boundary is the ingest line). Operates on matcher
output, the two NetworkLayers, and the existing balanced TMCs.

Method policy (locked with Akshaey, driven by a verified data fact):
  SANDAG `*_flow` fields are PERIOD volumes (total_flow == Σ of the 10 period
  flows, exactly) while DemandIQ counts are PEAK-HOUR. So:
    - RATIO  F = C × (Mf / Mb)        — time-base-invariant; the operative method.
    - DIFF   F = C + K × (Mf − Mb)    — needs an engineer peak-hour factor K to be
                                        dimensionally valid; computed only when K
                                        is supplied, never silently wrong.
    - COMBINED = ½(ratio + diff)      — likewise gated on K.
  Default method is 'ratio'. Difference/Combined are exposed on drill-in once K
  is provided.

Count totals reuse balancer.DIRECTION_SPEC so the inbound (approach) and outbound
(exit) movement sets are single-sourced with the S6 balancer.
"""

from balancer import DIRECTION_SPEC

# ── Tuning knobs (revisit after the first real corridor pass) ─────────────────
# Flag when the model growth factor Mf/Mb exceeds this — usually a tiny Mb
# inflating the ratio rather than real growth.
REFINE_GROWTH_FACTOR_MAX = 4.0
# Flag when ratio and difference (K supplied) disagree by more than this fraction
# of the larger — the two methods telling different stories warrants a drill-in.
METHOD_DIVERGENCE_THRESHOLD = 0.5
# NCHRP 765 Table 6-3 p. 112 example limits on count ÷ model base (peak-hour terms).
NCHRP765_MIN_DIFF = 0.5
NCHRP765_MAX_RAT = 2.0
# Flag (not cap) yearly growth at/above this — Ohio §2.4 / VDOT review trigger.
HIGH_GROWTH_PER_YEAR = 0.03
# NCHRP 255 p. 54: base-year error doesn't carry across a big capacity change.
CAPACITY_CHANGE_FLAG = 0.25
# Horizon may run this many years past the count (user decision).
MAX_HORIZON_YEARS = 50

APPROACHES = ("NB", "SB", "EB", "WB")
# Approach direction → the direction traffic EXITS the same physical leg.
OPPOSITE = {"NB": "SB", "SB": "NB", "EB": "WB", "WB": "EB"}


def _sum(counts, movements):
    return sum(float(counts.get(m, 0) or 0) for m in movements)


def _interp(m_base, m_future, year_base, year_future, year):
    """Linear model volume at `year` (NCHRP 765 §9.2 p. 223; NCHRP 255 Ch. 7).
    Extrapolates past either model year the same way."""
    if year is None or year_future == year_base:
        return m_base if year is None or year == year_base else m_future
    return m_base + (m_future - m_base) * (year - year_base) / (year_future - year_base)


def _nchrp765_pick(C, Mb, Mf, K, ratio, difference):
    """NCHRP 765 Table 6-2 col. 12 (p. 110): choose by R = count / base assignment
    (peak-hour units, so K·Mb). MRATIO leans to DIFF when the model grows a lot.
    shortcut: the table's comparison symbols were lost in the PDF text; read as
    MR<1: R<1 ratio, R>2 diff, else average; MR>1: R<0.5 mratio, R>2 diff, else
    avg(mratio, diff) — confirm against the printed page if a reviewer asks."""
    R = C / (K * Mb)
    MR = Mf / Mb
    mratio = ratio if MR <= 1 else ((MR - 1) * difference + ratio) / MR
    lo = 1.0 if MR < 1 else NCHRP765_MIN_DIFF
    if R < lo:
        return (mratio if MR > 1 else ratio), ("ratio (NCHRP 765, weighted)" if MR > 1 else "ratio"), R
    if R > NCHRP765_MAX_RAT:
        return difference, "difference", R
    return 0.5 * (mratio + difference), "average of ratio and difference", R


def _refine_one(count_base, model_base, model_future, *,
                peak_hour_factor, method, growth_factor_max, years=None):
    """NCHRP 255/765 refinement for one directional total (inbound or outbound).

    years: optional (model_base_year, model_future_year, count_year, horizon_year).
    When given, the model volumes are first interpolated to the count and horizon
    years, so growth covers only count→horizon, not the model's own span.
    """
    C, Mb_raw, Mf_raw = float(count_base), float(model_base), float(model_future)
    flags = []
    Mb, Mf = Mb_raw, Mf_raw
    if years is not None:
        yb, yf, yc, yh = years
        Mb = _interp(Mb_raw, Mf_raw, yb, yf, yc)
        Mf = _interp(Mb_raw, Mf_raw, yb, yf, yh)
        for label, val in (("count-year", Mb), ("horizon-year", Mf)):
            if val < 0:   # a falling model trend run past its years can cross zero
                flags.append({"code": "REFINE-EXTRAPOLATED-BELOW-ZERO",
                              "message": f"{label} model volume extrapolates to {val:.0f} — set to 0."})
        Mb, Mf = max(Mb, 0.0), max(Mf, 0.0)
    K = peak_hour_factor
    out = {"count_base": C, "model_base_raw": Mb_raw, "model_future_raw": Mf_raw,
           "model_base": round(Mb, 2), "model_future": round(Mf, 2)}

    growth = (Mf / Mb) if Mb > 0 else None
    ratio = (C * growth) if growth is not None else None
    difference = C + K * (Mf - Mb) if K is not None else None
    combined = (0.5 * (ratio + difference) if ratio is not None else difference) if K is not None else None
    R = (C / (K * Mb)) if (K is not None and Mb > 0) else None

    if Mb <= 0:
        if K is not None and Mf > 0 and difference is not None and difference >= 0:
            # NCHRP 255 p. 54: a tiny/zero base makes the ratio explode — use difference.
            flags.append({"code": "REFINE-MODEL-ZERO-BASE",
                          "message": f"model base volume is 0 — used difference {difference:.0f} (NCHRP 255 p. 54)."})
            return {**out, "growth_factor": None, "count_model_ratio": None, "ratio": None,
                    "difference": difference, "combined": combined, "refined": difference,
                    "method": "difference", "status": "refined", "flags": flags,
                    **_growth_meta(C, difference, years, flags)}
        flags.append({
            "code": "REFINE-MODEL-ZERO-BASE",
            "message": (f"model base volume is 0 — ratio undefined; held at count {C:.0f}"
                        + ("" if K is not None else " (give a peak-hour factor to use the difference method)") + "."),
        })
        return {**out, "growth_factor": None, "count_model_ratio": None, "ratio": None,
                "difference": difference, "combined": combined, "refined": C,
                "method": "held-flat", "status": "held-flat", "flags": flags,
                **_growth_meta(C, C, years, [])}

    if method in ("difference", "combined", "nchrp765") and K is None:
        flags.append({"code": "REFINE-NO-K",
                      "message": f"{method} requested but no peak_hour_factor — fell back to ratio."})
        method = "ratio"
    if method == "difference":
        selected, sel_method = difference, "difference"
    elif method == "combined":
        selected, sel_method = combined, "combined"
    elif method == "nchrp765":
        selected, sel_method, _ = _nchrp765_pick(C, Mb, Mf, K, ratio, difference)
    else:
        selected, sel_method = ratio, "ratio"

    if growth > growth_factor_max:
        flags.append({
            "code": "REFINE-GROWTH-OUTLIER",
            "message": (f"model growth factor {growth:.1f}× exceeds "
                        f"{growth_factor_max:.1f}× — verify the model volumes."),
        })
    if R is not None and not (NCHRP765_MIN_DIFF <= R <= NCHRP765_MAX_RAT):
        flags.append({
            "code": "REFINE-MODEL-VS-COUNT",
            "message": (f"count is {R:.2f}× the model's base volume (peak-hour terms) — the model "
                        "may not know this road well (NCHRP 765 §6.1 Step 2); check before relying on it."),
        })
    if ratio is not None and difference is not None:
        denom = max(abs(ratio), abs(difference), 1.0)
        if abs(ratio - difference) / denom > METHOD_DIVERGENCE_THRESHOLD:
            flags.append({
                "code": "REFINE-METHOD-DIVERGENCE",
                "message": (f"ratio ({ratio:.0f}) and difference ({difference:.0f}) "
                            f"disagree by >{METHOD_DIVERGENCE_THRESHOLD:.0%} — drill in."),
            })

    refined = selected
    if refined < 0:
        # NCHRP 255 p. 50: a negative difference result → use the ratio result.
        flags.append({
            "code": "REFINE-NEGATIVE-TO-RATIO",
            "message": f"{sel_method} forecast {refined:.0f} < 0 — used ratio {ratio:.0f} (NCHRP 255 p. 50).",
        })
        refined, sel_method = ratio, "ratio"

    return {
        **out, "growth_factor": round(growth, 4),
        "count_model_ratio": round(R, 3) if R is not None else None, "ratio": ratio,
        "difference": difference, "combined": combined,
        "refined": refined, "method": sel_method, "status": "refined", "flags": flags,
        **_growth_meta(C, refined, years, flags),
    }


def _growth_meta(C, refined, years, flags):
    """Annual growth count→horizon. No floor: declines are allowed (the user's
    call — recent MPO forecasts do go negative), only noted."""
    if years is None or C <= 0 or refined <= 0:
        return {"annual_growth_pct": None}
    n = years[3] - years[2]
    if n <= 0:
        return {"annual_growth_pct": None}
    rate = (refined / C) ** (1 / n) - 1
    if rate < 0:
        flags.append({"code": "REFINE-DECLINE",
                      "message": f"model shows traffic falling {rate:.1%}/yr — kept as is; note the reason in the report."})
    elif rate >= HIGH_GROWTH_PER_YEAR:
        flags.append({"code": "REFINE-HIGH-GROWTH",
                      "message": f"growth {rate:.1%}/yr is high (3%/yr+ gets a second look in DOT practice)."})
    return {"annual_growth_pct": round(rate * 100, 2)}


def _absent_leg():
    return {"status": "absent", "link_id": None, "inbound": None,
            "outbound": None, "flags": []}


def _pending_leg(link_id, reason, message):
    return {"status": "pending", "reason": reason, "link_id": link_id,
            "inbound": None, "outbound": None,
            "flags": [{"code": "REFINE-PENDING", "message": message}]}


def refine_corridor(
    match_results,
    base_layer,
    horizon_layer,
    balanced_tmcs,
    period,
    *,
    peak_hour_factor=None,
    method="ratio",
    growth_factor_max=REFINE_GROWTH_FACTOR_MAX,
    count_year=None,
    horizon_year=None,
):
    """Refine every matched leg of every intersection for one period.

    Args:
        match_results: output of matcher.match_network_to_legs (matched on BASE).
        base_layer / horizon_layer: NetworkLayers (same link_id set, per ingest's
            overlap check).
        balanced_tmcs: {int_num: {movement: value}} — the existing balanced TMCs
            for THIS period (the Q5 handoff). Counts are peak-hour.
        period: a contract.Period selecting the model flow period (Period.AM/PM).
        peak_hour_factor: optional K (period→peak-hour). Enables difference/
            combined; ratio ignores it (growth factor is K-invariant).
        method: 'ratio' (default/operative) | 'difference' | 'combined' |
            'nchrp765' (picks per leg by count ÷ model; needs K).
        count_year / horizon_year: the analysis years. When given, model volumes
            are interpolated to them first (NCHRP 765 §9.2). Omitted → the model
            years are used as-is (legacy behaviour).

    Returns:
        list of per-intersection dicts:
          { intersection_num, period, snapped_node,
            approaches: { "NB": { status: bound|pending|absent, link_id,
                                  inbound: {...} | None,    # row target
                                  outbound: {...} | None,   # column target
                                  flags: [...] }, ... } }
        Each inbound/outbound dict carries count_base, model_base, model_future,
        growth_factor, ratio, difference, combined, refined, method, status.
    """
    years = None
    if count_year is not None and horizon_year is not None:
        count_year, horizon_year = int(count_year), int(horizon_year)
        if not count_year <= horizon_year <= count_year + MAX_HORIZON_YEARS:
            raise ValueError(f"horizon year {horizon_year} must be between the count year "
                             f"{count_year} and {count_year + MAX_HORIZON_YEARS}.")
        years = (base_layer.scenario_year, horizon_layer.scenario_year, count_year, horizon_year)
    base_idx = {lk.link_id: lk for lk in base_layer.links}
    horizon_idx = {lk.link_id: lk for lk in horizon_layer.links}

    out = []
    for mr in match_results:
        num = mr["intersection_num"]
        node = mr["snapped_node"]
        counts = balanced_tmcs.get(num, {}) or {}
        approaches = {}

        for d in APPROACHES:
            leg = mr["legs"].get(d) or {"status": "unmatched"}
            mstatus = leg.get("status")

            if mstatus == "absent":
                approaches[d] = _absent_leg()
                continue
            if mstatus in ("unmatched", "ambiguous"):
                approaches[d] = _pending_leg(
                    leg.get("link_id"), mstatus,
                    f"leg {d} is {mstatus} in the matcher — confirm the link before refining.")
                continue

            link_id = leg["link_id"]
            base_link = base_idx.get(link_id)
            horizon_link = horizon_idx.get(link_id)
            if base_link is None or horizon_link is None:
                approaches[d] = _pending_leg(
                    link_id, "no-horizon-twin",
                    f"link {link_id} has no twin in the {'base' if base_link is None else 'horizon'} "
                    "network — cannot read model growth.")
                continue

            # Inbound (approach / row target) and outbound (exit / column target).
            try:
                mb_in = base_link.inbound_to(node).get(period, 0.0)
                mf_in = horizon_link.inbound_to(node).get(period, 0.0)
                mb_out = base_link.outbound_from(node).get(period, 0.0)
                mf_out = horizon_link.outbound_from(node).get(period, 0.0)
            except ValueError:
                approaches[d] = _pending_leg(
                    link_id, "node-mismatch",
                    f"link {link_id} is not incident to node {node} in both networks "
                    "— ID likely reused across model series.")
                continue

            c_in = _sum(counts, DIRECTION_SPEC[d]["approach"])
            out_dir = OPPOSITE[d]
            # Exclude the U-turn (outflow_u): the Pass 1 IPF matrix skips U-turn
            # cells (they pass through unchanged per the S6 convention), so a
            # column can only sum to the non-U exit movements. Matches
            # balancer.intersection_targets, which also omits outflow_u — keeping
            # the refined column target reachable by the IPF.
            c_out = _sum(counts, DIRECTION_SPEC[out_dir]["outflow"])

            kw = dict(peak_hour_factor=peak_hour_factor, method=method,
                      growth_factor_max=growth_factor_max, years=years)
            leg_flags = []
            lb, lh = base_link.lanes or 0, horizon_link.lanes or 0
            if lb > 0 and abs(lh - lb) / lb > CAPACITY_CHANGE_FLAG:
                leg_flags.append({
                    "code": "REFINE-CAPACITY-CHANGE",
                    "message": (f"model lanes change {lb}→{lh} — the base-year count/model "
                                "relationship may not hold (NCHRP 255 p. 54); review this leg."),
                })
            if years is not None and (years[3] > max(years[0], years[1]) + 5 or years[2] < min(years[0], years[1]) - 5):
                leg_flags.append({
                    "code": "REFINE-EXTRAPOLATED",
                    "message": (f"years {years[2]}→{years[3]} reach more than 5 years past the model's "
                                f"{years[0]}–{years[1]} runs — volumes are extrapolated (ODOT APM §6.2)."),
                })
            approaches[d] = {
                "status": "bound", "link_id": link_id,
                "inbound": _refine_one(c_in, mb_in, mf_in, **kw),
                "outbound": _refine_one(c_out, mb_out, mf_out, **kw),
                "flags": leg_flags,
            }

        out.append({
            "intersection_num": num,
            "period": period.value,
            "snapped_node": node,
            "approaches": approaches,
        })

    return out
