import { useRef, useState } from 'react'
import { Upload, Check, Loader2 } from 'lucide-react'

import { friendlyError } from '../lib/forecastRun'
import '../styles/volumes.css'   // .di-up__drop — the same drop zone as count uploads

const MAX_BYTES = 500 * 1024 * 1024
const ACCEPT = '.zip,.shp,.shx,.dbf,.prj,.cpg,.gpkg,.geojson,.json'
// Column info per upload id. Kept in memory only — the project stores the id + mapping.
const fileInfo = new Map()

const SLOTS = { base: 'Base-year model file', horizon: 'Horizon-year model file' }

// What each column is for, in plain words. `req` = needed to run.
// shown open; everything else folds under 'More columns'
const MAIN_ROLES = new Set(['am_ab', 'am_ba', 'pm_ab', 'pm_ba', 'name'])

const ROLES = [
  { key: 'am_ab', label: 'Morning peak volume, A→B direction', req: true,
    help: 'Vehicles over the whole AM model period, from the start node to the end node.' },
  { key: 'am_ba', label: 'Morning peak volume, B→A direction',
    help: 'The other direction. Leave blank if each row is one direction of travel.' },
  { key: 'pm_ab', label: 'Evening peak volume, A→B direction', help: 'Needed if you counted the PM peak.' },
  { key: 'pm_ba', label: 'Evening peak volume, B→A direction', help: 'Leave blank if each row is one direction.' },
  { key: 'from_node', label: 'Start node number (A)', help: 'Joins links at intersections. Leave both node columns blank to join links where their ends meet.' },
  { key: 'to_node', label: 'End node number (B)', help: 'The other end of the link.' },
  { key: 'link_id', label: 'Link ID', help: 'Pairs each road piece between the two model years. Blank = use the node numbers.' },
  { key: 'daily', label: 'Daily volume, both directions', help: 'For VMT. Blank = no daily figures.' },
  { key: 'length_mi', label: 'Link length, miles', help: 'For VMT. Blank = measured from the map.' },
  { key: 'name', label: 'Street name', help: 'Shown when you confirm the matches.' },
  { key: 'lanes_ab', label: 'Lanes, A→B', help: 'Flags legs where the model adds or removes lanes.' },
  { key: 'lanes_ba', label: 'Lanes, B→A', help: 'Blank if one lanes column covers the link.' },
  { key: 'fclass', label: 'Road type', help: 'Shown when you confirm the matches.' },
  { key: 'connector_field', label: 'Centroid connectors (not real roads)', help: 'Rows with the values below are left out.' },
]

function sendFiles(files, intersections, onProgress) {
  return new Promise((resolve, reject) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f, f.name)
    fd.append('intersections', JSON.stringify(intersections))
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/forecast/upload-network')
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)) }
    xhr.onload = () => {
      let body = null
      try { body = JSON.parse(xhr.responseText) } catch { /* non-JSON */ }
      if (xhr.status === 200 && body) resolve(body)
      else reject(new Error(body?.detail || `The upload failed (HTTP ${xhr.status}).`))
    }
    xhr.onerror = () => reject(new TypeError('network'))
    xhr.send(fd)
  })
}

export default function ModelFileUpload({ networks, intersections, periodHours, needsPm, onNetwork, onPeriodHours }) {
  const base = networks.find((n) => n.role === 'base')
  // An edit to the base file's columns carries to the horizon file when it has that column.
  const setMapping = (i, key, value) => {
    const n = networks[i]
    const patch = (m) => {
      const next = { ...(m ?? {}), [key]: value || undefined }
      if (key === 'connector_field' && !value) delete next.connector_values
      return next
    }
    onNetwork(i, { mapping: patch(n.mapping) })
    if (n.role === 'base') {
      networks.forEach((h, j) => {
        const fields = fileInfo.get(h.uploadId)?.fields?.map((f) => f.name)
        if (j !== i && h.uploadId && (!value || key === 'connector_values' || fields?.includes(value))) {
          onNetwork(j, { mapping: patch(h.mapping) })
        }
      })
    }
  }

  return (
    <div className="di-field">
      <label className="di-label">Your model files</label>
      <p className="di-help" style={{ marginTop: 0 }}>
        Upload the loaded highway network your travel model produced for the base year and for
        the horizon year: a zipped shapefile (.zip), the .shp with its .shx, .dbf and .prj, a
        GeoPackage (.gpkg) or GeoJSON, up to 500 MB each. Cube .net files can&apos;t be read —
        export the loaded network to a shapefile first.
      </p>
      {networks.map((n, i) => (
        <FileSlot
          key={n.role}
          network={n}
          intersections={intersections}
          onNetwork={(partial) => onNetwork(i, partial)}
        />
      ))}

      {base?.uploadId && (
        <>
          <label className="di-label" style={{ marginTop: 12 }}>Check the columns</label>
          <p className="di-help" style={{ marginTop: 0 }}>
            DemandIQ guessed which column holds what. Fix any that are wrong — the example value
            is from the first rows of your file.
          </p>
          {networks.map((n, i) => n.uploadId && (n.role === 'base' || differs(base, n)) && (
            <ColumnCheck
              key={n.role}
              title={networks.length > 1 ? SLOTS[n.role] : null}
              network={n}
              needsPm={needsPm}
              onChange={(key, v) => setMapping(i, key, v)}
            />
          ))}
          {networks.some((n) => n.role !== 'base' && n.uploadId && !differs(base, n)) && (
            <p className="di-help">The horizon file uses the same columns.</p>
          )}

          <div className="di-row" style={{ marginTop: 8 }}>
            {['AM', 'PM'].map((p) => (
              <div className="di-field" key={p}>
                <label className="di-label" htmlFor={`fc-hours-${p}`}>
                  How long is the model&apos;s {p} period? (hours)
                </label>
                <input
                  id={`fc-hours-${p}`}
                  className="di-input"
                  type="number"
                  inputMode="decimal"
                  min="0.25"
                  step="0.5"
                  placeholder={p === 'AM' ? 'e.g. 3' : 'e.g. 3.5'}
                  value={periodHours?.[p] ?? ''}
                  onChange={(e) => onPeriodHours({ ...(periodHours ?? {}), [p]: e.target.value })}
                  style={{ maxWidth: 200 }}
                />
              </div>
            ))}
          </div>
          <p className="di-help" style={{ marginTop: 0 }}>
            Model volumes cover the whole period; your counts are one hour. The length lets step 3
            suggest the peak-hour share (K). See your model&apos;s documentation.
          </p>
        </>
      )}
    </div>
  )
}

// Example values: whole numbers as-is, decimals to 2 places (DBF floats carry noise).
const fmtSample = (v) => (typeof v === 'number' && !Number.isInteger(v) ? String(Math.round(v * 100) / 100) : String(v))

const differs = (a, b) => JSON.stringify(a?.mapping ?? {}) !== JSON.stringify(b?.mapping ?? {})

function FileSlot({ network, intersections, onNetwork }) {
  const [phase, setPhase] = useState(null)   // null | { pct } | 'checking'
  const [error, setError] = useState(null)
  const [over, setOver] = useState(false)
  const inputRef = useRef(null)
  const info = fileInfo.get(network.uploadId)

  const send = async (list) => {
    const files = [...list]
    if (!files.length) return
    setError(null)
    if (files.reduce((s, f) => s + f.size, 0) > MAX_BYTES) {
      setError('That is over 500 MB. Zip the shapefile parts, or clip the network to your study area first.')
      return
    }
    setPhase({ pct: 0 })
    try {
      const body = await sendFiles(files, intersections, (pct) => setPhase(pct >= 100 ? 'checking' : { pct }))
      fileInfo.set(body.upload_id, body)
      onNetwork({
        uploadId: body.upload_id,
        mapping: body.mapping,
        fileName: files.length === 1 ? files[0].name : `${files.length} files`,
        featureCount: body.feature_count,
      })
    } catch (err) {
      setError(friendlyError(err))
    } finally {
      setPhase(null)
    }
  }

  const outside = info?.intersections_outside ?? []
  return (
    <div className="di-fc-file">
      <div className="di-fc-file__head">
        <span className="di-fc-file__title">{SLOTS[network.role]}</span>
        <label className="di-fc-file__year">
          Model year
          <input
            className="di-input"
            type="number"
            inputMode="numeric"
            placeholder={network.role === 'base' ? '2025' : '2050'}
            value={network.scenarioYear}
            onChange={(e) => onNetwork({ scenarioYear: e.target.value === '' ? '' : Number(e.target.value) })}
            aria-label={`${SLOTS[network.role]} model year`}
          />
        </label>
      </div>

      <div
        className={`di-up__drop${over ? ' di-up__drop--over' : ''}`}
        onDragEnter={(e) => { e.preventDefault(); setOver(true) }}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); send(e.dataTransfer.files) }}
        onClick={() => phase == null && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-label={`Upload the ${SLOTS[network.role].toLowerCase()}`}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click() }}
      >
        {phase != null ? (
          <>
            <Loader2 size={18} className="di-spin" />
            <span className="di-up__drop-text">
              {phase === 'checking' ? 'Checking the file…' : `Uploading… ${phase.pct}%`}
            </span>
            {phase !== 'checking' && <progress max="100" value={phase.pct} className="di-fc-file__bar" />}
          </>
        ) : network.uploadId ? (
          <>
            <Check size={18} style={{ color: 'var(--signal-green)' }} />
            <span className="di-up__drop-text">
              {network.fileName || 'Model file'}
              {network.featureCount != null && <> · {Number(network.featureCount).toLocaleString()} road links</>}
            </span>
            <span className="di-up__drop-hint">Drop or click to replace</span>
          </>
        ) : (
          <>
            <Upload size={18} className="di-up__drop-icon" />
            <span className="di-up__drop-text">Drag the model file here, or click to pick it</span>
            <span className="di-up__drop-hint">.zip · .shp + .shx + .dbf + .prj · .gpkg · .geojson — up to 500 MB</span>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          onChange={(e) => { send(e.target.files); e.target.value = '' }}
          style={{ display: 'none' }}
        />
      </div>

      {error && <div className="di-fc-banner di-fc-banner--error" role="alert">{error}</div>}
      {outside.length > 0 && (
        <div className="di-fc-banner di-fc-banner--warn">
          Intersection{outside.length === 1 ? '' : 's'} {outside.join(', ')}{' '}
          {outside.length === 1 ? 'is' : 'are'} outside this file&apos;s area — the model has no
          roads there. Check it&apos;s the right region&apos;s file.
        </div>
      )}
    </div>
  )
}

function ColumnCheck({ title, network, needsPm, onChange }) {
  const info = fileInfo.get(network.uploadId)
  const m = network.mapping ?? {}
  // Without the column list (page reloaded), offer the columns already picked.
  const fields = info?.fields ?? [...new Set(Object.entries(m)
    .filter(([k, v]) => k !== 'connector_values' && v).map(([, v]) => v))].map((name) => ({ name }))
  const sample = (name) => fields.find((f) => f.name === name)?.sample
  const cf = m.connector_field
  const cfValues = info?.values?.[cf]

  return (
    <div className="di-fc-map">
      {title && <div className="di-fc-file__title">{title}</div>}
      {!info && <p className="di-help">Upload the file again to see all of its columns.</p>}
      <table>
        <thead>
          <tr><th>What DemandIQ needs</th><th>Column in your file</th><th>Example</th></tr>
        </thead>
        <tbody>
          {ROLES.filter((r) => MAIN_ROLES.has(r.key)).map(row)}
        </tbody>
      </table>
      {/* optional columns stay folded unless one needs a look */}
      <details className="di-fc-map__more" open={ROLES.some((r) => !MAIN_ROLES.has(r.key) && needsLook(r))}>
        <summary>More columns (optional) — {ROLES.filter((r) => !MAIN_ROLES.has(r.key) && m[r.key]).length} found</summary>
        <table>
          <tbody>
            {ROLES.filter((r) => !MAIN_ROLES.has(r.key)).map(row)}
          </tbody>
        </table>
      </details>
    </div>
  )

  function needsLook(r) {
    const v = m[r.key]
    return (v && info?.confidence?.[r.key] === 'low') || (!v && (r.req || (r.key === 'pm_ab' && needsPm)))
  }

  function row(r) {
            const v = m[r.key] ?? ''
            const low = v && info?.confidence?.[r.key] === 'low'
            const missing = !v && (r.req || (r.key === 'pm_ab' && needsPm))
            return (
              <tr key={r.key}>
                <td>
                  <div className="di-fc-map__role">
                    {r.label}
                    {low && <span className="di-fc-map__flag">Check</span>}
                    {missing && <span className="di-fc-map__flag di-fc-map__flag--req">Pick a column</span>}
                  </div>
                  <div className="di-fc-map__help">{r.help}</div>
                </td>
                <td>
                  <select
                    className="di-input"
                    value={v}
                    onChange={(e) => onChange(r.key, e.target.value)}
                    aria-label={r.label}
                  >
                    <option value="">— not in this file —</option>
                    {fields.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
                  </select>
                  {r.key === 'connector_field' && cf && (
                    <input
                      className="di-input"
                      style={{ marginTop: 6 }}
                      placeholder="Values to leave out, e.g. 10"
                      value={(m.connector_values ?? []).join(', ')}
                      onChange={(e) => onChange('connector_values',
                        e.target.value.split(',').map((s) => s.trim()).filter(Boolean))}
                      aria-label="Connector values to leave out"
                    />
                  )}
                </td>
                <td className="di-fc-map__sample">
                  {r.key === 'connector_field' && cfValues
                    ? `Values: ${cfValues.slice(0, 12).join(', ')}${cfValues.length > 12 ? '…' : ''}`
                    : v && sample(v) != null ? fmtSample(sample(v)) : ''}
                </td>
              </tr>
            )
  }
}
