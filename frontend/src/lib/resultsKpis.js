// Largest disagreement between two adjacent raw counts on one shared link, for
// the period on screen — the first number a reviewer checks. Read straight from
// the run (backend app.py: reconciliation_delta = |raw downstream − raw upstream|).
// null when the run has no paired links, so the KPI shows a dash, not a zero.
export function largestLinkGap(lastRun, period) {
  let best = null
  for (const l of lastRun?.resolvedLinks?.[period] ?? []) {
    const gap = l.reconciliation_delta
    if (typeof gap === 'number' && (best === null || gap > best.gap)) {
      best = { gap: Math.round(gap), fromNum: l.fromNum, toNum: l.toNum, direction: l.direction }
    }
  }
  return best
}

// Total balanced volume entering all intersections in one period, with
// engineer overrides applied — the same final numbers the diagrams and Excel
// use. null when nothing is balanced, so the KPI shows a dash, not a zero.
export function enteringTotal(intersections, period, overrides) {
  let sum = 0
  for (const it of intersections ?? []) {
    const bal = it.balanced?.[period]
    if (!bal) continue
    const ov = overrides?.[period]?.[String(it.num)] ?? {}
    for (const mv of new Set([...Object.keys(bal), ...Object.keys(ov)])) {
      const v = ov[mv] ?? bal[mv]
      if (typeof v === 'number') sum += v
    }
  }
  return sum || null
}
