// theme normalization tests — pure ESM, no runner:
//   node frontend/src/lib/theme.test.mjs
//
// Locks the Phase 5 contract: the picker and the boot path must never apply an
// invalid data-theme. A stale id left in localStorage by an older build, a
// null (never set), or a typo must all resolve to the dark default — not blank.

import { normalizeTheme, DEFAULT_THEME, THEMES, otherTheme } from './theme.js'

let passed = 0, failed = 0
function ok(cond, msg) {
  if (cond) { passed++ } else { failed++; console.error('  FAIL:', msg) }
}

// ── Known ids pass through untouched ─────────────────────────────────────
ok(normalizeTheme('signal-flow') === 'signal-flow', 'signal-flow passes through')
ok(normalizeTheme('signal-flow-day') === 'signal-flow-day', 'signal-flow-day passes through')

// ── Anything unknown falls back to the default (never undefined/blank) ────
ok(normalizeTheme(null) === DEFAULT_THEME, 'null → default')
ok(normalizeTheme(undefined) === DEFAULT_THEME, 'undefined → default')
ok(normalizeTheme('') === DEFAULT_THEME, 'empty string → default')
ok(normalizeTheme('midnight') === DEFAULT_THEME, 'stale/unknown id → default')
ok(normalizeTheme('night-traffic') === 'signal-flow', 'retired night-traffic → signal-flow')
ok(normalizeTheme('sunshine') === 'signal-flow', 'retired sunshine → signal-flow')

// ── The default is itself a real theme ───────────────────────────────────
ok(THEMES.some((t) => t.id === DEFAULT_THEME), 'DEFAULT_THEME is a defined theme')

// ── Every theme labelled; the top-bar toggle flips between the two ───────
// Two: Signal Flow dark + day (Night Traffic / Sunshine retired).
ok(THEMES.length === 2, 'two themes defined')
for (const t of THEMES) ok(t.id && t.label, `${t.id || '?'} has id/label`)
ok(otherTheme('signal-flow') === 'signal-flow-day' && otherTheme('signal-flow-day') === 'signal-flow', 'toggle flips')
ok(otherTheme('sunshine') === 'signal-flow-day', 'retired id toggles from the default')

// Ids must be unique, or normalizeTheme accepts an id the picker can't show
// and two cards fight over the same selection. Worth asserting now that
// near-identical ids exist (signal-flow / signal-flow-day).
ok(new Set(THEMES.map((t) => t.id)).size === THEMES.length, 'theme ids are unique')

console.log(`theme: ${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
