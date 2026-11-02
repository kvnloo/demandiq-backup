// rail progress tests — pure ESM, no runner:  node frontend/src/lib/railProgress.test.mjs
import assert from 'node:assert/strict'
import { doneSteps } from './railProgress.js'

const lanes = { exists: true, lanes: [{ movement: 'T' }] }
const it = { approaches: { EB: lanes, WB: lanes, NB: { exists: false }, SB: lanes } }

assert.deepEqual([...doneSteps({})], [], 'empty project → nothing done')
assert.deepEqual([...doneSteps({ project: { name: '  ' } })], [], 'blank name is not done')
assert.deepEqual([...doneSteps({ project: { name: 'X' }, intersections: [{}] })], [1, 2], 'unconfigured approaches stop at 2')
assert.deepEqual([...doneSteps({ intersections: [{ ...it, volumes: { AM: { EBT: 5 } } }] })], [2, 3, 4], 'volumes → 4')
assert.deepEqual([...doneSteps({ intersections: [{ ...it, balanced: { PM: { EBT: 5 } } }] })], [2, 3, 5, 6], 'balanced → 5, 6')
assert.deepEqual([...doneSteps({ intersections: [it, { approaches: { EB: { exists: true, lanes: [] } } }] })], [2], 'one bad approach → not 3')
console.log('railProgress: all passed')
