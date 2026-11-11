"""Free code reader for count files: no AI, no cost.

read(path) returns (result, reason). result has exactly the shape of
count_agent.extract(); it is None when the file needs the cloud reader, and
reason says why: 'no text layer', 'unknown layout: ...' or 'check failed: ...'.
Every reading must pass read_checks before it is returned.

Layouts it reads:
  tabular   any table whose columns are named by movement (NL NT NR NU ... or
            NBL NBT ...): NDS / Quality Counts PDFs and Excel, the Forger's
            style T, the Union City demo sheets, CSV.
  diagram   the Forger's style D (Counts Unlimited-like peak-hour diagram).
"""
import csv
import re
from pathlib import Path

import read_checks
from count_agent import MOVEMENTS, _read_excel

_DIR = {'N': 'NB', 'S': 'SB', 'E': 'EB', 'W': 'WB'}
_WORD = {'NB': 'northbound', 'SB': 'southbound', 'EB': 'eastbound', 'WB': 'westbound'}
_TIME = re.compile(r'^(\d{1,2}):(\d{2})(?::\d{2})?\s*([AP]M)?$', re.I)
_TIME_IN = re.compile(r'(\d{1,2}):(\d{2})\s*([AP]M)', re.I)
_NUM = re.compile(r'^-?\d+(\.\d+)?$')


class _Unknown(Exception):
    pass


def _header_mv(cell):
    """Movement named by a column header ('NL', 'NBL', 'NORTHBOUND.NL'), else None."""
    tok = re.split(r'[\s.]+', cell.strip().upper())[-1]
    if len(tok) == 2 and tok[0] in _DIR and tok[1] in 'LTRU':
        return _DIR[tok[0]] + tok[1]
    if len(tok) == 3 and tok[:2] in _DIR.values() and tok[2] in 'LTRU':
        return tok
    return None


def _clock(h, m, ampm):
    h = int(h) % 12 + (12 if ampm.upper() == 'PM' else 0) if ampm else int(h)
    return f'{h:02d}:{int(m):02d}'


def _time(cell):
    m = _TIME.match(cell.strip())
    return _clock(*m.groups()) if m else None


def _num(cell):
    s = cell.strip().replace(',', '')
    if s in ('', 'None', '-'):
        return None
    if not _NUM.match(s):
        raise _Unknown(f'"{cell}" where a count belongs')
    f = float(s)
    return int(f) if f.is_integer() else f


def _period(t):
    for period, (lo, hi) in read_checks.WINDOWS.items():
        if lo <= t < hi:
            return period
    return None


def _tabular(rows):
    """{'AM': reading, 'PM': reading} from rows of cells under movement headers."""
    blocks, cur = [], None
    for cells in rows:
        cells = [str(c).strip() for c in cells]
        cols = {i: mv for i, mv in ((i, _header_mv(c)) for i, c in enumerate(cells)) if mv}
        if len(cols) >= 12 and len(set(cols.values())) == len(cols):
            cur = {'cols': cols, 'width': len(cells), 'quarters': [], 'stated': None, 'start': None,
                   'total': next((i for i, c in enumerate(cells) if c.upper() == 'TOTAL'), None)}
            blocks.append(cur)
            continue
        if not cur or not cells or not cells[0]:
            continue
        label = cells[0]
        is_peak = re.match(r'peak\s*h(ou)?r', label, re.I) and not re.search(r'factor|%', label, re.I)
        if not (_time(label) or is_peak):
            continue
        if len(cells) != cur['width']:                # a merged or missing cell shifts every column
            raise _Unknown('a row has a different number of cells than its header')
        if is_peak and (m := _TIME_IN.search(cells[min(cur['cols'])])):
            cur['start'] = _clock(*m.groups())        # 'PEAK HR : 07:45 AM - 08:45 AM'
            continue
        vols = {mv: _num(cells[i]) for i, mv in cur['cols'].items()}
        if all(v is None for v in vols.values()):
            continue                                  # interval outside the count period
        row = {'vols': vols, 'total': _num(cells[cur['total']]) if cur['total'] is not None else None}
        if is_peak:
            cur['stated'] = row
        else:
            cur['quarters'].append(dict(row, time=_time(label)))

    out = {}
    for period in read_checks.WINDOWS:
        mine = [b for b in blocks if any(_period(q['time']) == period for q in b['quarters'])]
        quarters = [q for b in mine for q in b['quarters'] if _period(q['time']) == period]
        if not quarters:
            raise _Unknown(f'no {period} counts found')
        if len({q['time'] for q in quarters}) != len(quarters):
            raise _Unknown(f'{period} 15-min rows repeat (several tables or pages)')
        stated = [b for b in mine if b['stated'] or b['start']]
        if len(stated) > 1 or any({_period(q['time']) for q in b['quarters']} - {period} for b in stated):
            raise _Unknown(f'cannot tell which peak-hour row is {period}')
        cols = set(mine[0]['cols'].values())
        if any(set(b['cols'].values()) != cols for b in mine):
            raise _Unknown('tables have different columns')
        b = stated[0] if stated else {'stated': None, 'start': None}
        start = b['start'] or (read_checks.busiest_starts(quarters, period) or [quarters[0]['time']])[0]
        rows = read_checks.peak_quarters(quarters, start)
        if b['stated']:
            hour = b['stated']['vols']
        elif rows:
            hour = {mv: sum(r['vols'][mv] or 0 for r in rows) for mv in cols}
        else:
            raise _Unknown(f'no full {period} peak hour of 15-min rows')
        volumes = {mv: (hour.get(mv) or 0) if mv in cols else None for mv in MOVEMENTS}
        peak15 = max(read_checks.quarter_total(r) for r in rows) if rows else 0
        out[period] = {
            'volumes': volumes, 'peak_start': start, 'quarters': quarters,
            'phf': round(sum(v or 0 for v in volumes.values()) / (4 * peak15), 3) if peak15 else None,
            'stated_hour': b['stated'] and b['stated']['vols'],
            'stated_total': b['stated'] and b['stated']['total'],
            'approach_totals': None,
        }
    return out


# ── Forger style D: peak-hour diagram, one panel per period ─────────────────
# shortcut: positions are the Forger's style-D drawing only; add a template per
# real diagram vendor once sample PDFs exist (real ones go to the cloud today).
_EDGE_APPROACH = {
    'N-up': {'top': 'SB', 'bottom': 'NB', 'left': 'EB', 'right': 'WB'},
    'E-up': {'top': 'WB', 'bottom': 'EB', 'left': 'SB', 'right': 'NB'},
}
# (edge, turn or 'total') -> (dx, dy above the road centre, how the text is aligned)
_SLOTS = {}
for _i, _t in enumerate('LTR'):
    _SLOTS[('top', _t)] = ((_i - 1) * 36, 108, 'c')
    _SLOTS[('bottom', _t)] = ((1 - _i) * 36, -114, 'c')
    _SLOTS[('left', _t)] = (-116, (1 - _i) * 24 - 3, 'r')
    _SLOTS[('right', _t)] = (116, (_i - 1) * 24 - 3, 'l')
_SLOTS.update({('top', 'total'): (0, 125, 'c'), ('bottom', 'total'): (0, -129, 'c'),
               ('left', 'total'): (-144, -39, 'c'), ('right', 'total'): (144, 33, 'c')})


def _diagram(page):
    roads = sorted((r for r in page.rects if abs(r['width'] - 200) < 1 and abs(r['height'] - 32) < 1),
                   key=lambda r: r['top'])
    if len(roads) != 2:
        raise _Unknown('diagram panels not found')
    words = page.extract_words()
    out = {}
    for period, road in zip(('AM', 'PM'), roads):
        cx, cy = (road['x0'] + road['x1']) / 2, (road['top'] + road['bottom']) / 2
        panel = [w for w in words if abs(w['bottom'] - cy) < 150]
        nx = cx + 144                                 # compass: vertical arrow = N up
        up = any(abs(l['x0'] - nx) < 1.5 and abs(l['x1'] - nx) < 1.5 and abs(l['top'] - (cy - 132)) < 1.5
                 and abs(l['height'] - 14) < 1 for l in page.lines)
        side = any(abs(l['x0'] - nx) < 1.5 and abs(l['width'] - 14) < 1 and abs(l['top'] - (cy - 125)) < 1.5
                   for l in page.lines)
        if up == side:
            raise _Unknown('compass not found')
        edges = _EDGE_APPROACH['N-up' if up else 'E-up']
        texts = [w['text'] for w in panel]
        try:
            i = texts.index('HOUR')
            start = _clock(*_TIME_IN.match(f'{texts[i + 1]} {texts[i + 2]}').groups())
            g = texts.index('ENTERING:') + 1
            grand = int(texts[g])
        except (ValueError, IndexError, AttributeError):
            raise _Unknown('peak hour or total entering not found')
        nums = [w for j, w in enumerate(panel) if w['text'].isdigit() and j != g]
        got = {}
        for (edge, slot), (dx, dy, align) in _SLOTS.items():
            x = cx + dx
            hits = [w for w in nums if abs({'c': (w['x0'] + w['x1']) / 2, 'r': w['x1'], 'l': w['x0']}[align] - x) < 6
                    and abs(w['bottom'] - 2 - (cy - dy)) < 4]
            if len(hits) != 1:
                raise _Unknown(f'{period} {edge} {slot} number not found')
            got[(edges[edge], slot)] = int(hits[0]['text'])
        if len(nums) != len(_SLOTS):
            raise _Unknown(f'{period} panel has numbers the template does not know')
        out[period] = {
            'volumes': {mv: got.get((mv[:2], mv[2])) for mv in MOVEMENTS},   # no U-turn column: null
            'approach_totals': {ap: got[(ap, 'total')] for ap in _DIR.values()},
            'stated_total': grand, 'peak_start': start, 'phf': None,
            'quarters': None, 'stated_hour': None,
        }
    return out


# ── file types ───────────────────────────────────────────────────────────────

def _cells(line):
    """PDF text line -> cells; leading words before the first number form one label."""
    toks = line.split()
    k = next((i for i, t in enumerate(toks) if _NUM.match(t.replace(',', ''))), None)
    return [' '.join(toks[:k])] + toks[k:] if k and k > 1 else toks


def _vendor(text):
    t = text[:5000].lower()
    for key, name in (('national data', 'NDS'), ('quality counts', 'Quality Counts'),
                      ('counts unlimited', 'Counts Unlimited')):
        if key in t:
            return name
    return 'Unknown'


def _hv(cars, periods):
    """HV% = (all vehicles - cars) / all vehicles over the peak hour, when a Cars table exists."""
    out = {}
    for period, p in periods.items():
        total = sum(v or 0 for v in p['volumes'].values())
        rows = cars and read_checks.peak_quarters(cars[period]['quarters'], p['peak_start'])
        out[period] = round(100 * (total - sum(read_checks.quarter_total(r) for r in rows)) / total, 1) \
            if rows and total else None
    return out


def _read(path):
    ext = Path(path).suffix.lower()
    cars = None
    if ext in ('.xlsx', '.xls'):
        text, warnings = _read_excel(path)
        if text.startswith('ERROR'):
            raise IOError(text)
        parts = re.split(r'\n=== SHEET: (.+?) ===\n', text)
        sheets = dict(zip(parts[1::2], parts[2::2]))
        total = [n for n in sheets if 'total' in n.lower()
                 and not re.search(r'truck|bike|ped', n, re.I)] or list(sheets)[:1]
        periods = _tabular([r.split('\t') for r in sheets[total[0]].splitlines()]) if total else {}
        car = [n for n in sheets if re.search(r'\bcars?\b', n, re.I)]
        if car:
            try:
                cars = _tabular([r.split('\t') for r in sheets[car[0]].splitlines()])
            except _Unknown:
                pass
        layout, ftype = f'tabular, sheet "{total[0]}"' if total else '', 'excel'
    elif ext == '.csv':
        text, warnings = Path(path).read_text(encoding='utf-8', errors='replace'), []
        periods, layout, ftype = _tabular(csv.reader(text.splitlines())), 'tabular', 'csv'
    elif ext == '.pdf':
        import pdfplumber
        warnings, ftype = [], 'pdf'
        with pdfplumber.open(path) as pdf:
            if not any(page.chars for page in pdf.pages):
                return None, 'no text layer (a scan)'
            # x_tolerance 1: the default 3 glues close headers ('WU TOTAL' -> 'WUTOTAL')
            text = '\n'.join(page.extract_text(x_tolerance=1) or '' for page in pdf.pages)
            if 'COUNTS UNLIMITED' in text and 'TOTAL ENTERING' in text and len(pdf.pages) == 1:
                periods, layout = _diagram(pdf.pages[0]), 'diagram (Forger style D)'
            else:
                periods, layout = _tabular(_cells(l) for l in text.splitlines()), 'tabular'
    else:
        return None, f'unknown layout: {ext or "no"} file type'
    if not periods:
        raise _Unknown('no count table found')

    names = set(re.findall(r'Location:\s*(.+?)(?:\s+City:|\t|\n|$)', text))
    if len(names) > 1:
        # shortcut: one intersection per file; multi-intersection files go to the cloud.
        raise _Unknown(f'{len(names)} intersections in one file')
    for period, p in periods.items():
        ok, why = read_checks.trusted(read_checks.run(p, period))
        if not ok:
            return None, f'check failed ({period}): {why}'
    hv = _hv(cars, periods)
    result = {
        'vendor_detected': _vendor(text),
        'format_summary': f'1 intersection, {layout}, read by the free code reader',
        'intersections': [{
            'name': names.pop().strip() if names else Path(path).stem,
            'am_peak_start': periods['AM']['peak_start'],
            'pm_peak_start': periods['PM']['peak_start'],
            'volumes': {k: p['volumes'] for k, p in periods.items()},
            'phf': {k: p['phf'] for k, p in periods.items()},
            'hv_pct': hv,
            'hv_method': '(all vehicles - cars) / all vehicles, peak hour' if any(hv.values()) else
                         'no heavy-vehicle data read',
            'notes': 'Free code reader. Every code check passed; confirm each number.',
        }],
        'file_type': ftype,
    }
    if layout.startswith('diagram'):
        # A diagram prints only approach totals, so a left/right swap inside one approach passes every check.
        for k, p in periods.items():
            v = p['volumes']
            pairs = ', '.join(f"{_WORD[d]} left {v[d + 'L']} / right {v[d + 'R']}"
                              for d in ('NB', 'SB', 'EB', 'WB') if v.get(d + 'L') is not None)
            warnings.append(f'{k}: diagram sheet, so left and right turns could not be checked against a total. '
                            f'Confirm them against the sheet: {pairs}.')
    if warnings:
        result['warnings'] = warnings
    return result, f'{layout}; all checks passed'


def read(path):
    """(result, reason). result is None when the cloud reader must read the file."""
    try:
        return _read(path)
    except _Unknown as e:
        return None, f'unknown layout: {e}'
    except IOError:
        raise                                         # unreadable file: same error as the cloud path
    except Exception as e:                            # noqa: BLE001 — a reader bug must not block the cloud
        return None, f'free reader error: {type(e).__name__}: {e}'
