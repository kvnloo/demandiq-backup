import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import NeedsStep from '../components/NeedsStep'
import { Plus, Trash2 } from 'lucide-react'

import { getForecasting, setForecasting, isExistingAnalysisDone } from '../store/projectStore'
import '../styles/forms.css'
import '../styles/forecasting.css'

// v1 SANDAG asset lives server-side under data/sandag/. The engineer gives just
// the FILE NAME; the backend resolves it under its data dir (so a path can't
// escape, and it's CWD-independent). The year is engineer-tagged here, not
// parsed from the filename, per the locked scenario-year call.
const DEFAULT_SANDAG_PATH = 'hwyLoad.shp'
const ROLES = [
  { id: 'base', label: 'Base' },
  { id: 'horizon', label: 'Horizon' },
]

// Model regions. MTC's two runs are fixed server-side; SANDAG files are named here.
const REGIONS = [
  { id: 'SANDAG', label: 'San Diego (SANDAG)' },
  { id: 'MTC', label: 'Bay Area (MTC)' },
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
  networks: [{ role: 'base', scenarioYear: '', path: DEFAULT_SANDAG_PATH }],
})

export default function F1_HorizonNetwork() {
  const navigate = useNavigate()
  const gateOpen = isExistingAnalysisDone()

  const [form, setForm] = useState(() => ({ ...DEFAULTS(), ...(getForecasting() ?? {}) }))

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
          path: DEFAULT_SANDAG_PATH,
        },
      ],
    }))
  const removeNetwork = (i) =>
    setForm((f) => ({ ...f, networks: f.networks.filter((_, j) => j !== i) }))

  const hasHorizon = form.networks.some((n) => n.role === 'horizon')
  const isMtc = form.region === 'MTC'
  const pickRegion = (id) =>
    setForm((f) => ({
      ...f,
      region: id,
      networks: id === 'MTC'
        ? MTC_NETWORKS
        : [{ role: 'base', scenarioYear: '', path: DEFAULT_SANDAG_PATH }],
    }))
  const countYear = Number(form.baseYear)
  const horizonYear = Number(form.horizonYear)
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

        {!isMtc && <div className="di-field">
          <label className="di-label">SANDAG model networks</label>
          <p className="di-help" style={{ marginTop: 0 }}>
            Tag each loaded-network file with its role and model year. The year is
            yours to set — it isn&apos;t read from the filename.
          </p>

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
              <input
                className="di-input"
                type="number"
                inputMode="numeric"
                placeholder="year"
                value={n.scenarioYear}
                onChange={(e) => updateNetwork(i, { scenarioYear: e.target.value })}
                style={{ maxWidth: 110 }}
                aria-label={`Network ${i + 1} model year`}
              />
              <input
                className="di-input"
                type="text"
                spellCheck={false}
                autoComplete="off"
                placeholder={DEFAULT_SANDAG_PATH}
                value={n.path}
                onChange={(e) => updateNetwork(i, { path: e.target.value })}
                aria-label={`Network ${i + 1} shapefile path`}
              />
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
