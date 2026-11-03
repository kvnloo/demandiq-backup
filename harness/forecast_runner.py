"""Forecast track — one Examiner cycle for one forecast puzzle.

Flow: Forecast Puzzler (hidden true future) → [noisy tiers: the tool's own
/balance-nchrp balances the counts] → POST /forecast/run → probes
(idempotency, 90° rotation) → grade against TWO targets → Scorekeeper.

  1. The textbook answer (forecast_oracle): the tool must reproduce the
     guideline arithmetic. A mismatch is a FAIL — that's a bug.
       link step   ratio / difference / average from the model    → forecast-link-math
       turn step   Furness of today's turns to those leg totals   → forecast-turn-math
       output      ≥ 0, Table 4-8 rounding, roads conserve         → forecast-invariant
     Tier FA (perfect model) adds: the link step must equal the TRUE future.
  2. The hidden true future: how close the forecast lands (share of
     movements within GEH 5). Not the tool's fault when the model is wrong,
     so it is a SCORE (forecast_scores.csv, Telegram checkpoints) — and a
     MARGINAL only in tier FA, where the model is perfect.
Verdicts are code only. No LLM.
"""

import csv
import json

from . import forecast_oracle as fo
from . import forecast_puzzler as fp
from . import grader, operate, scorekeeper
from .nchrp_math import STANDARD_MOVEMENTS, geh, link_conservation_entry

SCORES_CSV = scorekeeper.HARNESS_ROOT / 'forecast_scores.csv'
SCORE_FIELDS = ['run_id', 'timestamp', 'git_sha', 'seed', 'tier', 'scenario', 'n',
                'link_method', 'turn_method', 'verdict', 'geh5_pct', 'worst_geh',
                'road_error_pct', 'turn_error_pct']
FA_GEH5_MIN = 0.85          # FHWA calibration target: 85 % of movements within GEH 5
ORDER = ('pipeline-crash', 'forecast-invariant', 'forecast-link-math',
         'forecast-turn-math', 'unknown', 'forecast-accuracy')


def _check(name, category, bad):
    return {'check': name, 'category': category, 'ok': not bad, 'details': bad[:40]}


def _int_keys(d):
    return {int(k): v for k, v in (d or {}).items()}


def _balance(world, base_url):
    """Noisy tiers: the counts go through the tool's own balancer first, as in the app."""
    raw = world['raw_counts']
    payload = {
        'intersections': [{'num': i, 'name': f'Int {i}', **world['coords'][i]} for i in raw],
        'unbalanced_volumes': {p: {str(i): v for i, v in raw.items()} for p in ('AM', 'PM')},
        'links': world['links'], 'link_resolution_method': 'larger',
        'max_iterations': 100, 'tolerance': 0.5,
    }
    out, _ = operate._post_json(f'{base_url}/balance-nchrp', payload)
    return {int(i): {m: float(v) for m, v in mv.items() if m in STANDARD_MOVEMENTS}
            for i, mv in out['balanced']['AM'].items()}


def grade(world, counts, am, am2_raw, am_raw, am_rot):
    checks, n = [], world['n']
    refinement = {r['intersection_num']: r for r in am['refinement'] or []}

    # 1a. link step vs the guideline formulas (and vs truth in FA)
    oracle = fo.leg_targets(world, counts)
    bad_oracle, bad_truth, road_err = [], [], []
    for (i, d), (o_in, o_out) in oracle.items():
        leg = refinement[i]['approaches'][d]
        for side, want, truth in (('in', o_in, world['truth_legs'][(i, d)][0]),
                                  ('out', o_out, world['truth_legs'][(i, d)][1])):
            got = leg[f'{side}bound' if side == 'in' else 'outbound']['refined']
            if truth > 0:
                road_err.append(abs(got - truth) / truth)
            if want is not None and abs(got - want) > max(0.5, 1e-4 * want):
                bad_oracle.append(f'int {i} {d}-leg {side}: guideline={want:.1f} tool={got:.1f}')
            if world['tier'] == 'FA' and abs(got - truth) > max(0.5, 1e-3 * truth):
                bad_truth.append(f'int {i} {d}-leg {side}: true future={truth:.1f} tool={got:.1f}')
    checks.append(_check('F-link-guideline', 'forecast-link-math', bad_oracle))
    if world['tier'] == 'FA':
        checks.append(_check('F-link-perfect-model', 'forecast-link-math', bad_truth))

    # 1b. turn step: Furness to the tool's own leg totals (furness method only)
    if world['turn_method'] == 'furness':
        p1 = _int_keys(am['pass1']['futures'])
        bad = []
        for i in range(1, n + 1):
            appr = refinement[i]['approaches']
            rows = {d: appr[d]['inbound']['refined'] for d in fo.DIRS}
            cols = {d: appr[d]['outbound']['refined'] for d in fo.DIRS}
            want = fo.furness(counts[i], rows, cols)
            for m, w in want.items():
                g = float(p1[i].get(m, 0) or 0)
                # Furness stops once every factor is within ~1 % (NCHRP 255), so allow 1 %
                if abs(g - w) > max(2.0, 0.01 * w):
                    bad.append(f'int {i} {m}: Furness={w:.1f} tool={g:.1f}')
        checks.append(_check('F-furness', 'forecast-turn-math', bad))

    # 1c. delivered output
    out = _int_keys(am['pass2']['balanced'])
    unr = _int_keys(am['pass2']['balanced_unrounded'])
    bad = [f'int {i} {m}={v}' for i, mv in out.items() for m, v in mv.items()
           if m in STANDARD_MOVEMENTS and (v < 0 or not fo.allowed_rounded(v))]
    checks.append(_check('F-rounding-nonneg', 'forecast-invariant', bad))
    bad = []
    for a in range(1, n):
        for u, dn, d in ((a, a + 1, 'EB'), (a + 1, a, 'WB')):
            e = link_conservation_entry(unr, u, dn, d)
            if abs(e['delta']) > 1.0:
                bad.append(f'{u}→{dn} {d}: out {e["upstream_outflow"]:.1f} in {e["downstream_inflow"]:.1f}')
    checks.append(_check('F-roads-conserve', 'forecast-invariant', bad))

    # probes
    checks.append(_check('F-probe-idempotency', 'unknown',
                         [] if am_raw == am2_raw else ['same request twice gave different answers']))
    rot = fp.unrotate(_int_keys(am_rot['pass2']['balanced']))
    bad = [f'int {i} {m}: EW={out[i][m]} NS={rot[i].get(m)}' for i in out for m in STANDARD_MOVEMENTS
           if out[i].get(m) != rot[i].get(m)]
    checks.append(_check('F-probe-orientation', 'unknown', bad))   # F-: own signatures, not the balancer's

    # 2. the hidden true future
    rows = []
    for i in range(1, n + 1):
        for m in STANDARD_MOVEMENTS:
            t, g = world['truth_future'][i][m], float(out[i].get(m, 0) or 0)
            rows.append({'period': 'AM', 'int_num': i, 'movement': m, 'key': round(t, 1),
                         'tool': g, 'geh': geh(g, t)})
    rows.sort(key=lambda r: -r['geh'])
    geh5 = sum(r['geh'] < 5 for r in rows) / len(rows)
    tot = sum(r['key'] for r in rows)
    turn_err = sum(abs(r['tool'] - r['key']) for r in rows) / tot if tot else 0.0
    failed = [c for c in checks if not c['ok']]
    if failed:
        verdict, category = 'FAIL', min((c['category'] for c in failed), key=ORDER.index)
    elif world['tier'] == 'FA' and geh5 < FA_GEH5_MIN:
        verdict, category = 'MARGINAL', 'forecast-accuracy'
    else:
        verdict, category = 'PASS', None
    return {
        'verdict': verdict, 'failure_category': category, 'checks': checks,
        'geh': {'worst': rows[0]['geh'], 'worst_movement': f"AM int {rows[0]['int_num']} {rows[0]['movement']}",
                'n_over_marginal': sum(r['geh'] > 10 for r in rows),
                'n_marginal': sum(5 < r['geh'] <= 10 for r in rows), 'rows': rows[:30]},
        'signature': grader.compute_signature(category, failed, []),
        'score': {'geh5_pct': round(100 * geh5, 1),
                  'road_error_pct': round(100 * sum(road_err) / max(1, len(road_err)), 2),
                  'turn_error_pct': round(100 * turn_err, 2)},
    }


def _narration(g, world):
    bad = [c['check'] for c in g['checks'] if not c['ok']]
    return {'category': g['failure_category'], 'narrated_by': 'fallback',
            'explanation': (f"Forecast {world['link_method']} → {world['turn_method']}, "
                            f"{world['scenario']}, {world['n']} intersections. Failed: {', '.join(bad) or 'none'}. "
                            f"{g['score']['geh5_pct']}% of movements within GEH 5 of the true future.")}


def execute_forecast_run(seed, base_url=operate.DEFAULT_BASE_URL, tier='FB', git_sha='unknown'):
    world = fp.generate(seed, tier)
    meta = {'run_id': scorekeeper.make_run_id(seed), 'seed': seed, 'git_sha': git_sha,
            'n': world['n'], 'orientation': 'EW', 'tier': 0.0 if tier == 'FA' else 0.05,
            'difficulty': tier, 'base_url': base_url, 'harness_version': '1.1.0-forecast'}
    artifacts = {'forecast_world': {k: world[k] for k in ('tier', 'n', 'scenario', 'link_method',
                                                         'turn_method', 'model_years')}}
    try:
        counts = world['counts'] or _balance(world, base_url)
        payload = fp.forecast_payload(world, counts)
        r1, raw1 = operate._post_json(f'{base_url}/forecast/run', payload)
        _, raw2 = operate._post_json(f'{base_url}/forecast/run', payload)
        r3, _ = operate._post_json(f'{base_url}/forecast/run', fp.rotate_payload(payload))
        g = grade(world, counts, r1['AM'], raw2, raw1, r3['AM'])
    except operate.PipelineError as e:
        g = {'verdict': 'FAIL', 'failure_category': 'pipeline-crash',
             'checks': [_check('pipeline', 'pipeline-crash', [str(e)[:300]])],
             'geh': {'worst': 0.0, 'worst_movement': '', 'n_over_marginal': 0, 'n_marginal': 0, 'rows': []},
             'signature': '', 'score': {'geh5_pct': '', 'road_error_pct': '', 'turn_error_pct': ''}}
        g['signature'] = grader.compute_signature('pipeline-crash', g['checks'], [])
        artifacts['pipeline_error'] = {'stage': e.stage, 'detail': e.detail}
    artifacts['grade'] = {k: v for k, v in g.items() if k != 'score'}
    row = scorekeeper.record_run(meta, g, _narration(g, world), artifacts)
    _append_score(row, world, g)
    return row


def _append_score(row, world, g):
    new = not SCORES_CSV.exists()
    with open(SCORES_CSV, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
        if new:
            w.writeheader()
        w.writerow({'run_id': row['run_id'], 'timestamp': row['timestamp'], 'git_sha': row['git_sha'],
                    'seed': world['seed'], 'tier': world['tier'], 'scenario': world['scenario'],
                    'n': world['n'], 'link_method': world['link_method'],
                    'turn_method': world['turn_method'], 'verdict': row['verdict'],
                    'geh5_pct': g['score']['geh5_pct'], 'worst_geh': row['worst_geh'],
                    'road_error_pct': g['score']['road_error_pct'],
                    'turn_error_pct': g['score']['turn_error_pct']})


def score_summary(rows=None):
    """Per (link → turn) method over the score log: runs, mean share within
    GEH 5, mean movement error % vs the true future. For checkpoints."""
    if rows is None:
        if not SCORES_CSV.exists():
            return {}
        with open(SCORES_CSV) as f:
            rows = list(csv.DictReader(f))
    agg = {}
    for r in rows:
        if r.get('geh5_pct') in ('', None):
            continue
        k = f"{r['link_method']}→{r['turn_method']}"
        a = agg.setdefault(k, [0, 0.0, 0.0])
        a[0] += 1
        a[1] += float(r['geh5_pct'])
        a[2] += float(r.get('turn_error_pct') or 0)
    return {k: (c, s / c, e / c) for k, (c, s, e) in agg.items()}


def checkpoint_lines(session_rows):
    """Two short plain lines for Telegram: forecast pass rate and accuracy."""
    rows = [r for r in session_rows if str(r.get('tier', '')).startswith('F')]
    if not rows:
        return []
    ok = sum(r['verdict'] == 'PASS' for r in rows)
    lines = [f"Forecast tests: {ok}/{len(rows)} pass ({100.0 * ok / len(rows):.0f}%)"]
    s = score_summary()
    if s:
        best = sorted(s.items(), key=lambda kv: kv[1][2])[:3]
        lines.append('Closest to the true future: ' + ' · '.join(
            f"{k} off {e:.1f}% ({c})" for k, (c, _g, e) in best))
    return lines
