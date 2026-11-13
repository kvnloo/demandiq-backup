"""Code checks that decide whether a free (code) reading of a count file can be
trusted. Each check returns (ok, reason): ok is True, False, or None when the
sheet has nothing for that check to test. Any False sends the file to the
cloud reader, and so does a reading where no printed number ties out (trusted).

One period of one intersection is a dict:
  volumes          {movement: int|None}  the peak-hour values the reader returns
  peak_start       'HH:MM' or None
  phf              float or None
  quarters         [{'time': 'HH:MM', 'vols': {mv: v}, 'total': int|None}]  15-min rows
  stated_hour      {movement: v} the sheet's printed peak-hour row, or None
  stated_total     the sheet's printed peak-hour grand total, or None
  approach_totals  {'NB': v, ...} printed approach totals, or None
"""

WINDOWS = {'AM': ('06:00', '10:00'), 'PM': ('14:00', '19:00')}


def minutes(t):
    h, m = t.split(':')
    return int(h) * 60 + int(m)


def hhmm(mins):
    return f'{mins // 60:02d}:{mins % 60:02d}'


def quarter_total(q):
    return q['total'] if q.get('total') is not None else sum(v or 0 for v in q['vols'].values())


def peak_quarters(quarters, start):
    """The four 15-min rows of the hour starting at `start`, or None if any is missing."""
    by_time = {minutes(q['time']): q for q in quarters}
    s = minutes(start)
    rows = [by_time.get(s + 15 * i) for i in range(4)]
    return rows if all(rows) else None


def busiest_starts(quarters, period):
    """Every start (ties included) of the busiest 4 consecutive quarters inside the window."""
    lo, hi = (minutes(t) for t in WINDOWS[period])
    sums = {}
    for q in quarters:
        s, rows = minutes(q['time']), peak_quarters(quarters, q['time'])
        if lo <= s and s + 60 <= hi and rows:
            sums[s] = sum(quarter_total(r) for r in rows)
    best = max(sums.values(), default=None)
    return [hhmm(s) for s, v in sorted(sums.items()) if v == best]


def check_values(p):
    vols = p['volumes']
    if all(v is None for v in vols.values()):
        return False, 'no volumes read'
    bad = [mv for mv, v in vols.items()
           if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0)]
    return (False, f'not a whole non-negative number: {", ".join(bad)}') if bad \
        else (True, 'all whole, non-negative')


def check_quarter_rows(p):
    rows = [q for q in p.get('quarters') or [] if q.get('total') is not None]
    if not rows:
        return None, 'no 15-min row totals printed'
    bad = [q['time'] for q in rows if sum(v or 0 for v in q['vols'].values()) != q['total']]
    return (False, f'15-min row does not add to its total at {", ".join(bad)}') if bad \
        else (True, f'{len(rows)} 15-min rows add to their totals')


def check_hour_sum(p):
    """The four peak 15-min rows add up to the printed peak-hour row."""
    if not p.get('quarters') or not p.get('stated_hour'):
        return None, 'no printed peak-hour row and 15-min rows to compare'
    rows = peak_quarters(p['quarters'], p['peak_start'])
    if not rows:
        return False, f'15-min rows missing inside the peak hour starting {p["peak_start"]}'
    bad = [mv for mv, v in p['stated_hour'].items()
           if v is not None and sum(r['vols'].get(mv) or 0 for r in rows) != v]
    return (False, f'15-min rows do not add to the peak hour for {", ".join(bad)}') if bad \
        else (True, '15-min rows add to the peak hour')


def check_totals(p):
    """Approach totals match their movements; the grand total matches all movements."""
    vols, bad, what = p['volumes'], [], []
    for ap, total in (p.get('approach_totals') or {}).items():
        got = sum(vols.get(f'{ap}{t}') or 0 for t in 'ULTR')
        if got != total:
            bad.append(f'{ap} movements add to {got}, sheet total {total}')
    if p.get('approach_totals'):
        what.append('approach totals')
    if p.get('stated_total') is not None:
        got = sum(v or 0 for v in vols.values())
        if got != p['stated_total']:
            bad.append(f'movements add to {got}, sheet grand total {p["stated_total"]}')
        what.append('grand total')
    if not what:
        return None, 'no printed totals'
    return (False, '; '.join(bad)) if bad else (True, f'movements match the {" and ".join(what)}')


def check_peak_hour(p, period):
    if not p.get('quarters'):
        return None, 'no 15-min rows to find the peak hour from'
    starts = busiest_starts(p['quarters'], period)
    if not starts:
        return False, f'no full hour of 15-min rows inside the {period} window'
    if p['peak_start'] not in starts:
        return False, f'peak hour {p["peak_start"]} is not the busiest hour ({starts[0]})'
    return True, f'peak hour {p["peak_start"]} is the busiest 4 quarters'


def check_phf(p):
    if p.get('phf') is None:
        return None, 'no PHF'
    return (True, 'PHF in 0.25-1.0') if 0.25 <= p['phf'] <= 1.0 \
        else (False, f'PHF {p["phf"]} outside 0.25-1.0')


# Checks that tie the volumes to a number printed separately on the sheet.
_TIE_OUTS = ('15-min rows', 'peak hour sum', 'totals')


def run(p, period):
    """All checks for one period: [(name, ok, reason)]."""
    return [
        ('values', *check_values(p)),
        ('15-min rows', *check_quarter_rows(p)),
        ('peak hour sum', *check_hour_sum(p)),
        ('totals', *check_totals(p)),
        ('peak hour', *check_peak_hour(p, period)),
        ('PHF', *check_phf(p)),
    ]


def trusted(results):
    """(ok, reason). Every check passes or does not apply, and at least one printed
    number ties out; otherwise nothing independent confirms the reading."""
    failed = [f'{n}: {r}' for n, ok, r in results if ok is False]
    if failed:
        return False, failed[0]
    if not any(ok for n, ok, _ in results if n in _TIE_OUTS):
        return False, 'nothing printed on the sheet to check the numbers against'
    return True, f'{sum(1 for _, ok, _ in results if ok)} checks passed'
