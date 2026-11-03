"""Fair method comparison: every forecast method on the SAME corridors.

    python -m harness.method_compare --seeds 30 --tiers FB,FC --base-url http://127.0.0.1:8100

For each puzzle (tiers FB/FC: realistic model and count errors) the counts are
balanced once, then all 7 road-growth methods × 3 turn methods are run on the
identical inputs and scored against the hidden true future. Records nothing in
the Examiner ledger. Writes method_compare.csv next to this file.
"""

import argparse
import csv
import json
import math
import statistics as st

from . import forecast_puzzler as fp
from . import forecast_runner as fr
from . import operate
from .nchrp_math import STANDARD_MOVEMENTS, geh

LINK = fp.LINK_METHODS + fp.EXTRA_LINK_METHODS
OUT = fr.scorekeeper.HARNESS_ROOT / 'method_compare.csv'
VOLS = fr.scorekeeper.HARNESS_ROOT / 'method_compare_movements.json'   # per-movement answers, for method_anomalies


def score(world, am):
    out = {int(k): v for k, v in am['pass2']['balanced'].items()}
    tr = world['truth_future']
    pairs = [(float(out[i].get(m, 0) or 0), tr[i][m]) for i in tr for m in STANDARD_MOVEMENTS]
    tot = sum(t for _, t in pairs)
    road = []
    for r in am['refinement']:
        for d, leg in r['approaches'].items():
            for side, j in (('inbound', 0), ('outbound', 1)):
                t = world['truth_legs'][(r['intersection_num'], d)][j]
                if t > 0:
                    road.append(abs(leg[side]['refined'] - t) / t)
    return {'turn_err': 100 * sum(abs(g - t) for g, t in pairs) / tot,
            'geh5': 100 * sum(geh(g, t) < 5 for g, t in pairs) / len(pairs),
            'road_err': 100 * sum(road) / len(road)}


def run(seeds, tiers, url, start):
    rows, vols = [], {}
    for tier in tiers:
        for seed in range(start, start + seeds):
            w = fp.generate(seed, tier)
            counts = fr._balance(w, url)
            vols[f'{tier}:{seed}'] = {'scenario': w['scenario'], 'n': w['n'], 'counts': counts,
                                      'truth': w['truth_future'], 'methods': {}}
            for lm in LINK:
                for tm in fp.TURN_METHODS:
                    p = {**fp.forecast_payload(w, counts), 'refine_method': lm, 'turn_method': tm}
                    am, _ = operate._post_json(f'{url}/forecast/run', p)
                    rows.append({'tier': tier, 'seed': seed, 'scenario': w['scenario'],
                                 'link': lm, 'turn': tm, **score(w, am['AM'])})
                    vols[f'{tier}:{seed}']['methods'][f'{lm}|{tm}'] = am['AM']['pass2']['balanced']
    VOLS.write_text(json.dumps(vols))
    return rows


def report(rows):
    lines = []
    worlds = sorted({(r['tier'], r['seed']) for r in rows})
    by = {}
    for r in rows:
        by.setdefault((r['link'], r['turn']), {})[(r['tier'], r['seed'])] = r
    base = by[('ratio', 'furness')]
    wins = {k: 0 for k in by}
    for w in worlds:
        wins[min(by, key=lambda k: by[k][w]['turn_err'])] += 1
    lines.append(f'{len(worlds)} corridors, every method on the same ones. '
                 'Error = average movement error vs the true future (lower is better).')
    lines.append('vs Ratio→NCHRP standard: paired difference ± 2 standard errors (negative = better)')
    ranked = sorted(by, key=lambda k: st.mean(v['turn_err'] for v in by[k].values()))
    for k in ranked:
        e = [by[k][w]['turn_err'] for w in worlds]
        d = [by[k][w]['turn_err'] - base[w]['turn_err'] for w in worlds]
        se = st.stdev(d) / math.sqrt(len(d)) if len(d) > 1 and any(d) else 0.0
        g = st.mean(by[k][w]['geh5'] for w in worlds)
        lines.append(f'{k[0]:>16s} → {k[1]:<8s} error {st.mean(e):5.2f}%  '
                     f'Δ {st.mean(d):+5.2f} ± {2 * se:4.2f}  GEH5 {g:5.1f}%  best in {wins[k]}')
    lines.append('')
    lines.append('Road step alone (link forecast error vs true future road volumes):')
    for lm in sorted(LINK, key=lambda m: st.mean(by[(m, 'furness')][w]['road_err'] for w in worlds)):
        lines.append(f'{lm:>16s}  {st.mean(by[(lm, "furness")][w]["road_err"] for w in worlds):5.2f}%')
    lines.append('')
    lines.append('Best pair per scenario:')
    for sc in sorted({r['scenario'] for r in rows}):
        ws = [w for w in worlds if base[w]['scenario'] == sc]
        k = min(by, key=lambda k: st.mean(by[k][w]['turn_err'] for w in ws))
        lines.append(f'  {sc:8s} ({len(ws)}): {k[0]} → {k[1]} '
                     f'{st.mean(by[k][w]["turn_err"] for w in ws):.2f}% '
                     f'(Ratio→NCHRP {st.mean(base[w]["turn_err"] for w in ws):.2f}%)')
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--seeds', type=int, default=30)
    p.add_argument('--start-seed', type=int, default=800000)
    p.add_argument('--tiers', default='FB,FC')
    p.add_argument('--base-url', default=operate.DEFAULT_BASE_URL)
    a = p.parse_args(argv)
    rows = run(a.seeds, a.tiers.split(','), a.base_url, a.start_seed)
    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(report(rows))


if __name__ == '__main__':
    main()
