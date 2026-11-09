// node frontend/src/lib/gridPaste.test.mjs
import assert from 'node:assert/strict'
import { gridPaste } from './gridPaste.js'

const g = { rowKeys: ['EBL', 'EBT', 'WBT', 'PHF', 'HV'], nCols: 2 }
const at = (row, col) => ({ ...g, row, col })

assert.deepEqual(gridPaste('1,200', at(1, 0)), [{ row: 1, col: 0, value: 1200 }], 'single value, thousands comma')
assert.deepEqual(gridPaste('10\n20\n30\n', at(0, 1)), [
  { row: 0, col: 1, value: 10 }, { row: 1, col: 1, value: 20 }, { row: 2, col: 1, value: 30 }], 'column fills down')
assert.deepEqual(gridPaste('5\t6\r\n7\t8', at(1, 0)), [
  { row: 1, col: 0, value: 5 }, { row: 1, col: 1, value: 6 }, { row: 2, col: 0, value: 7 }, { row: 2, col: 1, value: 8 }], 'block, CRLF')
assert.deepEqual(gridPaste('Movement\tAM\tPM\nWBT\t44\t45\nHV %\t2\t3', at(0, 0)), [
  { row: 2, col: 0, value: 44 }, { row: 2, col: 1, value: 45 }, { row: 4, col: 0, value: 2 }, { row: 4, col: 1, value: 3 }], 'labelled rows go by name; header skipped')
assert.deepEqual(gridPaste('Left\t9', at(0, 0)), [{ row: 0, col: 0, value: 9 }], 'unknown label dropped, value kept in place')
assert.deepEqual(gridPaste('1\t2\t3\n4\n5\n6\n7\n8', at(3, 1)), [
  { row: 3, col: 1, value: 1 }, { row: 4, col: 1, value: 4 }], 'clipped to the grid')
assert.deepEqual(gridPaste('hello', at(0, 0)), [], 'no numbers → nothing (browser handles it)')
assert.deepEqual(gridPaste('-5', at(0, 0)), [], 'negative volumes rejected')
assert.deepEqual(gridPaste('0.92', at(3, 0)), [{ row: 3, col: 0, value: 0.92 }], 'PHF decimal')
console.log('gridPaste: all passed')
