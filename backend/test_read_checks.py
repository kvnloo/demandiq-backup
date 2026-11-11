"""Unit tests for read_checks: one hand-made good sheet, then one broken copy per check.

    cd backend && python3.12 test_read_checks.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import read_checks as rc                                          # noqa: E402

MVS = ['EBU', 'EBL', 'EBT', 'EBR', 'WBU', 'WBL', 'WBT', 'WBR',
       'NBU', 'NBL', 'NBT', 'NBR', 'SBU', 'SBL', 'SBT', 'SBR']


def _good():
    """AM, five 15-min rows 07:00-08:00; the busiest hour starts 07:15."""
    base = {mv: 0 for mv in MVS}
    quarters = []
    for t, k in (('07:00', 1), ('07:15', 2), ('07:30', 3), ('07:45', 3), ('08:00', 2)):
        v = dict(base, NBT=10 * k, SBT=5 * k, EBL=k)
        quarters.append({'time': t, 'vols': v, 'total': sum(v.values())})
    hour = {mv: sum(q['vols'][mv] for q in quarters[1:]) for mv in MVS}
    return {
        'volumes': hour, 'peak_start': '07:15',
        'phf': round(sum(hour.values()) / (4 * 48), 3),
        'quarters': quarters, 'stated_hour': dict(hour),
        'stated_total': sum(hour.values()),
        'approach_totals': {'NB': 100, 'SB': 50, 'EB': 10, 'WB': 0},
    }


def _failed(p):
    return {n for n, ok, _ in rc.run(p, 'AM') if ok is False}


def test_good_sheet_passes():
    res = rc.run(_good(), 'AM')
    assert all(ok for _, ok, _ in res), res
    assert rc.trusted(res)[0]


def test_each_bad_sheet_fails_its_check():
    p = _good(); p['quarters'][2]['vols']['NBT'] += 1            # misread 15-min cell
    assert _failed(p) >= {'15-min rows', 'peak hour sum'}
    p = _good(); p['volumes']['NBT'] += 10                      # misread hour value
    assert 'totals' in _failed(p)
    p = _good(); p['volumes']['NBL'], p['volumes']['NBT'] = 100, 0   # L/T swapped: approach still adds up
    assert 'totals' not in _failed(p) and _failed(p) == set()   # why the T template is graded on the Forger
    p = _good(); p['approach_totals']['SB'] = 51
    assert 'totals' in _failed(p)
    p = _good(); p['peak_start'] = '07:00'
    assert 'peak hour' in _failed(p)
    p = _good(); p['phf'] = 1.2
    assert _failed(p) == {'PHF'}
    p = _good(); p['volumes']['EBT'] = -3
    assert 'values' in _failed(p)
    p = _good(); p['volumes']['EBT'] = 2.5
    assert 'values' in _failed(p)


def test_nothing_to_tie_out_is_not_trusted():
    p = _good()
    p.update(quarters=None, stated_hour=None, stated_total=None, approach_totals=None)
    ok, why = rc.trusted(rc.run(p, 'AM'))
    assert not ok and 'nothing printed' in why


def test_peak_hour_outside_window_fails():
    p = _good()
    for q in p['quarters']:
        q['time'] = '1' + q['time'][1:]                          # 17:xx rows read as AM
    assert 'peak hour' in _failed(p)


if __name__ == '__main__':
    tests = [v for k, v in dict(globals()).items() if k.startswith('test_')]
    for t in tests:
        t()
    print(f'test_read_checks: {len(tests)} passed')
