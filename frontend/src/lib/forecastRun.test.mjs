// Forecast lib tests (QC items 5, 6, 10) — pure ESM, no test runner required:
//   node frontend/src/lib/forecastRun.test.mjs
import { readTrend } from './forecastMethods.js'
import { runKey, runRecord, isRunStale, isMatchStale, pullKey } from './forecastRun.js'
import { collectNotes, WARN_CODES, declineSummary } from './forecastNotes.js'

let passed = 0, failed = 0
function ok(cond, msg) {
  if (cond) { passed++ } else { failed++; console.error('  FAIL:', msg) }
}

// ── Item 10: thousands separators read, bad lines reported ─────────────────
{
  const { points, dropped } = readTrend('2015, 18,200\n2018 18900\n2021;19,300.5\n\n2024 0\nabc\n2016 1,2')
  ok(points.length === 3, `3 points read, got ${points.length}`)
  ok(points[0].year === 2015 && points[0].volume === 18200, `"2015, 18,200" → 18200, got ${points[0]?.volume}`)
  ok(points[2].volume === 19300.5, '19,300.5 read with decimals')
  ok(dropped.length === 3 && dropped.includes('2024 0') && dropped.includes('abc') && dropped.includes('2016 1,2'),
    `zero / text / malformed lines are reported: ${JSON.stringify(dropped)}`)
}

// ── Item 6: results go stale when F1 or F3 inputs change ───────────────────
{
  const fc = {
    region: 'SANDAG', baseYear: '2025', horizonYear: '2045',
    networks: [{ role: 'base', scenarioYear: 2025 }, { role: 'horizon', scenarioYear: 2050 }],
    matches: [{ intersection_num: 1 }], layers: { base: { scenario_year: 2025 }, horizon: { scenario_year: 2050 } },
    settings: { refineMethod: 'ratio', turnMethod: 'furness' },
  }
  fc.pulledFor = pullKey(fc)
  fc.results = { AM: {} }
  fc.run = runRecord(fc, fc.settings)
  ok(!isRunStale(fc) && !isMatchStale(fc), 'fresh run is not stale')
  ok(fc.run.horizonYear === '2045' && fc.run.modelYears.horizon === 2050, 'run record keeps the years it ran with')
  ok(!isRunStale({ ...fc, settings: { ...fc.settings, scenarioReductionPct: 10 } }), 'scenario % does not stale the run')
  ok(!isRunStale({ ...fc, settings: { ...fc.settings, refineMethod: 'difference' } }),
    'difference without K collapses to ratio — same run')
  ok(isRunStale({ ...fc, horizonYear: '2075' }), 'new horizon year → stale')
  ok(isRunStale({ ...fc, settings: { ...fc.settings, turnMethod: 'fratar' } }), 'new turn method → stale')
  const net = { ...fc, networks: [fc.networks[0], { role: 'horizon', scenarioYear: 2035 }] }
  ok(isMatchStale(net) && isRunStale(net), 'new model year → matches and results stale')
  ok(isRunStale({ ...fc, run: undefined }), 'legacy results with no run record count as stale')
  ok(runKey(fc, { refineMethod: 'rate', growthRate: 1 }) === runKey({ ...fc, region: 'MTC' }, { refineMethod: 'rate', growthRate: 1 }),
    'no-model runs ignore the network')
}

// ── Item 5: leg-level flags are collected (REFINE-EXTRAPOLATED lives there) ─
{
  const data = {
    refinement: [{
      intersection_num: 1,
      approaches: {
        NB: { flags: [{ code: 'REFINE-EXTRAPOLATED', message: 'Model only goes to 2050; 2075 is extrapolated' }],
          inbound: { flags: [{ code: 'REFINE-DECLINE', message: 'falls' }] }, outbound: { flags: [] } },
        SB: { flags: [{ code: 'REFINE-EXTRAPOLATED', message: 'Model only goes to 2050; 2075 is extrapolated' }] },
      },
    }],
    pass1: { meta: { 1: { flags: [{ code: 'X', message: 'p1' }] } } },
  }
  const notes = collectNotes(data)
  const ext = notes.find((n) => n.code === 'REFINE-EXTRAPOLATED')
  ok(ext && ext.where.length === 2, 'extrapolation flag collected once, from both legs')
  ok(notes.length === 3 && WARN_CODES.has('REFINE-EXTRAPOLATED'), 'all three notes collected')
  const dec = declineSummary([{ code: 'REFINE-DECLINE', message: 'model shows traffic falling -2.4%/yr — kept', where: ['Int 2 NB in'] }])
  ok(/falls on 1 approach\/exit total .*Int 2 NB in -2\.4%\/yr/.test(dec), `decline summary: ${dec}`)
}

console.log(`forecastRun: ${passed} passed, ${failed} failed`)
if (failed) process.exit(1)
