import assert from 'node:assert/strict'
import { flagPresentation } from './flagActions.js'

// Balancer emits a cell code for fractional counts, but only an approach for
// source outliers. Regression: never imply an unreported movement.
const fractional = flagPresentation({
  check: 'FRACTIONAL-VOLUME',
  int: 3,
  message: 'Int 3 EBT (AM): raw value 10.5 is not a whole number',
}, 3)
assert.equal(fractional.title, 'EBT · FRACTIONAL-VOLUME')
assert.equal(fractional.action, 'volumes')
assert.equal(fractional.actionLabel, 'Edit counts')

const outlier = flagPresentation({
  check: 'SOURCE-OUTLIER',
  int: 3,
  message: 'Int 3 EB approach total = 920 veh (AM), verify source count',
}, 3)
assert.equal(outlier.title, 'EB approach · SOURCE-OUTLIER')
assert.equal(outlier.action, 'volumes')

const convergence = flagPresentation({
  check: 'NCHRP-CONV', int: 3, message: 'Int 3 did not converge',
}, 3)
assert.equal(convergence.title, 'Int 3 · NCHRP-CONV')
assert.equal(convergence.action, 'balancing')

const unknown = flagPresentation({
  check: 'NCHRP-LINK-DELTA', int: 3, message: 'Link 3→4 NB not conserved',
}, 3)
assert.equal(unknown.title, 'Int 3 · NCHRP-LINK-DELTA')
assert.equal(unknown.action, 'analysis')
assert.equal(flagPresentation({
  check: 'GEOMETRY-FALLBACK', int: 3, message: 'Verify orientation',
}, 3).action, 'map')
assert.equal(flagPresentation({
  check: 'SOURCE-OUTLIER', int: 3, movement: 'not-a-movement', message: '',
}, 3).title, 'Int 3 · SOURCE-OUTLIER')

console.log('flagActions: 6 regression cases passed')
