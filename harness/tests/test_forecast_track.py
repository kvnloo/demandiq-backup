"""Offline tests for the forecast track's own parts (no backend needed).

    python -m pytest harness/tests/test_forecast_track.py -q
"""

import pytest

from harness import forecast_oracle as fo
from harness import forecast_puzzler as fp
from harness.loop import is_forecast_seed
from harness.nchrp_math import link_conservation_entry


@pytest.mark.parametrize('tier', fp.FORECAST_TIERS)
@pytest.mark.parametrize('seed', range(1, 9))
def test_true_future_conserves_on_every_road(seed, tier):
    w = fp.generate(seed, tier)
    for a in range(1, w['n']):
        for u, d, dr in ((a, a + 1, 'EB'), (a + 1, a, 'WB')):
            assert abs(link_conservation_entry(w['truth_future'], u, d, dr)['delta']) < 1e-6


def test_every_trip_has_a_path_and_no_uturns():
    for n in (3, 5, 7):
        zones = fp._zones(n)
        for o in zones:
            for d in zones:
                p = fp._path(o, d, n)
                assert (p is None) == (o == d)
                if p:
                    assert all(m[2] in 'LTR' for _, m in p)


@pytest.mark.parametrize('seed', range(1, 9))
def test_perfect_model_guideline_answer_is_the_true_future(seed):
    # Tier FA's promise: with a perfect model the textbook link step gives the
    # true future exactly — so the tool has exactly one right answer.
    w = fp.generate(seed, 'FA')
    for (i, d), (t_in, t_out) in fo.leg_targets(w, w['counts']).items():
        want_in, want_out = w['truth_legs'][(i, d)]
        assert t_in == pytest.approx(want_in, rel=1e-9)
        assert t_out == pytest.approx(want_out, rel=1e-9)


def test_oracle_formulas_on_the_odot_example():
    # ODOT APM v2 ch. 6: count 1,690; model 1,195 → 1,390 (same years as count/horizon)
    yrs = (fp.COUNT_YEAR, fp.HORIZON)
    assert fo.link_target(1690, 1195, 1390, yrs, 'ratio', 1.0) == pytest.approx(1965.7, abs=0.1)
    assert fo.link_target(1690, 1195, 1390, yrs, 'difference', 1.0) == pytest.approx(1885)
    assert fo.link_target(1690, 1195, 1390, yrs, 'combined', 1.0) == pytest.approx(1925.4, abs=0.1)


def test_negative_difference_falls_back_to_ratio():
    yrs = (fp.COUNT_YEAR, fp.HORIZON)
    assert fo.link_target(50, 1000, 400, yrs, 'difference', 1.0) == pytest.approx(20)


def test_oracle_furness_hits_its_targets():
    w = fp.generate(3, 'FA')
    base = w['counts'][2]
    rows = {d: 1.2 * fp.leg_in(w['counts'], 2, d) for d in fo.DIRS}
    cols = {d: 1.2 * fp.leg_out(w['counts'], 2, d) for d in fo.DIRS}
    x = fo.furness(base, rows, cols)
    for d in fo.DIRS:
        assert sum(x[m] for m in ('NBL', 'NBT', 'NBR', 'SBL', 'SBT', 'SBR', 'EBL', 'EBT', 'EBR',
                                  'WBL', 'WBT', 'WBR') if m[:2] == d) == pytest.approx(rows[d], rel=1e-5)


@pytest.mark.parametrize('v,want', [(3, 0), (63, 60), (847, 850), (1237, 1200), (12340, 12500), (95, 100), (150000, 150000)])
def test_rounding_table_4_8(v, want):
    assert fo.round_forecast(v) == want


def test_track_choice_is_seeded_and_near_the_share():
    picks = [is_forecast_seed(s, 0.25) for s in range(4000)]
    assert picks == [is_forecast_seed(s, 0.25) for s in range(4000)]
    assert 0.22 < sum(picks) / len(picks) < 0.28
    assert not any(is_forecast_seed(s, 0) for s in range(100))
