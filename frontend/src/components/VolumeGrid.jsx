import { Fragment } from 'react'
import { setCellValue, setPhf, setHvPct } from '../store/projectStore'
import { gridPaste } from '../lib/gridPaste'

const DIRS = ['EB', 'WB', 'NB', 'SB']
const MVTS = ['U', 'L', 'T', 'R']
const DIR_ARROWS = { EB: 'EB →', WB: '← WB', NB: 'NB ↑', SB: 'SB ↓' }

// Compute which of {U, L, T, R} are active for an approach, based on its lanes.
// eslint-disable-next-line react-refresh/only-export-components -- pure helper co-located with its sole consumer; HMR-only rule
export function activeMovements(approach) {
  const out = { U: false, L: false, T: false, R: false }
  if (!approach || approach.exists === false) return out
  for (const lane of approach.lanes || []) {
    for (const ch of lane.movement || '') if (ch in out) out[ch] = true
  }
  return out
}

// One AM-or-PM volume input cell (number, with source colouring + AI badge).
function VolCell({ cell, onChange, onPaste }) {
  const source = cell?._source
  const value = cell?.value
  const cls =
    source === 'agent'  ? 'di-vol-td--agent'  :
    source === 'manual' ? 'di-vol-td--manual' : ''
  return (
    <td className={`di-vol-td di-vol-td--cell ${cls}`}>
      {source === 'agent' && <span className="di-vol-badge">AI</span>}
      <input
        type="number"
        min="0"
        className="di-vol-input"
        value={value ?? ''}
        onPaste={onPaste}
        onChange={(e) => {
          const raw = e.target.value
          onChange(raw === '' ? null : Number(raw))
        }}
      />
    </td>
  )
}

function ScalarCell({ value, step, onChange, onPaste }) {
  return (
    <td className="di-vol-td di-vol-td--cell">
      <input
        type="number"
        step={step}
        min="0"
        className="di-vol-input"
        value={value ?? ''}
        onPaste={onPaste}
        onChange={(e) => {
          const raw = e.target.value
          onChange(raw === '' ? null : Number(raw))
        }}
      />
    </td>
  )
}

const PERIOD_COLS = ['AM', 'PM']

// Paste into any cell: one value, a column, or a block copied from Excel fills
// down and across from that cell (see lib/gridPaste). Falls back to the
// browser's own paste when the clipboard holds no numbers.
function pasteHandler(it, rowKeys, rowIdx, colIdx) {
  return (e) => {
    const writes = gridPaste(e.clipboardData.getData('text'), { rowKeys, nCols: 2, row: rowIdx, col: colIdx })
    if (writes.length === 0) return
    e.preventDefault()
    for (const { row, col, value } of writes) {
      const key = rowKeys[row], period = PERIOD_COLS[col]
      if (key === 'PHF') setPhf(it.id, period, value)
      else if (key === 'HV') setHvPct(it.id, period, value)
      else setCellValue(it.id, period, key, value, 'manual')
    }
  }
}

// Volume entry, transposed to the locked AM (PM) convention: one row per active
// movement with side-by-side AM / PM columns (was two rows — AM, PM — per
// intersection spanning every movement as a column). Narrow by design, so it
// never clips at laptop widths; inactive movements are simply omitted as rows.
export default function VolumeGrid({ intersections }) {
  return (
    <div className="di-vol-wrap">
      <p className="di-vol-hint">
        Type, or paste from Excel: one number, a column or a whole block. It fills down and across from the cell you paste into.
      </p>
      {intersections.map((it) => {
        const active = {
          EB: activeMovements(it.approaches?.EB),
          WB: activeMovements(it.approaches?.WB),
          NB: activeMovements(it.approaches?.NB),
          SB: activeMovements(it.approaches?.SB),
        }
        const codesByDir = Object.fromEntries(DIRS.map((d) => [d, MVTS.filter((m) => active[d][m]).map((m) => `${d}${m}`)]))
        const rowKeys = [...DIRS.flatMap((d) => codesByDir[d]), 'PHF', 'HV']
        const paste = (key, col) => pasteHandler(it, rowKeys, rowKeys.indexOf(key), col)
        return (
          <section key={it.id} className="di-vol-int">
            <div className="di-vol-int__head">
              <div className="di-vol-meta__num">{it.num}</div>
              <div className="di-vol-meta__name" title={it.name}>{it.name}</div>
              <div className="di-vol-meta__sub">
                <span>{it.control}</span>
                {it.vendorDetected && (
                  <span className="di-vol-meta__vendor" title={it.sourceFile || ''}>
                    {it.vendorDetected}
                  </span>
                )}
              </div>
            </div>

            <table className="di-vol-grid di-vol-grid--mvt">
              <thead>
                <tr>
                  <th className="di-vol-th di-vol-th--mvt">Movement</th>
                  <th className="di-vol-th di-vol-th--val">AM volume</th>
                  <th className="di-vol-th di-vol-th--val">PM volume</th>
                </tr>
              </thead>
              <tbody>
                {DIRS.map((d) => {
                  const codes = codesByDir[d]
                  if (codes.length === 0) return null
                  return (
                    <Fragment key={d}>
                      <tr className="di-vol-dir-row">
                        <td colSpan={3} className="di-vol-dir">{DIR_ARROWS[d]}</td>
                      </tr>
                      {codes.map((code) => (
                        <tr key={code}>
                          <td className="di-vol-td di-vol-td--mvt">{code}</td>
                          <VolCell
                            cell={it.volumes?.AM?.[code]}
                            onChange={(v) => setCellValue(it.id, 'AM', code, v, 'manual')}
                            onPaste={paste(code, 0)}
                          />
                          <VolCell
                            cell={it.volumes?.PM?.[code]}
                            onChange={(v) => setCellValue(it.id, 'PM', code, v, 'manual')}
                            onPaste={paste(code, 1)}
                          />
                        </tr>
                      ))}
                    </Fragment>
                  )
                })}
                <tr className="di-vol-scalar-row">
                  <td className="di-vol-td di-vol-td--mvt">PHF</td>
                  <ScalarCell value={it.phf?.AM} step="0.001" onChange={(v) => setPhf(it.id, 'AM', v)} onPaste={paste('PHF', 0)} />
                  <ScalarCell value={it.phf?.PM} step="0.001" onChange={(v) => setPhf(it.id, 'PM', v)} onPaste={paste('PHF', 1)} />
                </tr>
                <tr className="di-vol-scalar-row">
                  <td className="di-vol-td di-vol-td--mvt">HV %</td>
                  <ScalarCell value={it.hvPct?.AM} step="0.1" onChange={(v) => setHvPct(it.id, 'AM', v)} onPaste={paste('HV', 0)} />
                  <ScalarCell value={it.hvPct?.PM} step="0.1" onChange={(v) => setHvPct(it.id, 'PM', v)} onPaste={paste('HV', 1)} />
                </tr>
              </tbody>
            </table>
          </section>
        )
      })}
    </div>
  )
}
