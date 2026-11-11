# Fix: corridor through inflated at T-intersections

**State:** fixed and merged (PR #28, 2026-11-10). Both OPEN items decided by the owner the same day.
Diagnosis: `harness/knowledge/diagnosis-through-inflation.md`.

## What was wrong, in one example

Corridor of 20 intersections, PM peak, intersection 7 is a T (no south leg).
Westbound through: true volume 60, count 50. After balancing it showed **210**.

The neighbouring links disagreed by 241 and 173 vehicles. Balancing has to make each link add up, and
at intersection 7 it was also holding the cross street's approach total at its own count. At a T that
leaves only one cell free to take the change, which is the through: EBT + WBT = east row + west row
- cross-street column, exactly. So the whole disagreement landed on 50 cars. Link totals still closed to
0.0, so no check fired. RECON-ADJUST (PR #19) only flagged it.

## The fix

In the second balancing pass (per-intersection IPF), a leg is held to its target **only if it sits on a
balanced link** (link mode Full or Land Use). A cross-street leg with no link to a neighbour now floats.
That spreads each link change over every movement on that link in proportion to its volume, instead of
dumping it on the through. Code: `backend/balancer.py`, `balance_nchrp`, the Level 2 loop.

Same case after: the westbound through is **85** instead of 210. Link totals still close exactly.

## Proof

Direct runs of the balancer on harness corridors (500 per tier, both peaks). "Off" means the worst
movement in the corridor is past GEH 5 against the true volume (GEH is the standard count-comparison
measure; under 5 is the usual acceptance line):

- Tier A (simple 4-leg corridors): 2 of 500 off before, 0 after.
- Tier B: 13 of 500 before, 2 after.
- Tier C: 39 of 500 before, 1 after.
- Tier D (long chains with T-intersections): 92 of 500 before, 0 after.
- Got worse: 1 of 2,000. Tier C seed 104, PM int 4 northbound right, true 145: was 210, now 220.

The four failing Examiner cases (seeds 34093, 13594, 15327, 15711) are frozen in
`backend/tests/through_inflation_cases.json` and tested in `test_balancer_regressions.py`.
RECON-ADJUST now fires about a third as often (103 flags before, 31 after, tiers A–D, 200 seeds each).

## Owner decisions (were OPEN)

1. **DECIDED (owner, 2026-11-10): keep.** "1 yes". **A cross-street approach total can now differ from its count** when that leg has no link to a
   neighbour. Before, it always matched its count exactly; after, it moves by its share of the link
   change (e.g. a side-street approach counted 300 may balance to 315). This is how a corridor
   balance normally behaves when the side street isn't checked against anything, but it is a visible
   change. Keep (recommended) or hold side-street totals fixed?
2. **DECIDED (owner, 2026-11-10): leave the "larger" rule as is** ("to leave"). **What is left is the "larger" link rule.** When two adjacent counts disagree, the link takes the
   bigger one, so balanced volumes drift a little upward. Three of the four frozen cases still sit
   just over GEH 5 against the true volume (5.0–5.1) for that reason, so the regression test checks
   GEH < 5 against the movement's own count and ≤ 10 against the true volume. Changing the link rule
   (average, or weighted by count quality) is a separate decision; nothing changes until the owner
   picks.
