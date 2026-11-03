"""Forecast Puzzler — builds a corridor whose TRUE future is known, then hands
the tool only what an engineer would have: today's counts and a regional model.

Harness-owned and independent of backend/forecasting (no imports from it): the
trip table, the path assignment and the model are re-derived here, so a bug in
the tool's own benchmark can't hide a bug in the tool.

World (straight east–west corridor, intersections 1..n, numbered west → east):
  zones   = every side-street leg (south / north of each intersection) plus the
            two corridor ends (west of 1, east of n)
  trips   = gravity table between zones; today's movements = trips assigned
            along the corridor; the future = the same trips grown per scenario
  model   = a regional model's link volumes for a 4-hour period, at two model
            years (2015, 2050), linear in time; peak hour = K × period

Tiers (the 'tier' column; F = forecast track):
  FA  perfect model: model ∝ truth, counts exact. The link step has ONE right
      answer — the true future road volumes — and the tool must hit it.
  FB  realistic: model off ±25 % per link, growth off ±8 %, counts off ±5 %
      (balanced through the tool's own /balance-nchrp first).
  FC  hard: FB plus declines, a new development, model years far from the
      count/horizon years, 3–7 intersections.
Deterministic per (seed, tier). Stdlib only.
"""

import math
import random

from .nchrp_math import DIRECTION_SPEC, STANDARD_MOVEMENTS

FORECAST_TIERS = ('FA', 'FB', 'FC')
DEFAULT_FORECAST_MIX = {'FA': 30, 'FB': 40, 'FC': 30}
K = 0.30                                   # peak hour ÷ 4-hour model period
COUNT_YEAR, HORIZON = 2025, 2045
LINK_METHODS = ('ratio', 'difference', 'combined')      # oracle-checkable
EXTRA_LINK_METHODS = ('weighted', 'modified_average', 'odot', 'nchrp765')
TURN_METHODS = ('furness', 'fratar', 'fuzzy')
_OPP = {'NB': 'SB', 'SB': 'NB', 'EB': 'WB', 'WB': 'EB'}


def tier_for_seed(seed, mix=None):
    mix = mix or DEFAULT_FORECAST_MIX
    tiers = sorted(mix)
    rng = random.Random(f'examiner-forecast-tier:{seed}')
    return rng.choices(tiers, weights=[mix[t] for t in tiers])[0]


# ── trips and their movements ───────────────────────────────────────────────

def _zones(n):
    z = [('W',), ('E',)]
    for i in range(1, n + 1):
        z += [('S', i), ('N', i)]
    return z


def _path(o, d, n):
    """Movements [(int, code)] a trip o → d uses. None if not a corridor trip."""
    if o == d:
        return None
    pos = lambda z: 0 if z[0] == 'W' else (n + 1 if z[0] == 'E' else z[1])  # noqa: E731
    a, b = pos(o), pos(d)
    if a == b:                                       # side street straight across
        return [(a, 'NBT' if o[0] == 'S' else 'SBT')]
    east = b > a
    mv = []
    if o[0] in ('S', 'N'):                           # turn onto the corridor
        code = {('S', True): 'NBR', ('S', False): 'NBL',
                ('N', True): 'SBL', ('N', False): 'SBR'}[(o[0], east)]
        mv.append((a, code))
        start = a + 1 if east else a - 1
    else:
        start = 1 if o[0] == 'W' else n
    head = 'EB' if east else 'WB'
    i = start
    while True:
        if i < 1 or i > n:                           # left the corridor at an end
            return mv
        if i == b:                                   # turn off at the destination
            code = {('EB', 'N'): 'EBL', ('EB', 'S'): 'EBR',
                    ('WB', 'N'): 'WBR', ('WB', 'S'): 'WBL'}[(head, d[0])]
            mv.append((i, code))
            return mv
        mv.append((i, head + 'T'))
        i += 1 if east else -1


def _assign(n, table):
    vols = {i: {m: 0.0 for m in STANDARD_MOVEMENTS} for i in range(1, n + 1)}
    for (o, d), v in table.items():
        for i, m in _path(o, d, n):
            vols[i][m] += v
    return vols


def _legs(n):
    return [(i, d) for i in range(1, n + 1) for d in ('NB', 'SB', 'EB', 'WB')]


def leg_in(vols, i, d):
    return sum(vols[i][m] for m in DIRECTION_SPEC[d]['approach'])


def leg_out(vols, i, d):
    """Traffic leaving intersection i through leg d (the leg vehicles approaching
    in direction d arrive from)."""
    return sum(vols[i][m] for m in DIRECTION_SPEC[_OPP[d]]['outflow'])


# ── the world ───────────────────────────────────────────────────────────────

def generate(seed, tier='FB'):
    if tier not in FORECAST_TIERS:
        raise ValueError(f'unknown forecast tier {tier!r}')
    rng = random.Random(f'examiner-forecast:{tier}:{seed}')
    n = rng.randint(3, 7) if tier == 'FC' else rng.randint(3, 5)
    scenario = rng.choice(('steady', 'decline', 'develop') if tier == 'FC' else ('steady',))
    link_method = rng.choice(LINK_METHODS if tier == 'FA' else LINK_METHODS + EXTRA_LINK_METHODS)
    turn_method = rng.choice(TURN_METHODS)
    zones = _zones(n)
    size = {z: (rng.uniform(800, 1200) if z[0] in ('W', 'E') else rng.uniform(80, 450)) for z in zones}
    pos = lambda z: 0 if z[0] == 'W' else (n + 1 if z[0] == 'E' else z[1])  # noqa: E731
    base, fut = {}, {}
    grow = {z: (0.85 if (scenario == 'decline' and z[0] in ('W', 'E')) else rng.uniform(0.95, 1.30)) for z in zones}
    dev = rng.randint(1, n)
    for o in zones:
        for d in zones:
            if _path(o, d, n) is None:
                continue
            k = abs(pos(o) - pos(d))
            v = size[o] * size[d] / 900 * math.exp(-0.35 * k)
            base[(o, d)] = v
            g = math.sqrt(grow[o] * grow[d])
            if tier != 'FA':
                g *= math.exp(rng.gauss(0, 0.10))          # every trip its own growth
            f = v * g
            site = lambda z: z[0] in ('S', 'N') and z[1] == dev   # noqa: E731
            if scenario == 'develop' and (site(o) or site(d)):
                f += 60 * math.exp(-0.6 * k)               # new site trips, mostly short
            fut[(o, d)] = f
    true_base, true_fut = _assign(n, base), _assign(n, fut)

    # counts: exact in FA; ±5 % noise (to be balanced by the tool) otherwise
    if tier == 'FA':
        counts = {i: dict(v) for i, v in true_base.items()}
        raw = None
    else:
        raw = {i: {m: max(0, int(round(v * (1 + rng.gauss(0, 0.05))))) for m, v in mv.items()}
               for i, mv in true_base.items()}
        counts = None                                    # the runner balances raw first

    # regional model, linear in time between two model years
    yb, yf = (2015, 2050) if tier != 'FC' else rng.choice(((2012, 2040), (2016, 2050), (2020, 2045)))
    links, leg_link = [], {}
    def model_pair(v_c, v_h, key, bias, gerr):
        # v at count year / horizon (peak hour) → model at yb / yf (period), linear in time
        slope = (v_h - v_c) / (HORIZON - COUNT_YEAR)
        mb = max(1.0, v_c + slope * (yb - COUNT_YEAR)) / K
        mf = max(1.0, v_c + slope * (yf - COUNT_YEAR)) / K
        b, g = bias.setdefault(key, (1.0, 1.0) if tier == 'FA' else
                               (math.exp(rng.gauss(0, 0.25)), math.exp(rng.gauss(0, 0.08))))
        return mb * b, mf * b * g
    bias = {}
    for i, d in _legs(n):
        if d in ('EB', 'WB') and not ((i == 1 and d == 'EB') or (i == n and d == 'WB')):
            continue                                     # corridor links added below
        lid = f'L{i}{d}'
        node = {'NB': i * 100 + 1, 'SB': i * 100 + 2, 'EB': 1000, 'WB': 2000}[d]
        ab = model_pair(leg_in(true_base, i, d), leg_in(true_fut, i, d), (lid, 'in'), bias, 0)
        ba = model_pair(leg_out(true_base, i, d), leg_out(true_fut, i, d), (lid, 'out'), bias, 0)
        links.append((lid, node, i, ab, ba))
        leg_link[(i, d)] = lid
    for a in range(1, n):
        lid = f'C{a}{a + 1}'
        ab = model_pair(leg_in(true_base, a + 1, 'EB'), leg_in(true_fut, a + 1, 'EB'), (lid, 'eb'), bias, 0)
        ba = model_pair(leg_in(true_base, a, 'WB'), leg_in(true_fut, a, 'WB'), (lid, 'wb'), bias, 0)
        links.append((lid, a, a + 1, ab, ba))
        leg_link[(a + 1, 'EB')] = lid
        leg_link[(a, 'WB')] = lid

    def layer(which, year):
        out = []
        for lid, fn, tn, ab, ba in links:
            xy = [[float(fn), 0.0], [float(tn), 0.0]]
            j = 0 if which == 'base' else 1
            out.append({'link_id': lid, 'from_node': fn, 'to_node': tn, 'geometry': xy,
                        'from_node_xy': xy[0], 'to_node_xy': xy[1], 'lanes': 2,
                        'flow': {'ab': {'AM': ab[j]}, 'ba': {'AM': ba[j]}}})
        return {'region': 'EXAMINER', 'source_crs_epsg': 4326, 'crs_epsg': 4326,
                'scenario_year': year, 'links': out}

    return {
        'seed': seed, 'tier': tier, 'n': n, 'scenario': scenario,
        'link_method': link_method, 'turn_method': turn_method,
        'model_years': (yb, yf), 'K': K,
        'coords': {i: {'lat': 37.58, 'lng': -122.03 + 0.005 * i} for i in range(1, n + 1)},
        'links': [{'fromNum': a, 'toNum': a + 1, 'direction': 'EB/WB', 'mode': 'Full'} for a in range(1, n)],
        'layers': {'base': layer('base', yb), 'horizon': layer('horizon', yf)},
        'matches': [{'intersection_num': i, 'snapped_node': i,
                     'legs': {d: {'status': 'matched', 'link_id': leg_link[(i, d)]}
                              for d in ('NB', 'SB', 'EB', 'WB')}} for i in range(1, n + 1)],
        'raw_counts': raw, 'counts': counts,
        'truth_future': true_fut,
        'truth_legs': {(i, d): (leg_in(true_fut, i, d), leg_out(true_fut, i, d)) for i, d in _legs(n)},
    }


def forecast_payload(world, counts):
    return {
        'layers': world['layers'], 'matches': world['matches'],
        'balanced_tmcs': {'AM': {str(i): v for i, v in counts.items()}},
        'periods': ['AM'], 'links': world['links'],
        'coords': {str(i): c for i, c in world['coords'].items()},
        'count_year': COUNT_YEAR, 'horizon_year': HORIZON,
        'refine_method': world['link_method'], 'turn_method': world['turn_method'],
        'peak_hour_factor': world['K'],
    }


# ── 90° rotation (east–west corridor → north–south) for the orientation probe ─
_ROT = {'EB': 'NB', 'NB': 'WB', 'WB': 'SB', 'SB': 'EB'}


def rotate_payload(p):
    q = dict(p)
    q['balanced_tmcs'] = {'AM': {i: {_ROT[m[:2]] + m[2:]: v for m, v in mv.items()}
                                 for i, mv in p['balanced_tmcs']['AM'].items()}}
    q['links'] = [{**l, 'direction': 'NB/SB'} for l in p['links']]
    q['coords'] = {i: {'lat': 37.58 + (c['lng'] + 122.03), 'lng': -122.03} for i, c in p['coords'].items()}
    q['matches'] = [{**m, 'legs': {_ROT[d]: v for d, v in m['legs'].items()}} for m in p['matches']]
    return q


def unrotate(vols):
    inv = {v: k for k, v in _ROT.items()}
    return {i: {inv[m[:2]] + m[2:]: v for m, v in mv.items()} for i, mv in vols.items()}
