"""Intersection-level look at the method comparison: where do methods disagree
with each other, and where does one go badly wrong vs the true future?

    python -m harness.method_anomalies            # reads method_compare_movements.json
Writes method_anomalies.html (white, phone-friendly) next to this file.

Anomaly = a delivered movement off the true future by more than GEH 10 (the
level calibration guidance treats as clearly wrong) AND by 50+ vehicles.
Disagreement = the 21 methods' answers for one movement span more than GEH 5.
"""

import html
import json
import statistics as st

from .method_compare import VOLS
from .nchrp_math import STANDARD_MOVEMENTS, geh

OUT = VOLS.parent / 'method_anomalies.html'
NAME = {'ratio': 'Ratio', 'difference': 'Difference', 'combined': 'Average', 'weighted': 'Weighted',
        'modified_average': 'Mod. average', 'odot': 'ODOT', 'nchrp765': 'NCHRP 765',
        'furness': 'NCHRP std', 'fratar': 'Fratar', 'fuzzy': 'Fuzzy'}
SHOW = ['ratio|furness', 'ratio|fratar', 'ratio|fuzzy', 'difference|furness', 'odot|furness', 'weighted|fuzzy']
WORD = {'L': 'left', 'T': 'through', 'R': 'right'}


def label(k):
    a, b = k.split('|')
    return f'{NAME[a]} → {NAME[b]}'


def move_name(m):
    road = 'main street' if m[:2] in ('EB', 'WB') else 'side street'
    return f"{m[:2]} {WORD[m[2]]} ({road})"


def analyse(data):
    per_method, kinds, spread_kinds, ints = {}, {}, {}, []
    for wid, w in data.items():
        methods = w['methods']
        for i in map(str, range(1, w['n'] + 1)):
            truth = w['truth'][i]
            rows, worst_spread = [], 0.0
            for m in STANDARD_MOVEMENTS:
                t = truth[m]
                vals = {k: float(v[i].get(m, 0) or 0) for k, v in methods.items()}
                lo, hi = min(vals.values()), max(vals.values())
                sp = geh(lo, hi)
                worst_spread = max(worst_spread, sp)
                if sp > 5:
                    key = (m[:2] in ('EB', 'WB'), m[2])
                    spread_kinds[key] = spread_kinds.get(key, 0) + 1
                for k, v in vals.items():
                    bad = geh(v, t) > 10 and abs(v - t) >= 50
                    c = per_method.setdefault(k, [0, 0, 0])
                    c[0] += 1
                    c[1] += bad
                    c[2] += abs(v - t) >= 50 and abs(v - t) > 0.25 * t     # off by a quarter
                    if bad:
                        key = (m[:2] in ('EB', 'WB'), m[2])
                        kinds[key] = kinds.get(key, 0) + 1
                rows.append((m, w['counts'][i].get(m, 0), t, vals, lo, hi, sp))
            ent_t = sum(truth[m] for m in STANDARD_MOVEMENTS)
            ent = {k: sum(float(v[i].get(m, 0) or 0) for m in STANDARD_MOVEMENTS) for k, v in methods.items()}
            ints.append({'world': wid, 'int': i, 'scenario': w['scenario'], 'rows': rows,
                         'spread': worst_spread, 'entering_truth': ent_t, 'entering': ent,
                         'worst_err': max(abs(r[3]['ratio|furness'] - r[2]) for r in rows)})
    return per_method, kinds, spread_kinds, ints


def text_summary(per_method, kinds, spread_kinds, ints):
    n_mv = next(iter(per_method.values()))[0]
    lines = [f'{len(ints)} intersections × 12 movements = {n_mv} movements per method.']
    lines.append('Badly wrong movements (GEH > 10 and 50+ vehicles off the true future):')
    for k, (tot, bad, _q) in sorted(per_method.items(), key=lambda kv: kv[1][1]):
        lines.append(f'  {label(k):28s} {bad:4d}  ({100 * bad / tot:.1f}%)')
    lines.append('Movements off by more than 25 % and 50+ vehicles:')
    for k, (tot, _b, q) in sorted(per_method.items(), key=lambda kv: kv[1][2]):
        lines.append(f'  {label(k):28s} {q:4d}  ({100 * q / tot:.1f}%)')
    lines.append('Where they happen (all methods): ' + ', '.join(
        f"{'main' if mainst else 'side'} {WORD[t]} {c}" for (mainst, t), c in
        sorted(kinds.items(), key=lambda kv: -kv[1])))
    big = [x for x in ints if x['spread'] > 5]
    lines.append(f'Intersections where the methods disagree by more than GEH 5 on some movement: '
                 f'{len(big)} of {len(ints)}')
    lines.append('Which movements disagree most: ' + ', '.join(
        f"{'main' if mainst else 'side'} {WORD[t]} {c}" for (mainst, t), c in
        sorted(spread_kinds.items(), key=lambda kv: -kv[1])))
    errs = {k: st.mean(abs(x['entering'][k] - x['entering_truth']) / x['entering_truth'] for x in ints)
            for k in per_method}
    lines.append('Total traffic entering each intersection, average error vs true future: ' + ', '.join(
        f'{label(k)} {100 * e:.1f}%' for k, e in sorted(errs.items(), key=lambda kv: kv[1])[:3])
        + ' … worst ' + ', '.join(f'{label(k)} {100 * e:.1f}%' for k, e in
                                  sorted(errs.items(), key=lambda kv: kv[1])[-2:]))
    return '\n'.join(lines)


def write_html(summary, ints):
    worst = sorted(ints, key=lambda x: -x['spread'])[:6]
    parts = ['<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">',
             '<title>Forecast methods — intersection check</title><style>',
             'body{font:15px/1.45 -apple-system,Segoe UI,sans-serif;background:#fff;color:#111;margin:16px;max-width:980px}',
             'h1{font-size:20px}h2{font-size:16px;margin-top:28px}pre{white-space:pre-wrap;background:#f6f6f6;padding:10px;border-radius:6px}',
             'table{border-collapse:collapse;font-size:13px;margin:6px 0 4px;display:block;overflow-x:auto}',
             'th,td{border:1px solid #ddd;padding:3px 6px;text-align:right;white-space:nowrap}th{background:#f0f0f0}',
             'td:first-child,th:first-child{text-align:left}.bad{background:#fde2e2;font-weight:600}.hi{background:#fff4cc}',
             '</style><h1>Forecast methods, checked intersection by intersection</h1>',
             '<p>Every method was run on the same test corridors, where the true 2045 volumes are known. '
             'Numbers are vehicles in the peak hour. <span class=bad>Red</span> = more than GEH 10 and 50+ vehicles '
             'off the true future. <span class=hi>Yellow</span> = the 21 methods disagree by more than GEH 5.</p>',
             f'<h2>Summary</h2><pre>{html.escape(summary)}</pre>',
             '<h2>The 6 intersections where the methods disagree most</h2>']
    for x in worst:
        parts.append(f"<p><b>Corridor {x['world']} · intersection {x['int']}</b> · {x['scenario']} · "
                     f"methods span up to GEH {x['spread']:.1f}</p><table><tr><th>Movement</th>"
                     '<th>Count today</th><th>True 2045</th>'
                     + ''.join(f'<th>{html.escape(label(k))}</th>' for k in SHOW)
                     + '<th>Range, all 21</th></tr>')
        for m, c, t, vals, lo, hi, sp in x['rows']:
            cells = ''.join(
                f"<td class={'bad' if geh(vals[k], t) > 10 and abs(vals[k] - t) >= 50 else ''}>{vals[k]:.0f}</td>"
                for k in SHOW)
            parts.append(f"<tr><td>{move_name(m)}</td><td>{float(c):.0f}</td><td>{t:.0f}</td>{cells}"
                         f"<td class={'hi' if sp > 5 else ''}>{lo:.0f}–{hi:.0f}</td></tr>")
        parts.append('</table>')
    OUT.write_text('\n'.join(parts))


def main():
    data = json.loads(VOLS.read_text())
    res = analyse(data)
    s = text_summary(*res)
    write_html(s, res[3])
    print(s)
    print(OUT)


if __name__ == '__main__':
    main()
