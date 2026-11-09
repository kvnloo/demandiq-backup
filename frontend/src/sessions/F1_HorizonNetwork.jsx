import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import NeedsStep from '../components/NeedsStep'
import ModelFileUpload from '../components/ModelFileUpload'
import { Plus, Trash2 } from 'lucide-react'

import { getForecasting, setForecasting, isExistingAnalysisDone, getIntersections, getSnapshot } from '../store/projectStore'
import { isMatchStale, isRunStale } from '../lib/forecastRun'
import '../styles/forms.css'
import '../styles/forecasting.css'

// SANDAG model runs live server-side as data/sandag/hwyLoad_<year>.shp. The
// engineer picks a model YEAR from the ones the server has; the server picks the
// file (a client never sends a path) — same as MTC.
const SANDAG_NETWORKS = [
  { role: 'base', scenarioYear: '' },
  { role: 'horizon', scenarioYear: '' },
]
const ROLES = [
  { id: 'base', label: 'Base' },
  { id: 'horizon', label: 'Horizon' },
]

// Model regions. MTC's two runs are fixed server-side; SANDAG's year is picked here.
const REGIONS = [
  { id: 'SANDAG', label: 'San Diego (SANDAG)' },
  { id: 'MTC', label: 'Bay Area (MTC)' },
  { id: 'UPLOAD', label: 'My model file' },
]
const MTC_NETWORKS = [
  { role: 'base', scenarioYear: 2015 },
  { role: 'horizon', scenarioYear: 2050 },
]
const MAX_HORIZON_YEARS = 50

const DEFAULTS = () => ({
  region: 'SANDAG',
  baseYear: '',
  horizonYear: 2030,
  networks: SANDAG_NETWORKS,
})

export default function F1_HorizonNetwork() {
  const navigate = useNavigate()
  const gateOpen = isExistingAnalysisDone()

  const [form, setForm] = useState(() => ({ ...DEFAULTS(), ...(getForecasting() ?? {}) }))
  const [sandagYears, setSandagYears] = useState(null)   // null = loading, [] = none / failed

  useEffect(() => {
    fetch('/api/forecast/model-years')
      .then((r) => (r.ok ? r.json() : { SANDAG: [] }))
      .then((d) => setSandagYears(d.SANDAG ?? []))
      .catch(() => setSandagYears([]))
  }, [])

  // Self-guard: forecasting grows the existing balanced TMCs, so it's unreachable

  // Debounced auto-save into the forecasting slice — never lose the tags.
  const saveTimer = useRef(null)
  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => setForecasting(form), 400)
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current) }
  }, [form])

  if (!gateOpen) {
    return (
      <div className="di-page di-fc">
        <div className="di-eyebrow">Forecasting · Step 1</div>
        <h1 className="di-h1">Horizon &amp; network</h1>
        <NeedsStep
          title="Balance the existing volumes first"
          body="Forecasting grows the balanced base-year counts."
          to="/analysis"
          label="Go to Run Analysis"
        />
      </div>
    )
  }

  const update = (key, value) => setForm((f) => ({ ...f, [key]: value }))
  const updateNetwork = (i, partial) =>
    setForm((f) => ({
      ...f,
      networks: f.networks.map((n, j) => (j === i ? { ...n, ...partial } : n)),
    }))
  const addNetwork = () =>
    setForm((f) => ({
      ...f,
      networks: [
        ...f.networks,
        {
          role: f.networks.some((n) => n.role === 'base') ? 'horizon' : 'base',
          scenarioYear: '',
        },
      ],
    }))
  const removeNetwork = (i) =>
    setForm((f) => ({ ...f, networks: f.networks.filter((_, j) => j !== i) }))

  const hasHorizon = form.networks.some((n) => n.role === 'horizon')
  const isMtc = form.region === 'MTC'
  const isUpload = form.region === 'UPLOAD'
  const pickRegion = (id) =>
    setForm((f) => ({
      ...f,
      region: id,
      networks: id === 'MTC' ? MTC_NETWORKS : SANDAG_NETWORKS,
    }))
  const countYear = Number(form.baseYear)
  const horizonYear = Number(form.horizonYear)
  // Changed since the last pull / run? Say so — results are never relabelled.
  const fc = getForecasting() ?? {}
  const pullStale = isMatchStale({ ...fc, ...form })
  const runStale = !pullStale && isRunStale({ ...fc, ...form })
  const horizonOutOfRange = countYear > 0 && horizonYear > 0 &&
    (horizonYear < countYear || horizonYear > countYear + MAX_HORIZON_YEARS)

  return (
    <div className="di-page di-fc">
      <div className="di-eyebrow">Forecasting · Step 1</div>
      <h1 className="di-h1">Horizon &amp; network</h1>
      <p className="di-subtitle">
        Set the count year and the year to forecast. The model network is scoped to
        your existing intersections — no redrawing.
      </p>

      <div className="di-form">
        <div className="di-row">
          <div className="di-field">
            <label className="di-label" htmlFor="fc-base-year">
              Count year<span className="di-required">*</span>
            </label>
            <input
              id="fc-base-year"
              className="di-input"
              type="number"
              inputMode="numeric"
              placeholder="2025"
              value={form.baseYear}
              onChange={(e) => update('baseYear', e.target.value)}
            />
          </div>
          <div className="di-field">
            <label className="di-label" htmlFor="fc-horizon-year">
              Horizon year<span className="di-required">*</span>
            </label>
            <input
              id="fc-horizon-year"
              className="di-input"
              type="number"
              inputMode="numeric"
              min={countYear || undefined}
              max={countYear ? countYear + MAX_HORIZON_YEARS : undefined}
              value={form.horizonYear}
              onChange={(e) => update('horizonYear', e.target.value)}
            />
          </div>
        </div>
        {horizonOutOfRange && (
          <div className="di-fc-banner di-fc-banner--warn">
            Forecast year must be between {countYear} and {countYear + MAX_HORIZON_YEARS}.
          </div>
        )}

        <div className="di-field">
          <label className="di-label">Travel model</label>
          <div className="di-pill-group">
            {REGIONS.map((r) => (
              <button
                key={r.id}
                type="button"
                aria-pressed={form.region === r.id}
                className={`di-pill${form.region === r.id ? ' di-pill--selected' : ''}`}
                onClick={() => pickRegion(r.id)}
              >
                {r.label}
              </button>
            ))}
          </div>
          {isMtc && (
            <p className="di-help">
              Uses MTC&apos;s Plan Bay Area 2050 model runs for 2015 and 2050, all nine
              counties. Volumes are interpolated to your count year and forecast year.
            </p>
          )}
        </div>

        {isUpload && (
          <ModelFileUpload
            networks={form.networks}
            intersections={getIntersections().map((it) => ({ num: it.num, lat: it.lat, lng: it.lng }))}
            periodHours={form.periodHours}
            needsPm={(getSnapshot().project?.analysisPeriods ?? ['AM', 'PM']).includes('PM')}
            onNetwork={updateNetwork}
            onPeriodHours={(v) => update('periodHours', v)}
          />
        )}

        {!isMtc && !isUpload && <div className="di-field">
          <label className="di-label">SANDAG model networks</label>
          <p className="di-help" style={{ marginTop: 0 }}>
            Pick the model run for the base and the horizon. The base run should be
            close to your count year; DemandIQ interpolates both to your count and
            forecast years.
          </p>
          {sandagYears?.length === 0 && (
            <div className="di-fc-banner di-fc-banner--warn">
              No SANDAG model runs are available on this server.
            </div>
          )}

          {form.networks.map((n, i) => (
            <div key={i} className="di-fc-network">
              <div className="di-pill-group">
                {ROLES.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    aria-pressed={n.role === r.id}
                    className={`di-pill${n.role === r.id ? ' di-pill--selected' : ''}`}
                    onClick={() => updateNetwork(i, { role: r.id })}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
              <select
                className="di-input"
                value={n.scenarioYear}
                onChange={(e) => updateNetwork(i, { scenarioYear: e.target.value === '' ? '' : Number(e.target.value) })}
                style={{ maxWidth: 200 }}
                aria-label={`Network ${i + 1} model year`}
              >
                <option value="">{sandagYears == null ? 'Loading years…' : 'Pick a model year'}</option>
                {(sandagYears ?? []).map((y) => <option key={y} value={y}>{y} model run</option>)}
                {n.scenarioYear !== '' && sandagYears && !sandagYears.includes(Number(n.scenarioYear)) && (
                  <option value={n.scenarioYear}>{n.scenarioYear} (not on this server)</option>
                )}
              </select>
              {form.networks.length > 1 && (
                <button
                  type="button"
                  className="di-fc-network__remove"
                  onClick={() => removeNetwork(i)}
                  aria-label="Remove this network"
                >
                  <Trash2 size={15} />
                </button>
              )}
            </div>
          ))}

          <button type="button" className="di-fc-add" onClick={addNetwork}>
            <Plus size={14} /> Add network file
          </button>
        </div>}

        {!isMtc && !hasHorizon && (
          <div className="di-fc-note">
            Add the {form.horizonYear || '2030'} horizon network to run a forecast —
            Method 1 grows the count by the model&apos;s base→horizon change, so it
            needs both years. You can save with the base network only for now and
            add the horizon file when it&apos;s available.
          </div>
        )}

        {pullStale && (
          <div className="di-fc-banner di-fc-banner--warn">
            The model network or region changed since the last pull — re-pull it in step 2.
            The earlier matches and results no longer apply.
          </div>
        )}
        {runStale && (
          <div className="di-fc-banner di-fc-banner--warn">
            The years changed since the last forecast run — run the forecast again in step 3.
          </div>
        )}

        <div className="di-actions">
          <button
            type="button"
            className="di-btn-amber"
            onClick={() => navigate('/forecast/match')}
          >
            Continue to Network Match →
          </button>
        </div>
      </div>
    </div>
  )
}
