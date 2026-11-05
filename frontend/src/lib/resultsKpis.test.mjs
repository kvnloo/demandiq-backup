// node frontend/src/lib/resultsKpis.test.mjs
import assert from 'node:assert/strict'
import { largestLinkGap, enteringTotal } from './resultsKpis.js'

const run = { resolvedLinks: { AM: [
  { fromNum: 1, toNum: 2, direction: 'EB', reconciliation_delta: 5 },
  { fromNum: 2, toNum: 3, direction: 'EB', reconciliation_delta: 145.4 },
  { fromNum: 3, toNum: 4, direction: 'WB', reconciliation_delta: null }], PM: [] } }

assert.deepEqual(largestLinkGap(run, 'AM'), { gap: 145, fromNum: 2, toNum: 3, direction: 'EB' })
assert.equal(largestLinkGap(run, 'PM'), null, 'no links → null')
assert.equal(largestLinkGap(null, 'AM'), null, 'no run → null')
assert.equal(largestLinkGap({ resolvedLinks: { AM: [{ reconciliation_delta: null }] } }, 'AM'), null, 'unpaired only → null')
const ints = [{ num: 1, balanced: { AM: { EBT: 100, WBT: 50 } } }, { num: 2, balanced: { AM: { NBT: 30 } } }, { num: 3 }]
assert.equal(enteringTotal(ints, 'AM', {}), 180, 'sums balanced')
assert.equal(enteringTotal(ints, 'AM', { AM: { 1: { EBT: 120 } } }), 200, 'override replaces its cell (+20)')
assert.equal(enteringTotal(ints, 'PM', {}), null, 'nothing balanced → null')
console.log('resultsKpis: all passed')
