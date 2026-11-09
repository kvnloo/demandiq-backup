import { parseVolumeNumeric } from './bulkPaste.js'

const norm = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z]/g, '')
const isNum = (s) => !Number.isNaN(parseVolumeNumeric(s))

// Clipboard text (Excel / Sheets copy: tabs between cells, newlines between
// rows) → cell writes for one intersection's grid of rowKeys × nCols, pasted at
// cell (row, col). A pasted row that starts with a grid row's name ("EBT",
// "PHF", "HV %") goes to that row, from the first column. Other rows fill down
// from the pasted cell. Header rows, labels and non-numbers are skipped, so
// copying a whole block with its labels still lands in the right cells.
// Returns [{ row, col, value }]; empty when nothing numeric was pasted.
export function gridPaste(text, { rowKeys, nCols, row, col }) {
  const keys = rowKeys.map(norm)
  const out = []
  let next = row
  for (const line of String(text ?? '').replace(/\r/g, '').split('\n')) {
    let cells = line.split('\t')
    if (!cells.some(isNum)) continue // blank line or header row
    const named = isNum(cells[0]) ? -1 : keys.indexOf(norm(cells[0]))
    let r, c
    if (named >= 0) {
      r = named; c = 0; cells = cells.slice(1)
    } else {
      r = next++; c = col
      while (cells.length && !isNum(cells[0]) && cells[0].trim() !== '') cells = cells.slice(1) // unknown label
    }
    cells.forEach((v, j) => {
      const n = parseVolumeNumeric(v)
      if (r < rowKeys.length && c + j < nCols && Number.isFinite(n) && n >= 0) out.push({ row: r, col: c + j, value: n })
    })
  }
  return out
}
