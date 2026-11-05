"""
DemandIQ Forecasting — HTTP API (two endpoints).

An APIRouter, mounted by app.py under /forecast. Kept separate from app.py so it
carries no dependency on the app's ANTHROPIC_API_KEY / auth and can be tested in
isolation on a bare FastAPI() with TestClient.

Endpoints:
  POST /forecast/ingest-match  — the ONLY geo-dependent route. Ingests the SANDAG
                                 network(s) around the project intersections and
                                 matches the base layer to the legs. Returns the
                                 filtered layers (KB-scale) + match results for
                                 the review UI. Returns 503 if the geo deps aren't
                                 installed (lean image); the rest of the app works.
  POST /forecast/run           — pure Python. refine → Pass 1 → Pass 2 → future TMCs.

Validation mirrors app.py's style: dict payloads + HTTPException. Round-tripped
layers are validated through the NetworkLayer Pydantic model.
"""

import math

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from forecasting.contract import NetworkLayer, Period
from forecasting.adapters.mtc import available_years as mtc_years
from forecasting.ingest import ADAPTERS, ingest_networks, sandag_years
from forecasting.matcher import match_network_to_legs
from forecasting.refinement import refine_corridor
from forecasting.pass1 import forecast_corridor
from forecasting.pass2 import balance_future, round_forecast
from forecasting.od import forecast_trips
from forecasting.trend import fit_trend, uniform_growth
from forecasting.vmt import corridor_vmt

router = APIRouter()

# Link step without a travel model; turn/corridor step choices.
NO_MODEL_METHODS = {"trend", "rate"}
LINK_METHODS = {"ratio", "difference", "combined", "weighted", "modified_average", "nchrp765", "odot"}
TURN_METHODS = {"furness", "fratar", "fuzzy"}


def _bad(msg):
    raise HTTPException(status_code=400, detail=msg)


def _intx_num(k, what):
    try:
        if float(k) == int(float(k)):
            return int(float(k))
    except (TypeError, ValueError):
        pass
    _bad(f"{what}: {k!r} is not an intersection number")


def _volume(v, what):
    """A finite, non-negative number (vehicles) — the trust boundary for counts/targets."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        _bad(f"{what} must be a number of vehicles")
    if not math.isfinite(f) or f < 0:
        _bad(f"{what} must be zero or more vehicles")
    return f


def _by_intx(d, what):
    """{intersection number: object} from a JSON object keyed by number."""
    if d in (None, ""):
        return {}
    if not isinstance(d, dict):
        _bad(f"{what} must be an object keyed by intersection number")
    out = {}
    for k, v in d.items():
        n = _intx_num(k, what)
        if not isinstance(v, dict):
            _bad(f"{what} for intersection {n} must be an object")
        out[n] = v
    return out


def _str_keys(d):
    return {str(k): v for k, v in (d or {}).items()}


def _serialize_pass2(p2):
    """JSON-safe view of balance_nchrp's result (resolved_links is tuple-keyed)."""
    return {
        "balanced": _str_keys(p2.get("balanced", {})),
        "balanced_unrounded": _str_keys(p2.get("balanced_unrounded", {})),
        "link_conservation": p2.get("link_conservation", []),
        "iterations": _str_keys(p2.get("iterations", {})),
        "converged": _str_keys(p2.get("converged", {})),
        "final_residual": _str_keys(p2.get("final_residual", {})),
        "qc_flags": p2.get("qc_flags", []),
        "movements_review": p2.get("movements_review", []),
        "method": p2.get("method"),
        "resolved_links": [
            {"fromNum": u, "toNum": d, "direction": dirn, "volume": v}
            for (u, d, dirn), v in p2.get("resolved_links", {}).items()
        ],
    }


@router.post("/ingest-match")
def ingest_match(payload: dict):
    """Ingest the SANDAG network(s) around the project intersections and match the
    base layer to the legs. Geo-dependent — 503 if geopandas et al. are absent."""
    intersections = payload.get("intersections")
    if not isinstance(intersections, list) or not intersections:
        raise HTTPException(status_code=400, detail="intersections must be a non-empty list")

    networks = payload.get("networks")
    if not isinstance(networks, list) or not networks:
        raise HTTPException(status_code=400, detail="networks must be a non-empty list")

    region = payload.get("region", "SANDAG")
    if region not in ADAPTERS:
        raise HTTPException(status_code=400, detail=f"unknown region {region!r} (available: {sorted(ADAPTERS)})")

    for it in intersections:
        if not isinstance(it, dict):
            _bad("each intersection must be an object")
        if it.get("num") is None:
            _bad("every intersection needs its number (num)")
        _intx_num(it["num"], "intersection")

    # The model file is picked server-side from (region, model year) — clients
    # never send a path (any `path` is dropped), so nothing can escape data/.
    # Cheap checks run here, before the slow shapefile read.
    norm_networks, roles = [], set()
    for n in networks:
        if not isinstance(n, dict):
            raise HTTPException(status_code=400, detail="each network must be an object")
        role = n.get("role")
        if role not in ("base", "horizon"):
            _bad("each network needs a role: 'base' or 'horizon'")
        if role in roles:
            _bad(f"two networks are tagged {role!r} — use one base and one horizon")
        roles.add(role)
        try:
            year = int(n.get("scenario_year"))
        except (TypeError, ValueError):
            _bad(f"the {role} network needs a model year")
        norm_networks.append({"role": role, "scenario_year": year})
    if "base" not in roles:
        _bad("a network with role 'base' is required to match")

    try:
        radius = float(payload.get("filter_radius_ft", 300))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="filter_radius_ft must be numeric")

    try:
        layers = ingest_networks(intersections, norm_networks, region=region, filter_radius_ft=radius)
    except ModuleNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=("Forecasting isn't set up on this server yet (its geo dependencies "
                    "aren't installed) — ask the DemandIQ admin."),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    matches = match_network_to_legs(intersections, layers["base"])
    return {
        "layers": {role: lyr.model_dump(mode="json") for role, lyr in layers.items()},
        "matches": matches,
    }


@router.get("/model-years")
def model_years():
    """Model years available on this server, per file-backed region."""
    return {"SANDAG": sandag_years(), "MTC": mtc_years()}


@router.post("/run")
def run_forecast(payload: dict):
    """refine → Pass 1 → Pass 2 for each period. Pure Python; no geo deps."""
    refine_method = payload.get("refine_method", "ratio")
    turn_method = payload.get("turn_method", "furness")
    if turn_method not in TURN_METHODS:
        raise HTTPException(status_code=400, detail=f"turn_method must be one of {sorted(TURN_METHODS)}")
    try:
        band = float(payload.get("fuzzy_band_pct", 5)) / 100
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="fuzzy_band_pct must be numeric")
    if refine_method not in LINK_METHODS | NO_MODEL_METHODS:
        raise HTTPException(status_code=400, detail=(
            f"refine_method must be one of {sorted(LINK_METHODS | NO_MODEL_METHODS)}"))
    uses_model = refine_method not in NO_MODEL_METHODS

    layers_in = payload.get("layers") or {}
    base_layer = horizon_layer = None
    if uses_model:
        if "base" not in layers_in or "horizon" not in layers_in:
            raise HTTPException(
                status_code=400,
                detail="layers must include both 'base' and 'horizon' "
                       "(forecasting needs two model years).")
        try:
            base_layer = NetworkLayer.model_validate(layers_in["base"])
            horizon_layer = NetworkLayer.model_validate(layers_in["horizon"])
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=f"invalid layer payload: {e}")

    matches = payload.get("matches") or []
    links = payload.get("links") or []
    if not isinstance(matches, list) or not all(isinstance(m, dict) for m in matches):
        _bad("matches must be a list of intersection matches")
    if not isinstance(links, list) or not all(isinstance(L, dict) for L in links):
        _bad("links must be a list of {fromNum, toNum, direction}")
    for L in links:
        L["fromNum"] = _intx_num(L.get("fromNum"), "link fromNum")
        L["toNum"] = _intx_num(L.get("toNum"), "link toNum")
    method = payload.get("method", "larger")
    peak_hour_factor = payload.get("peak_hour_factor")
    if peak_hour_factor not in (None, ""):
        try:
            peak_hour_factor = float(peak_hour_factor)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="peak_hour_factor must be a number")
        # K = peak hour ÷ model period, so a share: 0 < K ≤ 1
        if not (math.isfinite(peak_hour_factor) and 0 < peak_hour_factor <= 1):
            raise HTTPException(status_code=400, detail="peak_hour_factor must be between 0 and 1 (peak hour ÷ model period)")
    else:
        peak_hour_factor = None
    coords = {}
    for n, c in _by_intx(payload.get("coords"), "coords").items():
        try:
            if c.get("lat") is not None and c.get("lng") is not None:
                coords[n] = {"lat": float(c["lat"]), "lng": float(c["lng"])}
        except (TypeError, ValueError):
            _bad(f"coords for intersection {n} must be numbers (lat, lng)")
    balanced = payload.get("balanced_tmcs") or {}
    if not isinstance(balanced, dict):
        _bad("balanced_tmcs must be an object keyed by period (AM/PM)")
    periods = payload.get("periods") or ["AM", "PM"]
    if not isinstance(periods, list):
        _bad("periods must be a list, e.g. ['AM', 'PM']")
    # Analysis years (count → horizon). Both given → model volumes are interpolated
    # to them; either missing → the model years are used as-is.
    years = {}
    for key in ("count_year", "horizon_year"):
        v = payload.get(key)
        if v not in (None, ""):
            try:
                years[key] = int(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{key} must be a year")

    if len(years) == 1:
        raise HTTPException(status_code=400, detail="give both count_year and horizon_year, or neither")

    growth = None                      # no-model link step: (factor, label, annual rate, notes)
    if not uses_model:
        if len(years) < 2:
            raise HTTPException(status_code=400, detail="count_year and horizon_year are required")
        n = years["horizon_year"] - years["count_year"]
        if not 0 <= n <= 50:
            raise HTTPException(status_code=400, detail="horizon year must be 0–50 years after the count year")
        try:
            if refine_method == "trend":
                pts = payload.get("trend_points") or []
                if not all(isinstance(p, dict) and "year" in p and "volume" in p for p in pts):
                    raise ValueError("each past count needs a year and a volume")
                t = fit_trend(pts, years["count_year"])
                notes = [f"{t['kind']} trend from {t['n']} counts {t['years'][0]}–{t['years'][1]}: "
                         f"{t['annual_rate']:+.2%}/yr, R² {t['r2']:.2f}"]
                if t["r2"] < 0.75:
                    notes.append(f"trend fit is weak (R² {t['r2']:.2f} < 0.75 — ODOT APM §6.5 would not use it)")
                growth = (t["factor"](years["horizon_year"]), "historical trend", t["annual_rate"], notes)
            else:
                if payload.get("growth_rate_pct") in (None, ""):
                    raise ValueError("enter a growth rate (% per year; negative for a decline)")
                try:
                    rate = float(payload.get("growth_rate_pct")) / 100
                except (TypeError, ValueError):
                    rate = math.nan
                if not math.isfinite(rate):
                    raise ValueError("growth rate must be a number — % per year, e.g. 1.5 or -0.5")
                growth = (1 + rate * n, "growth rate entered", rate, [f"{rate:+.2%}/yr entered by the engineer"])
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e) or "growth_rate_pct must be numeric")
        if growth[0] <= 0:
            # Straight-line decline that reaches zero: say so rather than forecast 0 vph.
            how = "a straight-line trend of" if refine_method == "trend" else ""
            raise HTTPException(status_code=400, detail=(
                f"{how} {growth[2]:+.1%}/yr for {n} years removes all traffic by "
                f"{years['horizon_year']} — use a smaller rate, a nearer horizon, or a trend.").strip())

    manual_targets = {}
    for n, legs in _by_intx(payload.get("manual_targets"), "manual_targets").items():
        manual_targets[n] = {}
        for d, t in legs.items():
            if d not in ("NB", "SB", "EB", "WB") or not isinstance(t, dict):
                _bad(f"manual_targets for intersection {n}: use {{NB|SB|EB|WB: {{inbound, outbound}}}}")
            manual_targets[n][d] = {k: _volume(t.get(k), f"manual target Int {n} {d} {k}")
                                    for k in ("inbound", "outbound")}
    manual_targets = manual_targets or None

    # VMT peak→daily expansion factor — SEPARATE from the refinement K. Optional;
    # only used when the model carries no daily flows (else daily is data-derived).
    vmt_factor = payload.get("vmt_expansion_factor")
    if vmt_factor is not None:
        try:
            vmt_factor = float(vmt_factor)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="vmt_expansion_factor must be numeric")

    # Scenario demand-reduction factor — the engineer's mitigation assumption.
    # It scales the VMT figures only (turning movements stay as forecast); the
    # tool never generates one. Range is
    # validated inside corridor_vmt (0 < f < 1 → ValueError → 400 below).
    scenario_factor = payload.get("scenario_reduction_factor")
    if scenario_factor is not None:
        try:
            scenario_factor = float(scenario_factor)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="scenario_reduction_factor must be numeric")

    out = {}
    for p in periods:
        if p not in ("AM", "PM"):
            raise HTTPException(status_code=400, detail=f"period must be 'AM' or 'PM', got {p!r}")
        period = Period[p]
        tmcs = {n: {m: _volume(v, f"{p} count Int {n} {m}") for m, v in mv.items()}
                for n, mv in _by_intx(balanced.get(p), f"balanced_tmcs.{p}").items()}
        if not any(v > 0 for mv in tmcs.values() for v in mv.values()):
            _bad(f"no counted volumes for the {p} period — balance the existing counts first.")
        for L in links:
            for end in ("fromNum", "toNum"):
                if L[end] not in tmcs:
                    _bad(f"link {L['fromNum']}→{L['toNum']} points at intersection {L[end]}, "
                         f"which has no {p} counts.")
        try:
            if uses_model:
                rr = refine_corridor(matches, base_layer, horizon_layer, tmcs, period,
                                     peak_hour_factor=peak_hour_factor, method=refine_method,
                                     **years)
            else:
                rr = uniform_growth(tmcs, period, growth[0], label=growth[1],
                                    annual_rate=growth[2], notes=growth[3])
            trips = None
            if turn_method == "furness":
                futures, p1_meta = forecast_corridor(rr, tmcs, manual_targets=manual_targets)
                p2 = balance_future(futures, links, coords=coords, method=method)
            else:
                futures, p1_meta, trips = forecast_trips(rr, tmcs, links, coords,
                                                         method=turn_method, band=band,
                                                         manual_targets=manual_targets)
                # trips already agree across the corridor — only rounding remains
                p2 = {"balanced_unrounded": futures, "method": turn_method,
                      "converged": {n: m.get("converged", True) for n, m in p1_meta.items()},
                      "qc_flags": [],             # trip-table notes ride on pass1.meta flags
                      "balanced": {n: {m: round_forecast(v) for m, v in mv.items()} for n, mv in futures.items()}}
            vmt = (corridor_vmt(links, coords, matches, base_layer, horizon_layer,
                                tmcs, p2["balanced_unrounded"], rr, period,
                                expansion_factor=vmt_factor, scenario_factor=scenario_factor,
                                peak_hour_factor=peak_hour_factor)
                   if uses_model else None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        out[p] = {
            "refinement": rr,
            "pass1": {"futures": _str_keys(futures), "meta": _str_keys(p1_meta)},
            "pass2": _serialize_pass2(p2),
            "trips": trips,
            "vmt": vmt,
        }
    return out
