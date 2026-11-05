"""
DemandIQ Web App — Phase A
==========================
Local FastAPI wrapper around count_agent.extract().

Run locally:
    cd ~/Desktop/demandiq
    python3.12 app.py
    # then open http://127.0.0.1:8000

On Render: Dockerfile sets up Poppler; set ANTHROPIC_API_KEY and
APP_PASSWORD in the Environment tab.
"""

import os
import io
import re
import json
import math
import logging
import base64
import secrets
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill

from count_agent import extract, MOVEMENTS, MODEL as COUNT_MODEL
import capture
from balancer import balance_nchrp, fractional_volume_flags, source_outlier_flags, _orient
from line_diagram import (
    render_single_diagram,
    render_diagram_grid,
    render_corridor_diagram,
    corridor_cols,
    count_segment_labels,
    VIEW_MODES,
    FIG_INCHES,
    DPI,
)
from excel_export import build_workbook, strip_illegal_chars, workbook_bytes
import jev
from forecasting.api import router as forecasting_router

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. On Render: open the service's Environment "
        "tab, add ANTHROPIC_API_KEY, and redeploy. Locally: export it in your shell "
        "(e.g. ~/.bash_profile) and restart."
    )

# If APP_PASSWORD is set, all routes require basic auth.
# If unset (local dev), no auth — app works exactly as before.
APP_PASSWORD = os.getenv("APP_PASSWORD")

ALLOWED_EXT = {".xlsx", ".xls", ".pdf", ".csv"}

# Request limits. A count project is a few hundred kB of JSON; uploads are
# vendor count sheets (a few MB each).
MAX_BODY_BYTES = 20 * 1024 * 1024
MAX_EXTRACT_FILES = 50
MAX_EXTRACT_FILE_BYTES = 25 * 1024 * 1024
# shortcut: /extract total capped at 200 MB rather than 50 x 25 MB; raise if users batch more.
MAX_EXTRACT_BODY_BYTES = 200 * 1024 * 1024
MAX_DIAGRAM_CELLS = 200
MAX_VOLUME = 20000   # veh/h for one movement; anything larger is a typo

log = logging.getLogger("demandiq")

# With a password set the app is a private deployment: don't publish the API
# schema (/docs, /redoc, /openapi.json) to unauthenticated visitors.
_docs = {} if not APP_PASSWORD else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="DemandIQ", version="0.1.0", **_docs)


class BodySizeLimitMiddleware:
    """Reject request bodies over the cap with 413 before a route reads them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        limit = MAX_EXTRACT_BODY_BYTES if path.rstrip("/").endswith("/extract") else MAX_BODY_BYTES
        headers = dict(scope.get("headers") or [])
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > limit:
            return await self._reject(send, limit)

        seen = 0
        started = False

        async def counting_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise _BodyTooLarge()
            return msg

        async def tracking_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(send, limit)

    @staticmethod
    async def _reject(send, limit):
        body = json.dumps({"detail": f"The upload is too large (limit {limit // (1024 * 1024)} MB)."}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass


# ── Input validation helpers (trust boundary) ─────────────────────────────────
def _bad(msg):
    raise HTTPException(status_code=400, detail=msg)


def _is_num(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _as_int(v):
    """Whole number from an int, an integral float or a digit string, else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _show(v):
    return str(v)[:40]


def _check_volume(v, where):
    """None (not counted) or a finite number 0..MAX_VOLUME, else 400."""
    if v is None:
        return None
    if not _is_num(v) or not 0 <= v <= MAX_VOLUME:
        _bad(f"{where}: volume must be a number from 0 to {MAX_VOLUME:,} (got {_show(v)}).")
    return float(v)


def _check_vols(d, where):
    """Movement-volume dict {EBT: 120, ...}. Only movement keys are checked;
    other keys are ignored, as the balancer and renderers ignore them."""
    if d is None:
        return {}
    if not isinstance(d, dict):
        _bad(f"{where}: the movement volumes are in the wrong format.")
    for mv in ALL_MOVEMENTS:
        if mv in d:
            _check_volume(d[mv], f"{where} {mv}")
    return d


class ApiPrefixMiddleware:
    """Production stand-in for the Vite dev proxy.

    In development `frontend/vite.config.js` proxies `/api/*` to this server
    with the `/api` stripped, so the React code calls `/api/extract` while the
    route is registered as `/extract`. In production there is no dev server and
    no proxy — the same calls would 404. Stripping the prefix here keeps ONE set
    of URLs working in both places, so no frontend fetch needs a build-time base
    and no existing route (or test) changes.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/api" or path.startswith("/api/"):
                scope = dict(scope)
                scope["path"] = path[len("/api"):] or "/"
                raw = scope.get("raw_path")
                if raw and raw.startswith(b"/api"):
                    scope["raw_path"] = raw[len(b"/api"):] or b"/"
        await self.app(scope, receive, send)


app.add_middleware(ApiPrefixMiddleware)
app.add_middleware(BodySizeLimitMiddleware)

# CORS — allow the Vite React dev server (localhost:3000) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic(auto_error=False)


def require_auth(creds: HTTPBasicCredentials = Depends(security)):
    """Optional auth — only enforced when APP_PASSWORD env var is set."""
    if not APP_PASSWORD:
        return None  # local dev: auth disabled
    if creds is None or not secrets.compare_digest(
        creds.password.encode("utf-8"), APP_PASSWORD.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="DemandIQ"'},
        )
    return creds.username


# ── Forecasting routes (Method 1 / NCHRP 765) — mounted under /forecast ───────
# The import above is geo-free (the adapter's geopandas import is lazy), so the
# app starts under any interpreter; only /forecast/ingest-match needs the geo
# deps at call time (503 otherwise). Auth matches the rest of the app.
app.include_router(forecasting_router, prefix="/forecast",
                   dependencies=[Depends(require_auth)])


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/legacy", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def legacy_index(request: Request):
    """The original Phase A Jinja UI.

    `/` now serves the React build in production (see the SPA mount at the
    bottom of this file); this keeps the old single-page UI reachable.
    """
    return templates.TemplateResponse(request, "index.html")


def _extract_error_text(e):
    """Plain-language reason for a failed extraction (details go to the log)."""
    if isinstance(e, IOError):
        return ("Could not read this file. Check that it opens in Excel or a PDF "
                "viewer and is not password-protected.")
    if type(e) is ValueError:
        return str(e)          # count_agent's own messages are written for users
    return "Could not extract counts from this file right now. Try again in a minute."


# Plain def: FastAPI runs it in the threadpool, so a 15 s extraction never
# blocks the event loop (and /health) for other users.
@app.post("/extract", dependencies=[Depends(require_auth)])
def extract_endpoint(files: list[UploadFile] = File(...)):
    """Process uploaded files. Returns extracted JSON + errors."""
    if len(files) > MAX_EXTRACT_FILES:
        _bad(f"Too many files ({len(files)}). Upload at most {MAX_EXTRACT_FILES} at a time.")
    results = []
    errors = []

    for f in files:
        name = f.filename or "(unnamed file)"
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXT:
            errors.append({"file": name, "error": f"Unsupported file type: {ext or 'none'}. "
                           "Upload .xlsx, .xls, .pdf or .csv count sheets."})
            continue

        content = f.file.read(MAX_EXTRACT_FILE_BYTES + 1)
        if len(content) > MAX_EXTRACT_FILE_BYTES:
            errors.append({"file": name, "error": "File is larger than "
                           f"{MAX_EXTRACT_FILE_BYTES // (1024 * 1024)} MB. Split it into smaller files."})
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            data = extract(tmp_path, API_KEY)
            data["source_file"] = name
            results.append(data)
            # Record what the model read. Hash only — no file bytes stored.
            capture.record_extraction(name, content, result=data,
                                      model=COUNT_MODEL)
        except Exception as e:
            log.warning("extract failed for %s: %r", name, e)
            errors.append({"file": name, "error": _extract_error_text(e)})
            capture.record_extraction(name, content, error=e,
                                      model=COUNT_MODEL)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if not results:
        _bad("None of the files could be read. " +
             " ".join(f"{e['file']}: {e['error']}" for e in errors))
    return JSONResponse({"results": results, "errors": errors})


@app.post("/suggest-matches", dependencies=[Depends(require_auth)])
def suggest_matches(payload: dict):
    """Jev suggestions for extracted intersection names the UI couldn't match.
    {names: [str], candidates: [str]} → {available, suggestions: [{choice, p} | null]}."""
    names, candidates = payload.get("names"), payload.get("candidates")
    if not isinstance(names, list) or not isinstance(candidates, list):
        _bad("The intersection names to match are in the wrong format.")
    if not jev.available():
        return {"available": False, "suggestions": [None] * len(names)}
    return {"available": True,
            "suggestions": jev.suggest([str(n or "") for n in names], [str(c or "") for c in candidates])}


@app.post("/excel", dependencies=[Depends(require_auth)])
def excel_endpoint(payload: dict):
    """Generate Excel workbook from extraction results."""
    results = strip_illegal_chars(payload.get("results", []))
    if not results:
        _bad("No results to export.")
    if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
        _bad("The extraction results are in the wrong format.")

    wb = Workbook()
    ws = wb.active
    ws.title = "Extracted Counts"

    headers = ["Source File", "Vendor", "Intersection", "AM Peak", "PM Peak"]
    headers += [f"AM_{m}" for m in MOVEMENTS]
    headers += [f"PM_{m}" for m in MOVEMENTS]
    headers += ["PHF_AM", "PHF_PM", "HV%_AM", "HV%_PM", "HV Method", "Notes"]
    ws.append(headers)

    hdr_fill = PatternFill("solid", fgColor="2F3640")
    hdr_font = Font(bold=True, color="FFFFFF", size=10)
    for cell in ws[1]:
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r in results:
        source = r.get("source_file", "")
        vendor = r.get("vendor_detected", "")
        inters = r.get("intersections", [])
        if not isinstance(inters, list) or not all(isinstance(i, dict) for i in inters):
            _bad(f"The intersections in {_show(source) or 'a result'} are in the wrong format.")
        for inter in inters:
            where = f"Intersection {_show(inter.get('name', ''))}"
            vols = inter.get("volumes") or {}
            phf = inter.get("phf") or {}
            hv = inter.get("hv_pct") or {}
            if not all(isinstance(x, dict) for x in (vols, phf, hv)):
                _bad(f"{where}: volumes, PHF or heavy-vehicle % are in the wrong format.")
            am_vol = _check_vols(vols.get("AM"), f"{where} AM")
            pm_vol = _check_vols(vols.get("PM"), f"{where} PM")

            row = [
                source,
                vendor,
                inter.get("name", ""),
                inter.get("am_peak_start", ""),
                inter.get("pm_peak_start", ""),
            ]
            row += [am_vol.get(m) for m in MOVEMENTS]
            row += [pm_vol.get(m) for m in MOVEMENTS]
            row += [
                phf.get("AM"),
                phf.get("PM"),
                hv.get("AM"),
                hv.get("PM"),
                inter.get("hv_method", ""),
                inter.get("notes", ""),
            ]
            if any(v is not None and not isinstance(v, (str, int, float)) for v in row):
                _bad(f"{where}: a value is in the wrong format (expected text or a number).")
            ws.append(row)

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 36
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 10
    for col_idx in range(6, 6 + 32):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = 9
    for col_idx in range(6 + 32, 6 + 32 + 6):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = 11

    ws.freeze_panes = "D2"

    return StreamingResponse(
        io.BytesIO(workbook_bytes(wb)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="demandiq_results.xlsx"'},
    )


@app.post("/balance-nchrp", dependencies=[Depends(require_auth)])
def balance_nchrp_endpoint(payload: dict):
    """Stage 4: real NCHRP 255/765 turning-movement refinement.

    Replaces a previous cascade-prone link-by-link balancer (now removed).
    The algorithm:
      A) resolve_link_volumes — collapse adjacent intersection counts on the
         same link into one volume per directed edge (larger or average)
      B) Per-intersection 4×4 Fratar factoring against link-resolved and
         raw-derived row/col targets
      C) Round up to the next 5 (a counted movement never rounds to 0)
    There is no floor step: under Larger some movements can still end below
    their raw count, and the response reports every such drop.

    Payload:
      {
        "intersections": [{"num": int, "name": str,
                           "lat": float, "lng": float}, ...],  # lat/lng optional
                                                               # (used to set
                                                               # leg orientation)
        "unbalanced_volumes": {"AM": {num_str: {mv: val}}, "PM": {...}},
        "links": [{"fromNum", "toNum", "direction", "mode"}],
        "link_resolution_method": "larger" | "average",   # default 'larger'
        "tolerance": float,       # vehicles, 0 < tol <= 50, default 0.5
        "max_iterations": int    # per-int, 1..10000, default 500
      }

    Response (spec §9 + resolved_links for transparency):
      {
        "balanced": {"AM": {num_str: {mv: val}}, "PM": {...}},
        "resolved_links": {
            "AM": [{"fromNum","toNum","direction","volume"}, ...],
            "PM": [...]
        },
        "iterations": {"AM": {num_str: int}, "PM": {num_str: int}},
        "converged":  {"AM": {num_str: bool}, "PM": {num_str: bool}},
        "qc_flags": [...],
        "method": "larger" | "average",
      }
    """
    intersections = payload.get("intersections")
    if not isinstance(intersections, list) or not intersections:
        _bad("Add at least one intersection before balancing.")

    coords = {}
    listed = set()
    for it in intersections:
        n = _as_int(it.get("num")) if isinstance(it, dict) else None
        if n is None:
            _bad("Every intersection needs a whole-number ID (num).")
        if n in listed:
            _bad(f"Intersection {n} is listed twice. Give each intersection its own number.")
        listed.add(n)
        lat, lng = it.get("lat"), it.get("lng")
        if lat is not None and lng is not None:
            if not (_is_num(lat) and _is_num(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
                _bad(f"Intersection {n} has an invalid map location "
                     f"(lat {_show(lat)}, lng {_show(lng)}). Re-place its pin on the map.")
            coords[n] = {"lat": float(lat), "lng": float(lng)}

    unbalanced = payload.get("unbalanced_volumes")
    if not isinstance(unbalanced, dict):
        _bad("No volumes were sent. Enter counts for at least one period (AM or PM).")

    links_in = payload.get("links", [])
    if not isinstance(links_in, list):
        _bad("The intersection links are in the wrong format.")

    method = payload.get("link_resolution_method", "larger")
    if method not in ("larger", "average"):
        _bad("Link resolution method must be Larger or Average.")

    # Tolerance unit: vehicles — the largest leftover mismatch between an
    # intersection's balanced leg totals and their targets that still counts
    # as converged.
    tol = payload.get("tolerance", 0.5)
    if not _is_num(tol) or not 0 < tol <= 50:
        _bad("Convergence tolerance must be a number of vehicles above 0 and at most 50.")
    max_iter = _as_int(payload.get("max_iterations", 500))
    if max_iter is None or not 1 <= max_iter <= 10000:
        _bad("Maximum iterations must be a whole number from 1 to 10,000.")

    # Stage 4c.3: pass ALL links through (including mode='None') so they
    # appear in the link_conservation output as informational. Filtering by
    # mode happens internally in balance_nchrp (None skipped from leg-total
    # balancing + reconciliation; Land Use balanced but not reconciled;
    # Full balanced + reconciled to canonical).
    # F1: resolve_link_volumes only accepts the corridor-axis form ('EB/WB' or
    # 'NB/SB'); anything else is silently skipped, so reject it here.
    VALID_DIRECTIONS = ('EB/WB', 'NB/SB')
    active_links = []
    pair_axis = {}
    for L in links_in:
        if not isinstance(L, dict):
            _bad("The intersection links are in the wrong format.")
        a, b = _as_int(L.get("fromNum")), _as_int(L.get("toNum"))
        if a is None or b is None:
            _bad("Every link needs the numbers of the two intersections it connects.")
        for x in (a, b):
            if x not in listed:
                _bad(f"Link {a}→{b} refers to intersection {x}, which is not in the intersection list.")
        if a == b:
            _bad(f"Intersection {a} is linked to itself. Remove that link.")
        direction = L.get("direction")
        if direction not in VALID_DIRECTIONS:
            _bad(f"Link {a}→{b} has an invalid direction ({_show(direction)!r}). Set it to "
                 "EB/WB or NB/SB (re-create the link on the map if needed).")
        mode = L.get("mode", "Full")
        if mode not in ("Full", "Land Use", "None"):
            _bad(f"Link {a}→{b} has an unknown balance mode ({_show(mode)}). Use Full, Land Use or None.")
        pair = frozenset((a, b))
        if pair in pair_axis:
            if pair_axis[pair] != direction:
                _bad(f"Intersections {a} and {b} are linked on both EB/WB and NB/SB. "
                     "Two intersections can share only one street link.")
            _bad(f"Intersections {a} and {b} are linked twice. Remove the duplicate link.")
        pair_axis[pair] = direction
        active_links.append({"fromNum": a, "toNum": b, "direction": direction, "mode": mode})

    # Each leg reaches one neighbour: a second link on the same leg (1→3 next
    # to 1→2, or a 1-2-3-1 loop) silently double-counts that leg.
    leg_owner = {}
    for L in active_links:
        lo, hi, _ = _orient(L["fromNum"], L["toNum"], L["direction"], coords)
        sides = ("east", "west") if L["direction"] == "EB/WB" else ("north", "south")
        for n, side, other in ((lo, sides[0], hi), (hi, sides[1], lo)):
            if (n, side) in leg_owner:
                _bad(f"Intersection {n}'s {side} leg is linked to both intersection "
                     f"{leg_owner[(n, side)]} and intersection {other}. "
                     "Link each leg to its nearest neighbour only.")
            leg_owner[(n, side)] = other

    def _cast_period(period):
        period_vols = unbalanced.get(period)
        if period_vols is None:
            return {}
        if not isinstance(period_vols, dict):
            _bad(f"The {period} volumes are in the wrong format.")
        out = {}
        for n_str, mv_dict in period_vols.items():
            n = _as_int(n_str)
            if n not in listed:
                _bad(f"{period} volumes were sent for intersection {_show(n_str)}, "
                     "which is not in the intersection list.")
            mv_dict = _check_vols(mv_dict, f"Intersection {n} {period}")
            full = {k: 0.0 for k in ALL_MOVEMENTS}
            for mv, val in mv_dict.items():
                if mv in full and val is not None:
                    full[mv] = float(val)
            out[n] = full
        return out

    am_raw = _cast_period("AM")
    pm_raw = _cast_period("PM")
    counted = {p: any(any(v.values()) for v in raw.values())
               for p, raw in (("AM", am_raw), ("PM", pm_raw))}
    if not any(counted.values()):
        _bad("No volumes were sent. Enter counts for at least one period (AM or PM).")

    # A period with no counts at all (an AM-only project) is skipped. Within a
    # counted period, every linked intersection needs counts: the link has
    # nothing to balance against otherwise.
    for period, raw in (("AM", am_raw), ("PM", pm_raw)):
        if not counted[period]:
            continue
        for L in active_links:
            for n, other in ((L["fromNum"], L["toNum"]), (L["toNum"], L["fromNum"])):
                if not any((raw.get(n) or {}).values()):
                    _bad(f"Intersection {n} has no {period} counts but is linked to "
                         f"intersection {other}. Enter its {period} counts, or remove the link.")

    am_result = balance_nchrp(am_raw, active_links if counted["AM"] else [], method=method,
                              coords=coords, tol=tol, max_iter=max_iter)
    pm_result = balance_nchrp(pm_raw, active_links if counted["PM"] else [], method=method,
                              coords=coords, tol=tol, max_iter=max_iter)

    def _serialize_resolved(resolved, raw_pairs):
        """F7: each resolved-link entry now also carries the raw upstream and
        downstream counts that fed it, plus the absolute reconciliation
        delta. Lets the frontend surface 'algorithm smoothed away 300 veh of
        disagreement' instead of just the smoothed result."""
        out = []
        for (u, d, dirn), v in resolved.items():
            pair = raw_pairs.get((u, d, dirn)) or {}
            raw_up = pair.get('raw_upstream')
            raw_dn = pair.get('raw_downstream')
            entry = {
                "fromNum": u, "toNum": d, "direction": dirn, "volume": v,
                "raw_upstream":   raw_up if raw_up is not None else None,
                "raw_downstream": raw_dn if raw_dn is not None else None,
                "reconciliation_delta": (
                    abs(raw_dn - raw_up)
                    if raw_up is not None and raw_dn is not None
                    else None
                ),
            }
            out.append(entry)
        return out

    # Tag QC flags by period
    qc_all = []
    # Stage 10.1: source-data fractional-volume warnings (read-only check on
    # RAW inputs, before balancing). Surfaced alongside the NCHRP-* flags.
    qc_all += fractional_volume_flags(am_raw, period='AM')
    qc_all += fractional_volume_flags(pm_raw, period='PM')
    # F3 — source-data outlier warnings: flag intersections whose leg-approach
    # total is statistically implausible against the rest of the corridor (e.g.
    # an SBT count typed as 12 instead of 1200). Furness silently absorbs
    # these without this check.
    qc_all += source_outlier_flags(am_raw, period='AM')
    qc_all += source_outlier_flags(pm_raw, period='PM')

    # GEOMETRY-FALLBACK is corridor-level (geometry, not volumes) — AM and PM
    # share the same coords + links, so the flag would repeat. Dedupe by
    # (fromNum, toNum) and stamp period only on flags that didn't already
    # set one (flags with explicit period=None, like GEOMETRY-FALLBACK, are
    # left as-is so S6 renders them once corridor-wide).
    seen_geometry_pairs = set()

    def _append_flag(f, default_period):
        if f.get('check') == 'GEOMETRY-FALLBACK':
            pair = (f.get('fromNum'), f.get('toNum'))
            if pair in seen_geometry_pairs:
                return
            seen_geometry_pairs.add(pair)
            qc_all.append(dict(f))
            return
        if 'period' in f:
            qc_all.append(dict(f))
        else:
            qc_all.append({**f, 'period': default_period})

    for f in am_result['qc_flags']:
        _append_flag(f, 'AM')
    for f in pm_result['qc_flags']:
        _append_flag(f, 'PM')

    return {
        "balanced": {
            "AM": {str(n): vols for n, vols in am_result['balanced'].items()},
            "PM": {str(n): vols for n, vols in pm_result['balanced'].items()},
        },
        "resolved_links": {
            "AM": _serialize_resolved(am_result['resolved_links'], am_result.get('raw_link_pairs', {})),
            "PM": _serialize_resolved(pm_result['resolved_links'], pm_result.get('raw_link_pairs', {})),
        },
        "iterations": {
            "AM": {str(n): i for n, i in am_result['iterations'].items()},
            "PM": {str(n): i for n, i in pm_result['iterations'].items()},
        },
        "converged": {
            "AM": {str(n): c for n, c in am_result['converged'].items()},
            "PM": {str(n): c for n, c in pm_result['converged'].items()},
        },
        "final_residual": {
            "AM": {str(n): r for n, r in am_result['final_residual'].items()},
            "PM": {str(n): r for n, r in pm_result['final_residual'].items()},
        },
        "link_conservation": {
            "AM": am_result['link_conservation'],
            "PM": pm_result['link_conservation'],
        },
        "movements_review": {
            "AM": am_result['movements_review'],
            "PM": pm_result['movements_review'],
        },
        "qc_flags": qc_all,
        "method": method,
    }


# 16 movement keys including U-turns
ALL_MOVEMENTS = [
    'EBL', 'EBT', 'EBR', 'EBU',
    'WBL', 'WBT', 'WBR', 'WBU',
    'NBL', 'NBT', 'NBR', 'NBU',
    'SBL', 'SBT', 'SBR', 'SBU',
]


_RENDER_FAILED = "The diagram could not be drawn. Try again; if it keeps failing, contact support."


def _check_geo(geo):
    """Intersection geometry the renderers read: num, name, legs, streets."""
    if not isinstance(geo, dict) or _as_int(geo.get("num")) is None:
        _bad("Each diagram needs an intersection with a whole-number ID (num).")
    n = geo["num"]
    if not isinstance(geo.get("name"), str):
        _bad(f"Intersection {_show(n)} needs a name to draw its diagram.")
    for key, typ in (("active_legs", list), ("stopped_approaches", list),
                     ("streets", dict), ("control", str)):
        if key in geo and not isinstance(geo[key], typ):
            _bad(f"Intersection {_show(n)}: {key.replace('_', ' ')} is in the wrong format.")
    if any(not isinstance(v, str) for v in (geo.get("streets") or {}).values()):
        _bad(f"Intersection {_show(n)}: street names must be text.")
    return geo


def _check_cell(c):
    """One diagram cell {intersection, u_vols, b_vols, overrides}."""
    if not isinstance(c, dict):
        _bad("Each diagram cell is in the wrong format.")
    geo = _check_geo(c.get("intersection"))
    where = f"Intersection {geo['num']}"
    return {
        "geo": geo,
        "u_vols": _check_vols(c.get("u_vols"), where),
        "b_vols": _check_vols(c.get("b_vols"), where),
        "overrides": _check_vols(c.get("overrides"), f"{where} override"),
    }


def _check_cells(cells_in):
    if not isinstance(cells_in, list) or not cells_in:
        _bad("There are no intersections to draw.")
    if len(cells_in) > MAX_DIAGRAM_CELLS:
        _bad(f"Too many diagrams in one image ({len(cells_in)}). The limit is {MAX_DIAGRAM_CELLS}.")
    return [_check_cell(c) for c in cells_in]


def _check_view_mode(payload):
    view_mode = payload.get("view_mode", "combined")
    if view_mode not in VIEW_MODES:
        _bad(f"View mode must be one of: {', '.join(VIEW_MODES)}.")
    return view_mode


def _check_cols(payload):
    cols = _as_int(payload.get("cols", 3))
    if cols is None or not 1 <= cols <= 20:
        _bad("Columns must be a whole number from 1 to 20.")
    return cols


def _check_title(payload):
    title = payload.get("title")
    if title is not None and not isinstance(title, str):
        _bad("The diagram title must be text.")
    return title


# Plain def routes from here on: rendering and workbook building are CPU work,
# so they run in FastAPI's threadpool instead of freezing the event loop.
@app.post("/intersection-diagram", dependencies=[Depends(require_auth)])
def intersection_diagram_endpoint(payload: dict):
    """Render one intersection as a PNG line diagram.

    Wraps backend/line_diagram.render_single_diagram. Returns the PNG as
    base64 inside JSON so the frontend can drop it into an <img src=...>
    without an extra round-trip.
    """
    # Stage 5: optional per-cell engineer overrides {movement_key: number}
    # replace the b_vols value and add a "*" suffix to the rendered label.
    cell = _check_cell({"intersection": payload.get("intersection"), "u_vols": payload.get("u_vols"),
                        "b_vols": payload.get("b_vols"), "overrides": payload.get("overrides")})
    view_mode = _check_view_mode(payload)

    # Stage 10.4: optional free-text annotation, rendered as a caption strip.
    annotation = payload.get("annotation")
    if annotation is not None and not isinstance(annotation, str):
        _bad("The diagram note must be text.")

    try:
        png_bytes = render_single_diagram(cell["geo"], cell["u_vols"], cell["b_vols"], view_mode,
                                          overrides=cell["overrides"], annotation=annotation)
    except Exception:
        log.exception("intersection diagram render failed")
        raise HTTPException(status_code=500, detail=_RENDER_FAILED)

    px = int(FIG_INCHES * DPI)
    return {
        "intersection_num": cell["geo"]["num"],
        "png_base64": base64.b64encode(png_bytes).decode("ascii"),
        "width": px,
        "height": px,
    }


@app.post("/export-diagram-grid", dependencies=[Depends(require_auth)])
def export_diagram_grid_endpoint(payload: dict):
    """Stage 10.5: render every intersection diagram into ONE composite PNG
    grid. Reuses render_single_diagram's cell engine via render_diagram_grid.

    Payload:
      {
        "cells": [ {"intersection": {...geo}, "u_vols": {}, "b_vols": {},
                    "overrides": {}, "period_label": "AM"|"PM"|null }, ... ],
        "view_mode": "combined" | "balanced" | "unbalanced" | "delta",
        "cols": int,                # optional, default 3
        "title": str,               # optional banner
      }
    Returns: image/png (binary).
    """
    cells = _check_cells(payload.get("cells"))
    for c, raw in zip(cells, payload["cells"]):
        label = raw.get("period_label")
        if label is not None and not isinstance(label, str):
            _bad("The period label must be text (AM or PM).")
        c["period_label"] = label
    view_mode = _check_view_mode(payload)
    cols = _check_cols(payload)
    title = _check_title(payload)

    try:
        png_bytes = render_diagram_grid(cells, view_mode=view_mode, cols=cols, title=title)
    except Exception:
        log.exception("diagram grid render failed")
        raise HTTPException(status_code=500, detail=_RENDER_FAILED)

    return StreamingResponse(
        io.BytesIO(png_bytes),
        media_type="image/png",
        headers={"Content-Disposition": 'attachment; filename="demandiq_diagrams.png"'},
    )


@app.post("/corridor-diagram", dependencies=[Depends(require_auth)])
def corridor_diagram_endpoint(payload: dict):
    """PRD-03: render the corridor's two line-diagram figures (AM + PM) with
    per-segment link-volume labels between adjacent intersections.

    Returns both image bytes in one round-trip — base64 inside JSON so the
    frontend can drop each into an <img src=...> and bundle both into the
    Excel/zip export without an extra fetch.

    Segment labels show the BALANCED flow on each link in both directions
    (upstream outflow from the balanced volumes), not the resolution target.
    Corridors of up to line_diagram.CORRIDOR_ONE_ROW_MAX intersections are
    drawn in one row so every segment gets an in-figure label.

    Payload:
      {
        "cells": [ {"intersection": {...geo}, "u_vols": {}, "b_vols": {},
                    "overrides": {} }, ... ],            # in corridor order
        "resolved_links": {
            "AM": [{"fromNum","toNum","direction","volume"}, ...],
            "PM": [...],
        },
        "corridor_dir": "EB/WB" | "NB/SB",
        "view_mode": "combined" | "balanced" | "unbalanced" | "delta",
        "cols": int,                # optional, default 3 (used past one row)
        "title": str,               # optional banner (rendered on BOTH figures)
        "b_vols": {                 # optional; if provided overrides cell.b_vols
            "AM": {num_str: {mv: val}}, "PM": {...}
        }
      }
    Returns:
      { "am": {png_base64, width, height}, "pm": {...},
        "segment_counts": {"AM": int, "PM": int}  // segment labels drawn
      }
    """
    cells = _check_cells(payload.get("cells"))
    view_mode = _check_view_mode(payload)

    corridor_dir = payload.get("corridor_dir")
    if corridor_dir not in ("EB/WB", "NB/SB"):
        _bad("Corridor direction must be EB/WB or NB/SB.")

    resolved_in = payload.get("resolved_links") or {}
    if not isinstance(resolved_in, dict):
        _bad("The resolved links are in the wrong format.")
    resolved = {}
    for period in ("AM", "PM"):
        entries = resolved_in.get(period) or []
        if not isinstance(entries, list):
            _bad(f"The {period} resolved links are in the wrong format.")
        for r in entries:
            if (not isinstance(r, dict) or _as_int(r.get("fromNum")) is None
                    or _as_int(r.get("toNum")) is None or not isinstance(r.get("direction"), str)
                    or (r.get("volume") is not None and not _is_num(r.get("volume")))):
                _bad(f"A {period} resolved link is in the wrong format. Re-run the analysis.")
        resolved[period] = entries

    cols = _check_cols(payload)
    title = _check_title(payload)

    # Optional per-period balanced volumes — when provided, replace the
    # cell-level b_vols so AM and PM figures differ on the per-cell numbers
    # too (not just the segment labels). The frontend always sends both.
    period_b_vols = payload.get("b_vols") or {}
    if not isinstance(period_b_vols, dict):
        _bad("The balanced volumes are in the wrong format.")

    def _period_cells(period):
        period_b = period_b_vols.get(period) or {}
        if not isinstance(period_b, dict):
            _bad(f"The {period} balanced volumes are in the wrong format.")
        out = []
        for c in cells:
            b_vols = period_b.get(str(c["geo"]["num"]))
            if b_vols is not None:
                b_vols = _check_vols(b_vols, f"Intersection {c['geo']['num']} {period}")
            out.append({**c, "b_vols": b_vols if b_vols is not None else c["b_vols"]})
        return out

    period_cells = {p: _period_cells(p) for p in ("AM", "PM")}
    try:
        pngs = {p: render_corridor_diagram(period_cells[p], resolved[p], corridor_dir, p,
                                           view_mode=view_mode, cols=cols, title=title)
                for p in ("AM", "PM")}
    except Exception:
        log.exception("corridor diagram render failed")
        raise HTTPException(status_code=500, detail=_RENDER_FAILED)

    cell_px = int(FIG_INCHES * DPI)
    n = len(cells)
    cols_eff = corridor_cols(n, cols)
    width, height = cols_eff * cell_px, math.ceil(n / cols_eff) * cell_px

    return {
        p.lower(): {
            "png_base64": base64.b64encode(pngs[p]).decode("ascii"),
            "width": width,
            "height": height,
        }
        for p in ("AM", "PM")
    } | {
        "segment_counts": {p: count_segment_labels(period_cells[p], resolved[p], corridor_dir)
                           for p in ("AM", "PM")},
    }


def _check_project_state(payload):
    """Shapes build_workbook relies on; anything else is a 400, not a crash."""
    project = payload.get("project") or {}
    if not isinstance(project, dict) or not isinstance(project.get("name") or "", str):
        _bad("The project details are in the wrong format.")
    name = (project.get("name") or "").strip()
    if not name:
        _bad("The project needs a name before it can be exported.")
    for key in ("balancing", "analysis", "forecasting"):
        if not isinstance(payload.get(key) or {}, dict):
            _bad(f"The project's {key} section is in the wrong format.")
    if not isinstance((payload.get("analysis") or {}).get("lastRun") or {}, dict):
        _bad("The last analysis run is in the wrong format. Re-run the analysis.")
    intersections = payload.get("intersections") or []
    if not isinstance(intersections, list):
        _bad("The intersection list is in the wrong format.")
    for it in intersections:
        if not isinstance(it, dict) or _as_int(it.get("num")) is None:
            _bad("Every intersection needs a whole-number ID (num).")
        it["num"] = _as_int(it["num"])    # one type, so sorting by num works
        where = f"Intersection {it['num']}"
        approaches = it.get("approaches") or {}
        if not isinstance(approaches, dict):
            _bad(f"{where}: the approaches are in the wrong format.")
        for d, ap in approaches.items():
            if ap is not None and (not isinstance(ap, dict)
                                   or not isinstance(ap.get("lanes") or [], list)
                                   or not all(isinstance(l, dict) and isinstance(l.get("movement") or "", str)
                                              for l in ap.get("lanes") or [])):
                _bad(f"{where}: the {_show(d)} lane setup is in the wrong format.")
        for key in ("volumes", "balanced"):
            per = it.get(key) or {}
            if not isinstance(per, dict):
                _bad(f"{where}: {key} are in the wrong format.")
            for period, vols in per.items():
                if not isinstance(vols or {}, dict):
                    _bad(f"{where} {_show(period)}: {key} are in the wrong format.")
                for mv in ALL_MOVEMENTS:
                    v = (vols or {}).get(mv)
                    _check_volume(v.get("value") if isinstance(v, dict) else v,
                                  f"{where} {period} {mv}")
    overrides = (payload.get("balancing") or {}).get("overrides") or {}
    if not isinstance(overrides, dict):
        _bad("The engineer overrides are in the wrong format.")
    for period, by_int in overrides.items():
        if not isinstance(by_int or {}, dict):
            _bad("The engineer overrides are in the wrong format.")
        for n, cells in (by_int or {}).items():
            _check_vols(cells, f"Intersection {_show(n)} {period} override")
    return name


@app.post("/export-excel", dependencies=[Depends(require_auth)])
def export_excel_endpoint(payload: dict):
    """Generate the Excel workbook from the project snapshot.

    Accepts the same shape the frontend persists to localStorage as
    'demandiq:project' (plus an optional IANA "timezone" for the run time).
    Returns an .xlsx as a binary stream.
    """
    name = _check_project_state(payload)

    try:
        xlsx_bytes = build_workbook(payload)
    except Exception:
        log.exception("workbook build failed")
        raise HTTPException(status_code=500, detail="The Excel file could not be built. "
                            "Try again; if it keeps failing, contact support.")

    # Export is the moment the engineer signs off on the numbers, so it is the
    # cleanest place to record raw -> balanced -> override. Never blocks the
    # download: record_export swallows its own failures.
    capture.record_export(payload)

    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_") or "project"

    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.xlsx"',
        },
    )


@app.get("/health")
async def health():
    """Open endpoint — used by Render health checks."""
    return {"status": "ok"}


@app.get("/capture-stats", dependencies=[Depends(require_auth)])
def capture_stats():
    """How much evaluation data has accumulated.

    Behind auth, and counts only — no captured values are served. Use it to
    confirm production is actually writing (a Render instance with no
    DATABASE_URL silently collects nothing, because its disk is ephemeral).
    """
    return capture.stats()


# ── React SPA (production) ────────────────────────────────────────────────────
# Registered LAST so every API route above wins: FastAPI matches in definition
# order, and the catch-all below would otherwise shadow them.
#
# Local development does not use any of this — `npm run dev` serves the app on
# :3000 and proxies here — so the mount is skipped when there is no build.
DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if (DIST / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", dependencies=[Depends(require_auth)])
    async def spa(full_path: str):
        """Serve a built file when one matches, else index.html.

        The fallback is what makes a hard refresh on a client-side route like
        /results or /export work: React Router owns those paths, the server has
        no such route, so it must hand back the shell and let the app route.
        """
        candidate = (DIST / full_path).resolve()
        # Containment check — never serve outside the build directory.
        if candidate.is_file() and candidate.is_relative_to(DIST):
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")

else:
    @app.get("/", response_class=HTMLResponse)
    async def no_build():
        return HTMLResponse(
            "<h1>DemandIQ API is running</h1>"
            "<p>No frontend build found. In development the UI is served by "
            "Vite on <a href='http://localhost:3000'>:3000</a>; to serve it "
            "from here instead, run <code>cd frontend && npm run build</code>.</p>"
            "<p><a href='/legacy'>Legacy UI</a> · <a href='/docs'>API docs</a></p>",
            status_code=503,
        )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    # DEMANDIQ_DEV=1 turns on uvicorn auto-reload for local development (restarts
    # on source edits). Reload requires the "module:app" import string rather than
    # the app object. Production (Render) leaves DEMANDIQ_DEV unset, so the server
    # never watches the filesystem.
    reload = os.getenv("DEMANDIQ_DEV") == "1"
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=reload)
