import { parseVolumeNumeric, volumeError } from './bulkPaste.js'

const norm = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z]/g, '')
const isNum = (s) => !Number.isNaN(parseVolumeNumeric(s))
const SCALAR_ROWS = new Set(['PHF', 'HV'])

// Clipboard text (Excel / Sheets copy: tabs between cells, newlines between
// rows) → cell writes for one intersection's grid of rowKeys × nCols, pasted at
// cell (row, col). A pasted row that starts with a grid row's name ("EBT",
// "PHF", "HV %") goes to that row, from the first column. Every other pasted
// line fills the next grid row down — blank and non-number lines included, so
// one bad cell never shifts the values below it onto the wrong movement.
// Header lines above the first number are skipped, so copying a whole block
// with its labels still lands in the right cells.
// Returns { writes: [{ row, col, value }], skipped: [{ row, col, text, reason }] }:
// skipped lists in-grid cells left unchanged (blank, not a number, or not a
// valid count). Both empty when nothing numeric was pasted.
export function gridPaste(text, { rowKeys, nCols, row, col }) {
  const keys = rowKeys.map(norm)
  const writes = []
  const skipped = []
  const lines = String(text ?? '').replace(/\r/g, '').split('\n')
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop() // Excel's trailing newline
  let next = row
  let started = false
  for (const line of lines) {
    let cells = line.split('\t')
    const hasNum = cells.some(isNum)
    if (!started && !hasNum) continue // header row above the data
    started = true
    const named = hasNum && !isNum(cells[0]) ? keys.indexOf(norm(cells[0])) : -1
    let r, c
    if (named >= 0) {
      r = named; c = 0; cells = cells.slice(1)
    } else {
      r = next++; c = col
      if (hasNum) while (cells.length && !isNum(cells[0]) && cells[0].trim() !== '') cells = cells.slice(1) // unknown label
    }
    if (r >= rowKeys.length) continue
    cells.forEach((v, j) => {
      if (c + j >= nCols) return
      const n = parseVolumeNumeric(v)
      const reason = v.trim() === '' ? 'blank'
        : SCALAR_ROWS.has(rowKeys[r]) ? (Number.isFinite(n) && n >= 0 ? null : 'not a number')
        : volumeError(n)
      if (reason) skipped.push({ row: r, col: c + j, text: v.trim(), reason })
      else writes.push({ row: r, col: c + j, value: n })
    })
  }
  return { writes, skipped }
}
