import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useNavigate } from 'react-router-dom'
import NeedsStep from '../components/NeedsStep'
import { Loader2, Play } from 'lucide-react'

import {
  subscribe,
  getSnapshot,
  setForecasting,
  isExistingAnalysisDone,
} from '../store/projectStore'
import { runForecast, friendlyError, isMatchStale, isRunStale, runRecord } from '../lib/forecastRun'
import { collectNotes, noteText, WARN_CODES, declineSummary } from '../lib/forecastNotes'

import '../styles/forms.css'
import '../styles/forecasting.css'
import { METHODS, TURN_METHODS, NO_MODEL, readTrend, MODEL_PERIODS, K_WARN } from '../lib/forecastMethods'

const DIRS = ['NB', 'SB', 'EB', 'WB']
const RESOLUTIONS = [
  { id: 'larger', label: 'Larger' },
  { id: 'average', label: 'Average' },
]

function legNeedsReview(leg) {
  if (!leg) return false
  if (leg.status === 'ambiguous') return true
  if (leg.status === 'unmatched') {
    if (leg.decision === 'skip') return false
    if (leg.decision === 'manual') {
      const i = leg.manual?.inbound
      const o = leg.manual?.outbound
      return !(i !== '' && i != null && Number(i) >= 0 && o !== '' && o != null && Number(o) >= 0)
    }
    return true
  }
  return false
}

export default function F3_RunForecast() {
  const navigate = useNavigate()
  const snap = useSyncExternalStore(subscribe, getSnapshot, () => ({}))
  const gateOpen = isExistingAnalysisDone()
  const fc = snap.forecasting ?? {}
  const matches = fc.matches ?? null
  const hasBase = !!fc.layers?.base
  const hasHorizon = !!fc.layers?.horizon
  const results = fc.results ?? null

  const [settings, setSettings] = useState(() => ({
    method: snap.balancing?.linkResolution ?? 'larger',
    refineMethod: 'ratio',
    peakHourFactor: '',
    turnMethod: 'furness',
    fuzzyBand: 5,
    trendText: '',
    growthRate: '',
    ...(fc.settings ?? {}),
  }))
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [error, setError] = useState(null)


  // Persist settings (debounced).
  const saveTimer = useRef(null)
  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => setForecasting({ settings }), 400)
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current) }
  }, [settings])

  if (!gateOpen) {
    return (
      <div className="di-page di-fc">
        <div className="di-eyebrow">Forecasting · Step 3</div>
        <h1 className="di-h1">Run forecast</h1>
        <NeedsStep
          title="Balance the existing volumes first"
          body="Forecasting grows the balanced base-year counts."
          to="/analysis"
          label="Go to Run Analysis"
        />
      </div>
    )
  }

  // Difference/Combined are only dimensionally valid with K (peak hour ÷ model period).
  const kRaw = settings.peakHourFactor
  const kValid = kRaw !== '' && kRaw != null && Number(kRaw) > 0
  const noModel = NO_MODEL.has(settings.refineMethod)
  const refineMethod = noModel || kValid ? settings.refineMethod : 'ratio'
  const { points: trendPts, dropped: trendDropped } = readTrend(settings.trendText)
  const periods = MODEL_PERIODS[fc.region || 'SANDAG'] ?? MODEL_PERIODS.SANDAG
  const kOdd = kValid && (Number(kRaw) < K_WARN.min || Number(kRaw) > K_WARN.max)
  const matchStale = !noModel && isMatchStale(fc)
  const runStale = !!results && isRunStale(fc, settings)
  const yearsSet = !!fc.baseYear && !!fc.horizonYear
  const growthReady = refineMethod === 'trend'
    ? new Set(trendPts.map((p) => p.year)).size >= 3
    : settings.growthRate !== '' && settings.growthRate != null && !Number.isNaN(Number(settings.growthRate))

  const unresolved = (matches ?? []).reduce(
    (n, m) => n + DIRS.filter((d) => legNeedsReview(m.legs?.[d])).length,
    0
  )

  const update = (key, value) => setSettings((s) => ({ ...s, [key]: value }))

  const canRun = status !== 'loading' && (noModel
    ? growthReady && yearsSet
    : hasBase && hasHorizon && unresolved === 0 && (matches?.length ?? 0) > 0 && !matchStale)

  const run = async () => {
    setStatus('loading')
    setError(null)
    try {
      // The settings the run was made under: refineMethod collapses to 'ratio'
      // unless a valid K unlocked difference/combined.
      const runSettings = { ...settings, refineMethod }
      const data = await runForecast({ snap, settings: runSettings })
      // Store what the run used, so F4/S8 label these numbers — not later edits.
      setForecasting({ results: data, settings, run: runRecord(fc, settings) })
      setStatus('idle')
    } catch (err) {
      setError(friendlyError(err))
      setStatus('error')
    }
  }

  return (
    <div className="di-page di-fc">
      <div className="di-eyebrow">Forecasting · Step 3</div>
      <h1 className="di-h1">Run forecast</h1>
      <p className="di-subtitle">
        Grow the balanced counts to the forecast year in two choices: how each road
        grows, then how road forecasts become turning movements.{' '}
        <a href="/methods-guide.html" target="_blank" rel="noreferrer" style={{ color: 'var(--signal-amber)' }}>
          How the methods differ, with test results →
        </a>
      </p>

      {!matches && !noModel ? (
        <div className="di-fc-note">
          Pull and match the model network first — or{' '}
          <button
            type="button"
            onClick={() => update('refineMethod', 'trend')}
            style={{ background: 'none', border: 'none', color: 'var(--signal-amber)', cursor: 'pointer', padding: 0, font: 'inherit', textDecoration: 'underline' }}
          >
            forecast without a model
          </button>.{' '}
          <button
            type="button"
            onClick={() => navigate('/forecast/match')}
            style={{ background: 'none', border: 'none', color: 'var(--signal-amber)', cursor: 'pointer', padding: 0, font: 'inherit', textDecoration: 'underline' }}
          >
            Go to Network Match →
          </button>
        </div>
      ) : (
        <div className="di-form">
          {/* ── Link resolution (only the per-intersection method balances links) ── */}
          {settings.turnMethod === 'furness' && <div className="di-field">
            <label className="di-label">When neighbouring counts disagree on a link</label>
            <div className="di-pill-group">
              {RESOLUTIONS.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  aria-pressed={settings.method === r.id}
                  className={`di-pill${settings.method === r.id ? ' di-pill--selected' : ''}`}
                  onClick={() => update('method', r.id)}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <p className="di-help">
              Two neighbouring intersections can count different volumes on the road between
              them. Larger uses the bigger count; Average splits the difference.
            </p>
          </div>}

          {/* ── Refinement method ───────────────────────────────────── */}
          <div className="di-field">
            <label className="di-label">1 · How each road grows</label>
            <select
              className="di-input"
              value={refineMethod}
              onChange={(e) => update('refineMethod', e.target.value)}
              style={{ maxWidth: 360 }}
              aria-label="Refinement method"
            >
              {METHODS.map((m) => {
                const off = !m.noModel && m.id !== 'ratio' && !kValid
                return (
                  <option key={m.id} value={m.id} disabled={off}>
                    {m.label}{off ? ' — needs K' : ''}
                  </option>
                )
              })}
            </select>
            <p className="di-help" style={{ lineHeight: 1.5 }}>
              {METHODS.find((m) => m.id === refineMethod)?.help}
              {!noModel && (
                <>{' '}Model flows are whole-period volumes (AM {periods.AM}, PM {periods.PM}) and
                counts are one peak hour, so every model method except Ratio needs K below.</>
              )}
            </p>
            {refineMethod === 'trend' && (
              <>
                <label className="di-label" htmlFor="fc-trend">Past counts — one per line: year, volume</label>
                <textarea
                  id="fc-trend"
                  className="di-input"
                  rows={5}
                  placeholder={'2015 18200\n2018 18900\n2021 19300\n2024 19800'}
                  value={settings.trendText}
                  onChange={(e) => update('trendText', e.target.value)}
                  style={{ maxWidth: 360, fontFamily: 'var(--font-mono, monospace)' }}
                />
                <p className="di-help">
                  Same location and kind of count each year (e.g. Caltrans AADT or a yearly PM count).
                  {trendPts.length > 0 && ` ${trendPts.length} read.`} Needs 3+ years; 10 is better (VDOT).
                  {' '}Counts must be from the count year or earlier.
                </p>
                {trendDropped.length > 0 && (
                  <div className="di-fc-banner di-fc-banner--warn">
                    {trendDropped.length} line{trendDropped.length === 1 ? '' : 's'} not read (need a year and a
                    volume above 0): {trendDropped.map((l) => `“${l}”`).join(', ')}
                  </div>
                )}
              </>
            )}
            {refineMethod === 'rate' && (
              <>
                <label className="di-label" htmlFor="fc-rate">Growth, % per year</label>
                <input
                  id="fc-rate"
                  className="di-input"
                  type="number"
                  step="0.1"
                  placeholder="e.g. 1.0, or -0.5 for a decline"
                  value={settings.growthRate}
                  onChange={(e) => update('growthRate', e.target.value)}
                  style={{ maxWidth: 220 }}
                />
              </>
            )}
            {noModel && !yearsSet && (
              <div className="di-fc-banner di-fc-banner--warn">Set the count year and forecast year in step 1.</div>
            )}
          </div>

          <div className="di-field">
            <label className="di-label">2 · How road forecasts become turning movements</label>
            <select
              className="di-input"
              value={settings.turnMethod}
              onChange={(e) => update('turnMethod', e.target.value)}
              style={{ maxWidth: 420 }}
              aria-label="Turn method"
            >
              {TURN_METHODS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
            <p className="di-help" style={{ lineHeight: 1.5 }}>
              {TURN_METHODS.find((m) => m.id === settings.turnMethod)?.help}
            </p>
            {settings.turnMethod === 'fuzzy' && (
              <>
                <label className="di-label" htmlFor="fc-band">Band, ± %</label>
                <input
                  id="fc-band"
                  className="di-input"
                  type="number"
                  step="1"
                  min="0"
                  max="49"
                  value={settings.fuzzyBand}
                  onChange={(e) => update('fuzzyBand', e.target.value)}
                  style={{ maxWidth: 120 }}
                />
              </>
            )}
          </div>

          {/* ── Advanced: K = peak hour ÷ model period ──────────────── */}
          {!noModel && <div className="di-field">
            <label className="di-label" htmlFor="fc-k">Peak hour ÷ model period (K) — optional</label>
            <input
              id="fc-k"
              className="di-input"
              type="number"
              inputMode="decimal"
              step="0.01"
              placeholder="e.g. 0.35 — leave blank to use Ratio"
              value={settings.peakHourFactor}
              onChange={(e) => update('peakHourFactor', e.target.value)}
              style={{ maxWidth: 280 }}
            />
            <p className="di-help" style={{ lineHeight: 1.5 }}>
              The share of the model&apos;s period volume that falls in your counted peak hour.
              {' '}{fc.region === 'MTC' ? 'MTC' : 'SANDAG'}&apos;s AM period is {periods.AM} and PM is {periods.PM},
              so K is usually about {periods.typicalK}. Not the peak-hour factor (PHF ≈ 0.9) and not
              the daily K-factor (≈ 0.09).
            </p>
            {kOdd && (
              <div className="di-fc-banner di-fc-banner--warn">
                K = {Number(kRaw)} is outside the usual {K_WARN.min}–{K_WARN.max}.
                {Number(kRaw) > K_WARN.max ? ' Is this a peak-hour factor (PHF)?' : ' Is this a daily K-factor?'}
                {' '}K here is peak hour ÷ model period. You can still run with it.
              </div>
            )}
          </div>}

          {!noModel && !hasHorizon && (
            <div className="di-fc-note">
              A <strong>horizon network</strong> isn&apos;t loaded yet — Method 1 grows the
              count by the model&apos;s base→horizon change, so it needs both years.
              Add the {snap.forecasting?.horizonYear || '2030'} network in step 1 and re-pull
              in step 2. (You can dry-run today by tagging the same file as both base and
              horizon in step 1 — identity growth returns the existing counts.)
            </div>
          )}

          {matchStale && (
            <div className="di-fc-banner di-fc-banner--warn">
              Step 1 changed since the network was pulled (model years or region) — re-pull in{' '}
              <button
                type="button"
                onClick={() => navigate('/forecast/match')}
                style={{ background: 'none', border: 'none', color: 'var(--signal-amber)', cursor: 'pointer', padding: 0, font: 'inherit', textDecoration: 'underline' }}
              >
                Network Match
              </button>{' '}
              before running.
            </div>
          )}

          {!noModel && unresolved > 0 && (
            <div className="di-fc-banner di-fc-banner--warn">
              {unresolved} leg{unresolved === 1 ? '' : 's'} still need a decision in{' '}
              <button
                type="button"
                onClick={() => navigate('/forecast/match')}
                style={{ background: 'none', border: 'none', color: 'var(--signal-amber)', cursor: 'pointer', padding: 0, font: 'inherit', textDecoration: 'underline' }}
              >
                Network Match
              </button>{' '}
              before running.
            </div>
          )}

          <div className="di-actions">
            <button type="button" className="di-btn-amber" disabled={!canRun} onClick={run}>
              {status === 'loading' ? (
                <><Loader2 size={15} className="di-spin" /> Running…</>
              ) : (
                <><Play size={15} /> Run forecast</>
              )}
            </button>
          </div>

          {status === 'error' && error && (
            <div className="di-fc-banner di-fc-banner--error" role="alert">{error}</div>
          )}

          {results && runStale && (
            <div className="di-fc-banner di-fc-banner--warn" role="alert">
              Inputs changed since this run — the results below are from the earlier run
              ({fc.run?.baseYear || '?'} → {fc.run?.horizonYear || '?'}). Run the forecast again to update them.
            </div>
          )}
          {results && <RunDiagnostics results={results} stale={runStale} />}
        </div>
      )}
    </div>
  )
}

function RunDiagnostics({ results, stale }) {
  const navigate = useNavigate()
  return (
    <div className="di-fc-diag" style={stale ? { opacity: 0.6 } : undefined}>
      <div className="di-fc-summary" style={{ marginBottom: 4 }}>{stale ? 'Earlier run (out of date)' : 'Run complete'}</div>
      {Object.entries(results).map(([period, data]) => (
        <PeriodDiag key={period} period={period} data={data} />
      ))}
      <div className="di-actions">
        <button
          type="button"
          className="di-btn-amber"
          onClick={() => navigate('/forecast/results')}
        >
          Continue to Future Results →
        </button>
      </div>
    </div>
  )
}

function PeriodDiag({ period, data }) {
  const balanced = data.pass2?.balanced ?? {}
  const nums = Object.keys(balanced)
  const conv = data.pass2?.converged ?? {}
  const converged = nums.filter((n) => conv[n]).length
  const qc = data.pass2?.qc_flags?.length ?? 0

  // One line per distinct note, with where it applies (a corridor-wide note
  // would otherwise repeat on every leg). Trust-changing notes are shown open.
  const notes = collectNotes(data)
  const warnings = notes.filter((n) => WARN_CODES.has(n.code) && n.code !== 'REFINE-DECLINE').map(noteText)
  const declines = declineSummary(notes)
  if (declines) warnings.push(declines)
  const refFlags = notes.filter((n) => !WARN_CODES.has(n.code)).map(noteText)
  const residuals = nums.map((n) => data.pass1?.meta?.[n]?.residual ?? 0)
  const maxResid = residuals.length ? Math.max(...residuals) : 0

  return (
    <div className="di-fc-diag__period">
      <div className="di-fc-diag__head">
        <strong>{period}</strong>
        <span>{nums.length} intersection{nums.length === 1 ? '' : 's'}</span>
        <span className={converged === nums.length ? 'di-fc-gate--ok' : 'di-fc-gate--todo'}>
          {converged}/{nums.length} converged
        </span>
        {data.trips
          ? <span>{data.trips.reduce((n, t) => n + t.trips, 0)} corridor trip paths</span>
          : <span>max Pass-1 residual {maxResid.toFixed(1)} veh</span>}
        <span className={qc === 0 ? 'di-fc-gate--ok' : 'di-fc-gate--todo'}>
          {qc === 0 ? 'no QC flags' : `${qc} QC flag${qc === 1 ? '' : 's'}`}
        </span>
      </div>
      {warnings.map((n, i) => (
        <div key={i} className="di-fc-banner di-fc-banner--warn" style={{ marginTop: 6 }}>⚠ {n}</div>
      ))}
      {refFlags.length > 0 && (
        <details className="di-fc-diag__flags">
          <summary>{refFlags.length} note{refFlags.length === 1 ? '' : 's'}</summary>
          <ul>{refFlags.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </details>
      )}
    </div>
  )
}
