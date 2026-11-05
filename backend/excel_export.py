"""
DemandIQ — Excel workbook export (PRD-02).

Builds a 3-tab .xlsx from the full project state (same shape as the
frontend's localStorage 'demandiq:project' snapshot):

  1. Provenance     — auditable run header (method, windows, tolerance,
                       max iter, run-at, direction) + per-intersection
                       convergence table + QC flags table.
  2. Combined View  — raw + balanced (final) side-by-side, per intersection,
                       with deltas. Preserved unchanged from prior versions.
  3. Raw Movements  — audit-grade flat table: one row per (int, movement)
                       with AM raw/balanced/Δ/override and PM raw/balanced/Δ/override.

The colleague hand-off is the workbook itself — a reader should be able to
verify which intersections converged, what method was used, and inspect every
movement's raw vs. balanced delta WITHOUT opening DemandIQ.

Legacy (pre-NCHRP) runs without lastRun.config or per-intersection convergence
fields degrade gracefully (missing fields render as '—' rather than crashing).
"""

import io
from datetime import datetime, date
from zoneinfo import ZoneInfo
import math
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter


# ── Constants ────────────────────────────────────────────────────────────────
ALL_DIRS = ('EB', 'WB', 'NB', 'SB')
ALL_MOVES = ('U', 'L', 'T', 'R')

# Styling (matches prior /excel palette + size)
HDR_FILL  = PatternFill('solid', fgColor='2F3640')
HDR_FONT  = Font(name='Arial', bold=True, color='FFFFFF', size=10)
SUB_FILL  = PatternFill('solid', fgColor='CFD8DC')
SUB_FONT  = Font(name='Arial', bold=True, color='000000', size=10)
DATA_FONT = Font(name='Arial', color='000000', size=10)
# Combined View / Raw Movements: red text for a negative delta (movement
# dropped below raw); amber fill marks drops on Raw Movements; light yellow
# fill (plus an "Engineer override" cell note) marks engineer overrides.
NEG_FONT  = Font(name='Arial', color='C00000', size=10)
OVR_FILL  = PatternFill('solid', fgColor='FFF2CC')
DROP_FILL = PatternFill('solid', fgColor='FFE5B4')

CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
LEFT   = Alignment(horizontal='left',   vertical='center')

# Standard L/T/R movement order (no U-turns) for the Combined / Raw views.
COMBINED_MOVEMENTS = (
    'EBL', 'EBT', 'EBR', 'WBL', 'WBT', 'WBR',
    'NBL', 'NBT', 'NBR', 'SBL', 'SBT', 'SBR',
)

_THIN = Side(style='thin', color='AAAAAA')
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# Sentinel for missing fields on legacy runs.
MISSING = '—'


# ── Helpers ──────────────────────────────────────────────────────────────────
def _active_movements(approach):
    """Set of active movement chars {U, L, T, R} for an approach dict."""
    if not approach or approach.get('exists') is False:
        return set()
    active = set()
    for lane in approach.get('lanes') or []:
        for ch in lane.get('movement', '') or '':
            if ch in ALL_MOVES:
                active.add(ch)
    return active


def _movement_present(it, key):
    """True if the L/T/R movement is active at this intersection (its approach
    exists and the movement letter is in the lane config)."""
    direction, mv = key[:2], key[2]
    active = _active_movements((it.get('approaches') or {}).get(direction))
    return mv in active


def _raw_value(it, period, key):
    """Raw count for a movement — strips the {value, _source} wrapper."""
    cell = ((it.get('volumes') or {}).get(period) or {}).get(key)
    if isinstance(cell, dict):
        return cell.get('value')
    return cell


def _final_value(it, period, key, period_overrides):
    """FINAL value = engineer override if present, else balanced. Mirrors the
    finalVolumeFor logic used across the app. Returns (value, is_override)."""
    int_overrides = (period_overrides or {}).get(str(it.get('num', ''))) or {}
    if key in int_overrides:
        return int_overrides[key], True
    bal = ((it.get('balanced') or {}).get(period) or {}).get(key)
    return bal, False


def _override_for(it, period, key, period_overrides):
    """The engineer override value for a (int, period, movement), or None."""
    int_overrides = (period_overrides or {}).get(str(it.get('num', ''))) or {}
    return int_overrides.get(key)


def _style_header(cell):
    cell.fill = HDR_FILL
    cell.font = HDR_FONT
    cell.alignment = CENTER
    cell.border = BORDER


def _style_subheader(cell):
    cell.fill = SUB_FILL
    cell.font = SUB_FONT
    cell.alignment = CENTER
    cell.border = BORDER


def _style_data(cell, align=None, fmt=None):
    cell.font = DATA_FONT
    cell.alignment = align or CENTER
    cell.border = BORDER
    if fmt:
        cell.number_format = fmt


def _method_label(m):
    if m == 'average':
        return 'Average'
    if m == 'larger':
        return 'Larger'
    return MISSING


def _window_str(w):
    if not isinstance(w, dict):
        return MISSING
    s = w.get('start')
    e = w.get('end')
    if not s or not e:
        return MISSING
    return f"{s} – {e}"


def _yn(b):
    if b is True:
        return 'Y'
    if b is False:
        return 'N'
    return MISSING


def _readable_date(v):
    """'2026-11-05' → 'Nov 5, 2026'; anything else shown as given."""
    if not v:
        return MISSING
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        return str(v)
    return f"{d:%b} {d.day}, {d.year}"


def _local_time(ts, tz_name=None):
    """ISO UTC timestamp → 'Nov 5, 2026 1:12 PM PST' in the user's zone."""
    if not ts:
        return MISSING
    try:
        t = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except ValueError:
        return str(ts)
    # shortcut: defaults to Pacific (the current client base) when the browser sends no zone.
    try:
        t = t.astimezone(ZoneInfo(tz_name or 'America/Los_Angeles'))
    except Exception:
        pass                    # unknown zone / no tz database: keep UTC
    hour = t.hour % 12 or 12
    return f"{t:%b} {t.day}, {t.year} {hour}:{t:%M} {t:%p} {t.tzname() or ''}".strip()


# ── Untrusted text (names come from users and from LLM-read vendor PDFs) ─────
_FORMULA_START = ('=', '+', '-', '@', '\t', '\r')


def strip_illegal_chars(obj):
    """Copy of a JSON-like payload with control characters removed from every
    string. openpyxl raises IllegalCharacterError on them at assignment time,
    so this must run before any cell is written."""
    if isinstance(obj, str):
        return ILLEGAL_CHARACTERS_RE.sub('', obj)
    if isinstance(obj, dict):
        return {k: strip_illegal_chars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_illegal_chars(v) for v in obj]
    return obj


def workbook_bytes(wb):
    """Save to bytes with every formula-looking string stored as literal text,
    so a name like '=HYPERLINK(...)' can never run in Excel. DemandIQ writes
    no formulas of its own."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and v.startswith(_FORMULA_START):
                    cell.data_type = 's'
                    cell.quotePrefix = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Tab 1: Provenance ────────────────────────────────────────────────────────
def _write_provenance(wb, project_state):
    """Auditable run header + per-intersection convergence + QC flags.

    Pulls from the NCHRP-era lastRun shape: config (method, windows,
    tolerance, max iter, direction), timestamp, intConverged, iterations,
    finalResidual, qcFlags. A legacy run without these fields renders the
    missing values as '—' (per PRD risk note); no crash.
    """
    ws = wb.create_sheet('Provenance', 0)

    project = project_state.get('project') or {}
    intersections = project_state.get('intersections') or []
    balancing = project_state.get('balancing') or {}
    analysis = project_state.get('analysis') or {}
    last_run = analysis.get('lastRun') or {}
    # config snapshot is the source of truth for the run's settings; fall back
    # to current balancing only for fields that aren't run-snapshotted.
    cfg = last_run.get('config') or {}

    run_at = _local_time(last_run.get('timestamp'), project_state.get('timezone'))
    method = _method_label(cfg.get('linkResolution') or last_run.get('method'))
    peak_mode = cfg.get('peakMode') or balancing.get('peakMode') or MISSING
    am_window = _window_str(cfg.get('amWindow') or balancing.get('amWindow'))
    pm_window = _window_str(cfg.get('pmWindow') or balancing.get('pmWindow'))
    tol = cfg.get('convergenceThreshold')
    tol_str = f"{tol:.2f} veh" if isinstance(tol, (int, float)) else MISSING
    max_iter = cfg.get('maxIterations') or balancing.get('maxIterations') or MISSING
    direction = cfg.get('corridorDir') or balancing.get('corridorDir') or analysis.get('corridorDir') or MISSING

    # ── Header block ────────────────────────────────────────────────────
    header_rows = [
        ('Project',         project.get('name', '') or MISSING),
        ('Client',          project.get('client') or MISSING),
        ('Analyst',         project.get('analyst') or MISSING),
        ('Date',            _readable_date(project.get('date'))),
        ('Run at',          run_at),
        ('Method',          method),
        ('Direction',       direction),
        ('Peak mode',       peak_mode),
        ('AM window',       am_window),
        ('PM window',       pm_window),
        ('Tolerance',       tol_str),
        ('Max iterations',  max_iter),
        ('Intersections',   len(intersections)),
    ]
    r = 1
    for k, v in header_rows:
        c1 = ws.cell(row=r, column=1, value=k)
        _style_subheader(c1)
        c2 = ws.cell(row=r, column=2, value=v)
        _style_data(c2, align=LEFT)
        # Span the value cell across the remaining columns for readability.
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
        r += 1

    r += 1  # spacer

    # ── Per-intersection convergence section ────────────────────────────
    sec = ws.cell(row=r, column=1, value='Per-intersection convergence')
    _style_header(sec)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    r += 1

    conv_headers = [
        'Int #', 'Name',
        'AM iter', 'AM conv', 'AM final residual',
        'PM iter', 'PM conv', 'PM final residual',
    ]
    for col, label in enumerate(conv_headers, start=1):
        _style_header(ws.cell(row=r, column=col, value=label))
    r += 1

    am_iter = (last_run.get('iterations') or {}).get('AM') or {}
    pm_iter = (last_run.get('iterations') or {}).get('PM') or {}
    am_conv = (last_run.get('intConverged') or {}).get('AM') or {}
    pm_conv = (last_run.get('intConverged') or {}).get('PM') or {}
    am_res  = (last_run.get('finalResidual') or {}).get('AM') or {}
    pm_res  = (last_run.get('finalResidual') or {}).get('PM') or {}

    def _res(d, key):
        v = d.get(key)
        return float(v) if isinstance(v, (int, float)) else MISSING

    for it in sorted(intersections, key=lambda x: x.get('num', 0)):
        key = str(it.get('num', ''))
        _style_data(ws.cell(row=r, column=1, value=it.get('num', '')), fmt='0')
        _style_data(ws.cell(row=r, column=2, value=it.get('name', '')), align=LEFT)
        # AM iter / conv / residual
        _style_data(ws.cell(row=r, column=3, value=am_iter.get(key, MISSING)), fmt='0')
        _style_data(ws.cell(row=r, column=4, value=_yn(am_conv.get(key))))
        am_r = _res(am_res, key)
        _style_data(ws.cell(row=r, column=5, value=am_r),
                    fmt='0.00' if isinstance(am_r, float) else None)
        # PM iter / conv / residual
        _style_data(ws.cell(row=r, column=6, value=pm_iter.get(key, MISSING)), fmt='0')
        _style_data(ws.cell(row=r, column=7, value=_yn(pm_conv.get(key))))
        pm_r = _res(pm_res, key)
        _style_data(ws.cell(row=r, column=8, value=pm_r),
                    fmt='0.00' if isinstance(pm_r, float) else None)
        r += 1

    r += 1  # spacer

    # ── Resolved link targets section (F7) ──────────────────────────────
    # Surfaces the algorithm's silent smoothing — the raw upstream and
    # downstream counts that fed each resolved volume, plus their absolute
    # disagreement. A 300-veh gap between raw_up and raw_dn that Larger
    # picked one side of used to be invisible in the deliverable; now it's
    # in a column. Skipped entirely for legacy runs whose resolvedLinks
    # entries don't carry raw_upstream / raw_downstream.
    resolved_am = (last_run.get('resolvedLinks') or {}).get('AM') or []
    resolved_pm = (last_run.get('resolvedLinks') or {}).get('PM') or []
    has_raw_recon = any(
        r.get('raw_upstream') is not None and r.get('raw_downstream') is not None
        for r in (resolved_am + resolved_pm)
    )
    if has_raw_recon:
        sec = ws.cell(row=r, column=1, value='Resolved link targets')
        _style_header(sec)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 1
        rt_headers = ['Period', 'From', 'To', 'Dir',
                      'Target veh', 'Raw upstream', 'Raw downstream', 'Recon Δ']
        for col, label in enumerate(rt_headers, start=1):
            _style_header(ws.cell(row=r, column=col, value=label))
        r += 1

        def _num(v):
            return float(v) if isinstance(v, (int, float)) else None

        for period, entries in (('AM', resolved_am), ('PM', resolved_pm)):
            # Stable order: from, to, direction.
            sorted_entries = sorted(
                entries,
                key=lambda e: (e.get('fromNum', 0), e.get('toNum', 0), e.get('direction') or ''),
            )
            for e in sorted_entries:
                _style_data(ws.cell(row=r, column=1, value=period))
                _style_data(ws.cell(row=r, column=2, value=e.get('fromNum', '')), fmt='0')
                _style_data(ws.cell(row=r, column=3, value=e.get('toNum', '')), fmt='0')
                _style_data(ws.cell(row=r, column=4, value=e.get('direction') or ''))
                _style_data(ws.cell(row=r, column=5, value=_num(e.get('volume'))), fmt='0')
                _style_data(ws.cell(row=r, column=6, value=_num(e.get('raw_upstream'))), fmt='0')
                _style_data(ws.cell(row=r, column=7, value=_num(e.get('raw_downstream'))), fmt='0')
                _style_data(ws.cell(row=r, column=8, value=_num(e.get('reconciliation_delta'))), fmt='0')
                r += 1

        r += 1  # spacer before QC

    # ── QC flags section ────────────────────────────────────────────────
    qc_flags = last_run.get('qcFlags') or []
    sec = ws.cell(row=r, column=1, value='QC flags')
    _style_header(sec)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    r += 1

    qc_headers = ['Check', 'Period', 'Message']
    for col, label in enumerate(qc_headers, start=1):
        _style_header(ws.cell(row=r, column=col, value=label))
    # Merge "Message" across remaining columns for legibility.
    ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=8)
    r += 1

    if not qc_flags:
        c = ws.cell(row=r, column=1, value='All clean')
        _style_data(c, align=LEFT)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    else:
        for f in qc_flags:
            _style_data(ws.cell(row=r, column=1, value=f.get('check') or ''), align=LEFT)
            _style_data(ws.cell(row=r, column=2, value=f.get('period') or ''))
            mcell = ws.cell(row=r, column=3, value=f.get('message') or '')
            _style_data(mcell, align=LEFT)
            ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=8)
            r += 1

    # Column widths sized for printable 8.5x11 portrait.
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 32
    for c in range(3, 9):
        ws.column_dimensions[get_column_letter(c)].width = 14
    ws.freeze_panes = 'A2'


# ── Tab 2: Combined View (preserved byte-for-byte from prior export) ─────────
def _write_combined_view(wb, intersections, overrides):
    """Stage 10.2: raw + balanced (final) side-by-side, per intersection.
    Columns: Movement | AM Raw | AM Balanced | AM Δ | PM Raw | PM Balanced | PM Δ.
    Δ = Balanced − Raw (red when negative). Override balanced cells stay
    numeric, with a light fill + "Engineer override" note. Each section ends
    in a TOTAL subtotal row."""
    ws = wb.create_sheet('Combined View')
    headers = ['Movement', 'AM Raw', 'AM Balanced', 'AM Δ',
               'PM Raw', 'PM Balanced', 'PM Δ']
    for col, label in enumerate(headers, start=1):
        _style_header(ws.cell(row=1, column=col, value=label))

    am_ovr = overrides.get('AM') or {}
    pm_ovr = overrides.get('PM') or {}

    r = 2
    for it in intersections:
        # Intersection header row — bold, merged across all 7 columns.
        label = f"Int {it.get('num', '')} — {it.get('name', '')}".strip(' —')
        hc = ws.cell(row=r, column=1, value=label)
        hc.fill = HDR_FILL
        hc.font = HDR_FONT
        hc.alignment = LEFT
        for c in range(1, 8):
            ws.cell(row=r, column=c).border = BORDER
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
        r += 1

        sums = {'AM': [0, 0, 0], 'PM': [0, 0, 0]}

        for key in COMBINED_MOVEMENTS:
            if not _movement_present(it, key):
                continue
            _style_data(ws.cell(row=r, column=1, value=key), align=LEFT)

            col = 2
            for period, ovr in (('AM', am_ovr), ('PM', pm_ovr)):
                raw = _raw_value(it, period, key)
                bal, is_ovr = _final_value(it, period, key, ovr)
                raw_n = raw if isinstance(raw, (int, float)) else 0
                bal_n = bal if isinstance(bal, (int, float)) else 0
                delta = bal_n - raw_n
                sums[period][0] += raw_n
                sums[period][1] += bal_n
                sums[period][2] += delta

                _style_data(ws.cell(row=r, column=col, value=raw), fmt='0')
                bcell = ws.cell(row=r, column=col + 1)
                if is_ovr and bal is not None:
                    # Numeric so SUM and copy-paste into Synchro work; the
                    # fill + note mark it as an engineer override.
                    bcell.value = bal
                    _style_data(bcell, fmt='0')
                    bcell.fill = OVR_FILL
                    bcell.comment = Comment('Engineer override', 'DemandIQ')
                else:
                    bcell.value = bal
                    _style_data(bcell, fmt='0')
                dcell = ws.cell(row=r, column=col + 2, value=delta)
                _style_data(dcell, fmt='+0;-0;0')
                if delta < 0:
                    dcell.font = NEG_FONT
                col += 3
            r += 1

        _style_subheader(ws.cell(row=r, column=1, value='TOTAL'))
        col = 2
        for period in ('AM', 'PM'):
            for i in range(3):
                tcell = ws.cell(row=r, column=col + i, value=sums[period][i])
                _style_subheader(tcell)
                if i == 2:
                    tcell.number_format = '+0;-0;0'
                else:
                    tcell.number_format = '0'
            col += 3
        r += 2

    ws.freeze_panes = 'A2'
    ws.column_dimensions['A'].width = 14
    for c in range(2, 8):
        ws.column_dimensions[get_column_letter(c)].width = 13


# ── Tab 3: Raw Movements (audit) ─────────────────────────────────────────────
def _write_raw_movements(wb, intersections, overrides):
    """Flat audit table: one row per (int, movement) pair.

    Columns: Int # | Movement | AM Raw | AM Balanced | AM Δ | AM Override |
             PM Raw | PM Balanced | PM Δ | PM Override.
    U-turns excluded (same set as Combined View). Drops (Δ < 0) get amber
    fill on the period's three-cell group; overrides show the engineer value
    in the Override column (blank when absent).
    """
    ws = wb.create_sheet('Raw Movements')
    headers = [
        'Int #', 'Movement',
        'AM Raw', 'AM Balanced', 'AM Δ', 'AM Override',
        'PM Raw', 'PM Balanced', 'PM Δ', 'PM Override',
    ]
    for col, label in enumerate(headers, start=1):
        _style_header(ws.cell(row=1, column=col, value=label))

    am_ovr = overrides.get('AM') or {}
    pm_ovr = overrides.get('PM') or {}

    r = 2
    for it in sorted(intersections, key=lambda x: x.get('num', 0)):
        for key in COMBINED_MOVEMENTS:
            if not _movement_present(it, key):
                continue
            _style_data(ws.cell(row=r, column=1, value=it.get('num', '')), fmt='0')
            _style_data(ws.cell(row=r, column=2, value=key), align=LEFT)

            col = 3
            for period, ovr in (('AM', am_ovr), ('PM', pm_ovr)):
                raw = _raw_value(it, period, key)
                bal_raw = ((it.get('balanced') or {}).get(period) or {}).get(key)
                override_val = _override_for(it, period, key, ovr)
                raw_n = raw if isinstance(raw, (int, float)) else 0
                bal_n = bal_raw if isinstance(bal_raw, (int, float)) else 0
                delta = bal_n - raw_n

                _style_data(ws.cell(row=r, column=col, value=raw), fmt='0')
                _style_data(ws.cell(row=r, column=col + 1, value=bal_raw), fmt='0')
                dcell = ws.cell(row=r, column=col + 2, value=delta)
                _style_data(dcell, fmt='+0;-0;0')
                ocell = ws.cell(row=r, column=col + 3, value=override_val)
                _style_data(ocell, fmt='0' if override_val is not None else None)

                # Drop highlight on the period's three numeric cells.
                if delta < 0:
                    dcell.font = NEG_FONT
                    for cc in range(col, col + 3):
                        ws.cell(row=r, column=cc).fill = DROP_FILL
                # Override highlight on the Override cell only.
                if override_val is not None:
                    ocell.fill = OVR_FILL

                col += 4
            r += 1

    ws.freeze_panes = 'C2'
    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 10
    for c in range(3, 11):
        ws.column_dimensions[get_column_letter(c)].width = 12


# ── Tab 4: Corridor VMT (forecasting only) ────────────────────────────────────
# Numeric formats for the VMT sheet.
VMT_NUM = '#,##0'      # veh-mi / volumes — whole, thousands-separated
LEN_NUM = '0.000'      # miles — 3 decimals


def _basis_label(b):
    if b == 'peak_hour':
        return 'Peak hour'
    if b == 'daily':
        return 'Daily'
    return b or MISSING


def _write_corridor_vmt(wb, project_state):
    """Forecasting deliverable: per-period corridor VMT, read VERBATIM from the
    /forecast/run response folded into project_state['forecasting']['results'].

    Reuses the SAME vmt object the F4 panel and provenance chip render, so the
    workbook and the in-app chip can never disagree. The sheet is OMITTED
    silently when forecasting results are absent — non-forecasting exports keep
    their three-tab schema untouched.

    Per period (AM/PM, in that order, only those present):
      • corridor summary (existing / future / Δ VMT, basis, length, link count)
      • daily row when a daily figure resolved (asterisk + note only when it was
        engineer-factor-estimated; data-derived daily carries neither)
      • per-link breakdown (segment, length, volumes, VMT, Δ, held)
      • a network rollup row
    Trust-critical caveats — the held-segment gap note and the daily-estimated
    note — are read from the gap_flag / daily_estimated fields of the same object.
    """
    forecasting = project_state.get('forecasting') or {}
    results = forecasting.get('results') or {}
    # Only periods that actually carry a vmt object, in canonical order.
    periods = [p for p in ('AM', 'PM') if (results.get(p) or {}).get('vmt')]
    if not periods:
        return  # No forecasting results → omit the sheet entirely.

    ws = wb.create_sheet('Corridor VMT')
    NCOLS = 9
    link_headers = ['From', 'To', 'Length (mi)', 'Existing vol', 'Future vol',
                    'Existing VMT', 'Future VMT', 'Δ VMT', 'Held']

    def _merge_banner(row, text, style=_style_header):
        c = ws.cell(row=row, column=1, value=text)
        style(c)
        for cc in range(1, NCOLS + 1):
            ws.cell(row=row, column=cc).border = BORDER
            if cc > 1 and style is _style_header:
                ws.cell(row=row, column=cc).fill = HDR_FILL
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NCOLS)

    r = 1
    for p in periods:
        vmt = results[p]['vmt']
        c = vmt.get('corridor') or {}
        net = vmt.get('network') or c
        links = vmt.get('links') or []

        # ── Period banner ───────────────────────────────────────────────
        _merge_banner(r, f'{p} Peak — Corridor VMT')
        r += 1

        # ── Corridor summary (label | value) ────────────────────────────
        summary = [
            ('Existing VMT (veh-mi)', c.get('existing_vmt'), VMT_NUM),
            ('Future VMT (veh-mi)',   c.get('future_vmt'),   VMT_NUM),
            ('Δ VMT (veh-mi)',        c.get('delta'),        '+#,##0;-#,##0;0'),
            ('Basis',                 _basis_label(c.get('basis')), None),
            ('Corridor length (mi)',  c.get('corridor_length_mi'), LEN_NUM),
            ('Links',                 c.get('link_count'),   '0'),
        ]
        for label, value, fmt in summary:
            _style_subheader(ws.cell(row=r, column=1, value=label))
            vc = ws.cell(row=r, column=2, value=value)
            _style_data(vc, align=LEFT, fmt=fmt if isinstance(value, (int, float)) else None)
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=NCOLS)
            r += 1

        # ── Daily row — only when a daily figure resolved ───────────────
        if c.get('daily_existing_vmt') is not None:
            estimated = bool(c.get('daily_estimated'))
            daily_rows = [
                ('Daily existing VMT (veh-mi)', c.get('daily_existing_vmt')),
                ('Daily future VMT (veh-mi)',   c.get('daily_future_vmt')),
                ('Daily Δ VMT (veh-mi)',        c.get('daily_delta')),
            ]
            for i, (label, value) in enumerate(daily_rows):
                _style_subheader(ws.cell(row=r, column=1, value=label))
                # Asterisk rides ONLY on the engineer-factor-estimated case,
                # matching the F4 panel; data-derived daily is bare.
                if estimated and value is not None:
                    vc = ws.cell(row=r, column=2, value=f'{value:,.0f}*')
                    _style_data(vc, align=LEFT)
                else:
                    vc = ws.cell(row=r, column=2, value=value)
                    _style_data(vc, align=LEFT,
                                fmt=VMT_NUM if isinstance(value, (int, float)) else None)
                ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=NCOLS)
                r += 1

        # ── Trust-critical caveats ──────────────────────────────────────
        if c.get('gap_flag'):
            cell = ws.cell(row=r, column=1, value='⚠ Contains held segments')
            _style_data(cell, align=LEFT)
            cell.font = NEG_FONT
            cell.fill = DROP_FILL
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NCOLS)
            r += 1
        if c.get('daily_estimated'):
            factor = c.get('expansion_factor')
            note = f'Daily VMT estimated from peak-hour, expansion factor {factor}'
            cell = ws.cell(row=r, column=1, value=note)
            _style_data(cell, align=LEFT)
            cell.fill = OVR_FILL
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NCOLS)
            r += 1

        r += 1  # spacer

        # ── Per-link breakdown ──────────────────────────────────────────
        for col, label in enumerate(link_headers, start=1):
            _style_header(ws.cell(row=r, column=col, value=label))
        r += 1
        for lk in links:
            _style_data(ws.cell(row=r, column=1, value=lk.get('from_num')), fmt='0')
            _style_data(ws.cell(row=r, column=2, value=lk.get('to_num')), fmt='0')
            _style_data(ws.cell(row=r, column=3, value=lk.get('length_mi')), fmt=LEN_NUM)
            _style_data(ws.cell(row=r, column=4, value=lk.get('existing_vol')), fmt=VMT_NUM)
            _style_data(ws.cell(row=r, column=5, value=lk.get('future_vol')), fmt=VMT_NUM)
            _style_data(ws.cell(row=r, column=6, value=lk.get('existing_vmt')), fmt=VMT_NUM)
            _style_data(ws.cell(row=r, column=7, value=lk.get('future_vmt')), fmt=VMT_NUM)
            dcell = ws.cell(row=r, column=8, value=lk.get('delta'))
            _style_data(dcell, fmt='+#,##0;-#,##0;0')
            if isinstance(lk.get('delta'), (int, float)) and lk['delta'] < 0:
                dcell.font = NEG_FONT
            held = lk.get('held')
            hcell = ws.cell(row=r, column=9, value='Y' if held else '')
            _style_data(hcell)
            if held:
                for cc in range(1, NCOLS + 1):
                    ws.cell(row=r, column=cc).fill = DROP_FILL
            r += 1

        # ── Network rollup row ──────────────────────────────────────────
        _style_subheader(ws.cell(row=r, column=1, value='Network total'))
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        ws.cell(row=r, column=2).border = BORDER
        ws.cell(row=r, column=2).fill = SUB_FILL
        nrow = [
            (3, net.get('corridor_length_mi'), LEN_NUM),
            (4, None, None), (5, None, None),
            (6, net.get('existing_vmt'), VMT_NUM),
            (7, net.get('future_vmt'), VMT_NUM),
            (8, net.get('delta'), '+#,##0;-#,##0;0'),
            (9, None, None),
        ]
        for col, value, fmt in nrow:
            tcell = ws.cell(row=r, column=col, value=value)
            _style_subheader(tcell)
            if fmt and isinstance(value, (int, float)):
                tcell.number_format = fmt
        r += 1

        # ── Scenario block — only when an engineer reduction factor was applied ──
        # The factor and the word "Scenario" travel onto the sheet face so the
        # mitigated number can never be read as the unmitigated forecast. Omitted
        # entirely when no factor was supplied.
        sceno = vmt.get('scenario')
        if sceno:
            r += 1  # spacer
            factor_pct = f"{sceno['factor'] * 100:g}%"
            _merge_banner(r, f'Scenario — demand reduction {factor_pct} '
                             f'(engineer assumption, applied not generated)')
            r += 1
            estimated = bool(sceno.get('daily_estimated'))
            pct = sceno.get('pct')
            pct_str = f'{pct * 100:.1f}%' if isinstance(pct, (int, float)) else MISSING
            srows = [
                ('Baseline VMT (veh-mi)', sceno.get('baseline_vmt'), VMT_NUM),
                ('Scenario VMT (veh-mi)', sceno.get('vmt'), VMT_NUM),
                ('Δ VMT (veh-mi)', sceno.get('delta'), '+#,##0;-#,##0;0'),
                ('Δ %', pct_str, None),
            ]
            for label, value, fmt in srows:
                _style_subheader(ws.cell(row=r, column=1, value=label))
                vc = ws.cell(row=r, column=2, value=value)
                _style_data(vc, align=LEFT,
                            fmt=fmt if isinstance(value, (int, float)) else None)
                ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=NCOLS)
                r += 1

            # Daily scenario rows carry the SAME asterisk discipline as base daily.
            if sceno.get('daily_vmt') is not None:
                for label, value in (('Daily baseline VMT (veh-mi)', sceno.get('daily_baseline_vmt')),
                                     ('Daily scenario VMT (veh-mi)', sceno.get('daily_vmt')),
                                     ('Daily Δ VMT (veh-mi)', sceno.get('daily_delta'))):
                    _style_subheader(ws.cell(row=r, column=1, value=label))
                    if estimated and value is not None:
                        vc = ws.cell(row=r, column=2, value=f'{value:,.0f}*')
                        _style_data(vc, align=LEFT)
                    else:
                        vc = ws.cell(row=r, column=2, value=value)
                        _style_data(vc, align=LEFT,
                                    fmt=VMT_NUM if isinstance(value, (int, float)) else None)
                    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=NCOLS)
                    r += 1

            # Per-link baseline-vs-scenario breakdown.
            r += 1
            for col, label in enumerate(['From', 'To', 'Baseline VMT', 'Scenario VMT', 'Δ VMT'], start=1):
                _style_header(ws.cell(row=r, column=col, value=label))
            r += 1
            for lk in sceno.get('links') or []:
                _style_data(ws.cell(row=r, column=1, value=lk.get('from_num')), fmt='0')
                _style_data(ws.cell(row=r, column=2, value=lk.get('to_num')), fmt='0')
                _style_data(ws.cell(row=r, column=3, value=lk.get('baseline_future_vmt')), fmt=VMT_NUM)
                _style_data(ws.cell(row=r, column=4, value=lk.get('scenario_future_vmt')), fmt=VMT_NUM)
                dcell = ws.cell(row=r, column=5, value=lk.get('delta'))
                _style_data(dcell, fmt='+#,##0;-#,##0;0')
                if isinstance(lk.get('delta'), (int, float)) and lk['delta'] < 0:
                    dcell.font = NEG_FONT
                r += 1

        r += 2  # spacer before the next period

    # Column widths.
    ws.column_dimensions['A'].width = 26
    for col in range(2, NCOLS + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14
    ws.freeze_panes = 'A2'


# ── Public API ───────────────────────────────────────────────────────────────
def build_workbook(project_state):
    """Build the workbook from the project snapshot.

    Returns: bytes (.xlsx data). Tab order: Provenance, Combined View, Raw
    Movements, then a "Corridor VMT" tab ONLY when the project carries
    forecasting results (omitted otherwise — non-forecasting exports keep the
    three-tab schema). No per-intersection tabs; no legacy convergence/
    adjustments sheets (those reads were dead in the NCHRP era).
    """
    project_state = strip_illegal_chars(project_state)
    intersections = project_state.get('intersections') or []
    overrides = ((project_state.get('balancing') or {}).get('overrides')) or {'AM': {}, 'PM': {}}

    wb = Workbook()
    # Drop the default "Sheet" — we create our own three explicitly.
    default = wb.active
    wb.remove(default)

    _write_provenance(wb, project_state)
    _write_combined_view(wb, intersections, overrides)
    _write_raw_movements(wb, intersections, overrides)
    # Forecasting deliverable — appends a "Corridor VMT" sheet only when the
    # project carries forecasting results; otherwise a no-op (schema unchanged).
    _write_corridor_vmt(wb, project_state)

    return workbook_bytes(wb)


# ── Forecast workbook (Forecasting F1–F4) ────────────────────────────────────
# Separate from build_workbook: the forecast deliverable is its own file. Every
# number is read VERBATIM from the /forecast/run response F4 renders, so the
# workbook and F4 can never disagree. Labels come from forecasting.run — the
# settings the results were actually run under — never the current F1/F3 form.
FC_MOVEMENTS = ('NBL', 'NBT', 'NBR', 'SBL', 'SBT', 'SBR', 'EBL', 'EBT', 'EBR', 'WBL', 'WBT', 'WBR')
FC_LINK_METHODS = {
    'ratio': 'Ratio (growth)', 'difference': 'Difference (incremental)',
    'combined': 'Average of ratio and difference', 'weighted': 'Weighted growth',
    'modified_average': 'Modified average', 'nchrp765': 'Auto — NCHRP 765 rule',
    'odot': 'Auto — ODOT decision tree', 'trend': 'Historical trend (no model)',
    'rate': 'Growth rate entered (no model)',
}
FC_TURN_METHODS = {'furness': 'NCHRP standard (Furness, then corridor balance)',
                   'fratar': 'Trip table — Fratar', 'fuzzy': 'Trip table — fuzzy'}
FC_MODEL = {'SANDAG': ('SANDAG (San Diego)', 'hwyLoad_{y}.shp', 'AM 6–9 am (3 h), PM 3:30–7 pm (3.5 h)'),
            'MTC': ('MTC Travel Model One (Bay Area)', 'mtc/{y}/network_links.shp', 'AM 6–10 am (4 h), PM 3–7 pm (4 h)'),
            'UPLOAD': ("Engineer's own model file (uploaded)", '{y} model year file', None)}


def _js_round(v):
    """Math.round, so workbook integers match F4 exactly (Python round() is banker's)."""
    return int(math.floor(float(v) + 0.5))


def forecast_notes(pdata):
    """Distinct run notes for one period: [(code, message, [where...])] — the
    same flags F3/F4 show (leg-level, inbound/outbound, Pass 1, Pass 2)."""
    by = {}

    def add(f, where):
        key = (f.get('code') or f.get('check') or '', f.get('message') or '')
        by.setdefault(key, []).append(where)

    for r in pdata.get('refinement') or []:
        n = r.get('intersection_num')
        for d in ('NB', 'SB', 'EB', 'WB'):
            leg = (r.get('approaches') or {}).get(d) or {}
            for f in leg.get('flags') or []:
                add(f, f'Int {n} {d}')
            for io in ('inbound', 'outbound'):
                for f in (leg.get(io) or {}).get('flags') or []:
                    add(f, f"Int {n} {d} {'in' if io == 'inbound' else 'out'}")
    for n, m in ((pdata.get('pass1') or {}).get('meta') or {}).items():
        for f in (m or {}).get('flags') or []:
            add(f, f'Int {n}')
    for f in (pdata.get('pass2') or {}).get('qc_flags') or []:
        add(f, f"Int {f.get('int')}" if f.get('int') else 'corridor')
    return [(c, m, w) for (c, m), w in by.items()]


def build_forecast_workbook(project_state):
    """Forecast deliverable → .xlsx bytes. Tabs: Forecast Summary, Forecast TMCs,
    then Corridor VMT when the run carries VMT."""
    project_state = strip_illegal_chars(project_state)
    fc = project_state.get('forecasting') or {}
    results = fc.get('results') or {}
    run = fc.get('run') or {}
    st = run.get('settings') or fc.get('settings') or {}
    region = run.get('region') or fc.get('region') or 'SANDAG'
    rm, tm = st.get('refineMethod') or 'ratio', st.get('turnMethod') or 'furness'
    no_model = rm in ('trend', 'rate')
    model_name, file_pat, periods_txt = FC_MODEL.get(region, (region, '{y}', MISSING))
    if periods_txt is None:   # uploaded model: period lengths the engineer entered in F1
        ph = fc.get('periodHours') or {}
        periods_txt = ', '.join(f"{p} {ph[p]} h" for p in ('AM', 'PM') if ph.get(p)) or MISSING
    years = run.get('modelYears') or {}
    names = {it.get('num'): it for it in project_state.get('intersections') or []}

    wb = Workbook()
    wb.remove(wb.active)

    # ── Summary ──────────────────────────────────────────────────────────
    ws = wb.create_sheet('Forecast Summary')
    k = st.get('peakHourFactor')
    rows = [
        ('Project', (project_state.get('project') or {}).get('name') or MISSING),
        ('Count year', run.get('baseYear') or fc.get('baseYear') or MISSING),
        ('Forecast (horizon) year', run.get('horizonYear') or fc.get('horizonYear') or MISSING),
        ('How each road grows', FC_LINK_METHODS.get(rm, rm)),
        ('How turns are formed', FC_TURN_METHODS.get(tm, tm)),
    ]
    if no_model:
        rows.append(('Travel model', 'none — no-model growth'))
        if rm == 'rate':
            rows.append(('Growth rate (% per year, straight line)', st.get('growthRate')))
        else:
            rows.append(('Past counts (year, volume)', (st.get('trendText') or '').strip() or MISSING))
    else:
        rows += [
            ('Travel model', model_name),
            ('Model files used', ', '.join(
                f"{file_pat.format(y=years[r])} ({r}, {years[r]})" for r in ('base', 'horizon') if years.get(r))
                or MISSING),
            ('Model periods', periods_txt),
            ('K — peak hour ÷ model period', k if k not in (None, '') else 'not given (Ratio needs no K)'),
        ]
    if tm == 'furness':
        rows.append(('Link disagreement resolution', _method_label(st.get('method') or 'larger')))
    if tm == 'fuzzy':
        rows.append(('Fuzzy band (± %)', st.get('fuzzyBand')))
    rows += [
        ('Rounding', 'Future volumes rounded once per NCHRP 765 Table 4-8 (<100 → 10, <1,000 → 50, '
                     '<10,000 → 100, else 500/1,000). Δ = unrounded future − existing.'),
        ('Units', 'vehicles per peak hour (vph)'),
    ]
    for r_, (label, value) in enumerate(rows, start=1):
        _style_subheader(ws.cell(row=r_, column=1, value=label))
        _style_data(ws.cell(row=r_, column=2, value=value), align=LEFT)

    r_ = len(rows) + 2
    for col, h in enumerate(('Period', 'Note', 'Code', 'Where'), start=1):
        _style_header(ws.cell(row=r_, column=col, value=h))
    for p in ('AM', 'PM'):
        if p not in results:
            continue
        for code, msg, where in forecast_notes(results[p]):
            r_ += 1
            where_txt = f'{len(where)} places' if len(where) > 6 else ', '.join(map(str, where))
            for col, v in enumerate((p, msg, code, where_txt), start=1):
                _style_data(ws.cell(row=r_, column=col, value=v), align=LEFT)
    ws.column_dimensions['A'].width = 34
    ws.column_dimensions['B'].width = 90
    ws.column_dimensions['C'].width = 30
    ws.column_dimensions['D'].width = 30

    # ── Future TMCs ──────────────────────────────────────────────────────
    ws = wb.create_sheet('Forecast TMCs')
    heads = ('Int', 'Intersection', 'Period', 'Movement', 'Existing (vph)', 'Future (vph)',
             'Future unrounded', 'Δ (vph)', 'Change %', 'Note')
    for col, h in enumerate(heads, start=1):
        _style_header(ws.cell(row=1, column=col, value=h))
    r_ = 1
    for p in ('AM', 'PM'):
        p2 = (results.get(p) or {}).get('pass2') or {}
        fut, unr = p2.get('balanced') or {}, p2.get('balanced_unrounded') or {}
        for n in sorted(fut, key=lambda x: int(x)):
            it = names.get(int(n)) or {}
            ex_all = (it.get('balanced') or {}).get(p) or {}
            for mv in FC_MOVEMENTS:
                ex_raw = float(ex_all.get(mv) or 0)
                fu = fut[n].get(mv) or 0
                fu_raw = float((unr.get(n) or {}).get(mv, fu) or 0)
                ex = _js_round(ex_raw)
                if ex <= 0 and fu <= 0:
                    continue
                d = _js_round(fu_raw - ex_raw)
                pct = (fu_raw - ex_raw) / ex_raw if ex_raw > 0 else None
                r_ += 1
                vals = (int(n), it.get('name') or MISSING, p, mv, ex, fu, round(fu_raw, 1), d, pct,
                        'decline' if d < 0 else '')
                for col, v in enumerate(vals, start=1):
                    c = ws.cell(row=r_, column=col, value=v)
                    _style_data(c, fmt={7: '0.0', 8: '+#,##0;-#,##0;0', 9: '+0.0%;-0.0%;0.0%'}.get(col))
                    if d < 0 and col in (8, 9, 10):
                        c.font = NEG_FONT
    widths = (6, 34, 8, 10, 14, 13, 16, 10, 10, 10)
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.freeze_panes = 'A2'

    _write_corridor_vmt(wb, project_state)

    return workbook_bytes(wb)
