// node frontend/src/lib/gridPaste.test.mjs
import assert from 'node:assert/strict'
import { gridPaste } from './gridPaste.js'

const g = { rowKeys: ['EBL', 'EBT', 'WBT', 'PHF', 'HV'], nCols: 2 }
const at = (row, col) => ({ ...g, row, col })
const w = (text, opts) => gridPaste(text, opts).writes

assert.deepEqual(w('1,200', at(1, 0)), [{ row: 1, col: 0, value: 1200 }], 'single value, thousands comma')
assert.deepEqual(w('10\n20\n30\n', at(0, 1)), [
  { row: 0, col: 1, value: 10 }, { row: 1, col: 1, value: 20 }, { row: 2, col: 1, value: 30 }], 'column fills down')
assert.deepEqual(gridPaste('10\n20\n30\n', at(0, 1)).skipped, [], 'trailing newline is not a skipped cell')
assert.deepEqual(w('5\t6\r\n7\t8', at(1, 0)), [
  { row: 1, col: 0, value: 5 }, { row: 1, col: 1, value: 6 }, { row: 2, col: 0, value: 7 }, { row: 2, col: 1, value: 8 }], 'block, CRLF')
assert.deepEqual(w('Movement\tAM\tPM\nWBT\t44\t45\nHV %\t2\t3', at(0, 0)), [
  { row: 2, col: 0, value: 44 }, { row: 2, col: 1, value: 45 }, { row: 4, col: 0, value: 2 }, { row: 4, col: 1, value: 3 }], 'labelled rows go by name; header skipped')
assert.deepEqual(w('Left\t9', at(0, 0)), [{ row: 0, col: 0, value: 9 }], 'unknown label dropped, value kept in place')
assert.deepEqual(w('1\t2\t3\n4\n5\n6\n7\n8', at(3, 1)), [
  { row: 3, col: 1, value: 1 }, { row: 4, col: 1, value: 4 }], 'clipped to the grid')
assert.deepEqual(gridPaste('hello', at(0, 0)), { writes: [], skipped: [] }, 'no numbers → nothing (browser handles it)')
assert.deepEqual(gridPaste('-5', at(0, 0)), { writes: [], skipped: [{ row: 0, col: 0, text: '-5', reason: 'negative' }] }, 'negative volumes rejected, reported')
assert.deepEqual(w('0.92', at(3, 0)), [{ row: 3, col: 0, value: 0.92 }], 'PHF decimal')

// QC: a blank or non-number line still takes one movement row — nothing shifts up.
const blank = gridPaste('101\n\n303', at(0, 0))
assert.deepEqual(blank.writes, [{ row: 0, col: 0, value: 101 }, { row: 2, col: 0, value: 303 }], 'blank line advances one row')
assert.deepEqual(blank.skipped, [{ row: 1, col: 0, text: '', reason: 'blank' }], 'blank cell reported')
const junk = gridPaste('-30\nabc\n12.7\n999999', { rowKeys: ['SBL', 'SBT', 'SBR', 'WBL', 'PHF'], nCols: 2, row: 0, col: 0 })
assert.deepEqual(junk.writes, [], 'no invalid count is written')
assert.deepEqual(junk.skipped.map((s) => [s.row, s.reason]), [
  [0, 'negative'], [1, 'not a number'], [2, 'not a whole number'], [3, 'over 20,000']], 'each bad line stays on its own row')
console.log('gridPaste: all passed')
