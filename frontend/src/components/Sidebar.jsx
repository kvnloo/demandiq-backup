import { useSyncExternalStore } from 'react'
import { NavLink } from 'react-router-dom'
import { LifeBuoy } from 'lucide-react'
import { exportDiagnostics } from '../lib/diagnostics'
import { subscribe, getSnapshot, isExistingAnalysisDone } from '../store/projectStore'
import { doneSteps } from '../lib/railProgress'

const STEPS = [
  { num: 1, label: 'Project Setup',      path: '/' },
  { num: 2, label: 'Map / Intersection', path: '/map' },
  { num: 3, label: 'Approach Config',    path: '/approaches' },
  { num: 4, label: 'Volume Entry',       path: '/volumes' },
  { num: 5, label: 'Balancing Settings', path: '/balancing' },
  { num: 6, label: 'Run Analysis',       path: '/analysis' },
  { num: 7, label: 'Results',            path: '/results' },
  { num: 8, label: 'Export',             path: '/export' },
]

// Forecasting phase (Method 1 / NCHRP 765). `built` flips on as each F-screen
// lands; until then the row is a disabled placeholder. The whole group is gated
// on the existing analysis having produced balanced TMCs.
const FORECAST_STEPS = [
  { key: 'F1', label: 'Horizon & Network', path: '/forecast/setup',   built: true },
  { key: 'F2', label: 'Network Match',     path: '/forecast/match',   built: true },
  { key: 'F3', label: 'Run Forecast',      path: '/forecast/run',     built: true },
  { key: 'F4', label: 'Future Results',    path: '/forecast/results', built: true },
]

// Rail styled as CrashIQ's (styles/shell.css): numbered lamps, active step outlined.
// NavLink adds the `active` class itself.
function Step({ lamp, label }) {
  return (
    <>
      <span className="di-lamp" aria-hidden="true">{lamp}</span>
      <span className="di-t">{label}</span>
    </>
  )
}

export default function Sidebar() {
  const snap = useSyncExternalStore(subscribe, getSnapshot, () => ({}))
  const gateOpen = isExistingAnalysisDone()
  const done = doneSteps(snap)

  return (
    <nav className="di-rail" aria-label="Study steps">
      <div className="sf-eyebrow">Existing counts</div>
      <div className="di-steps">
        {STEPS.map(({ num, label, path }) => (
          <NavLink key={num} to={path} end={path === '/'} className={`di-step${done.has(num) ? ' di-done' : ''}`}>
            <Step lamp={done.has(num) ? '✓' : num} label={label} />
          </NavLink>
        ))}
      </div>

      {/* Forecasting (Method 1 / NCHRP 765) — amber-anchored, gated */}
      <div className="sf-eyebrow">Forecasting</div>
      <div className="di-steps">
      {FORECAST_STEPS.map(({ key, label, path, built }) => {
        if (built && gateOpen) {
          return (
            <NavLink key={key} to={path} className="di-step di-step--fc">
              <Step lamp={key} label={label} />
            </NavLink>
          )
        }
        const title = !gateOpen
          ? 'Balance the existing volumes first (Run Analysis → Results)'
          : 'Available after the earlier forecasting steps'
        return (
          <div key={key} title={title} className="di-step di-step--fc di-locked">
            <Step lamp={key} label={label} />
          </div>
        )
      })}
      </div>

      {/* App-level action — pinned to the bottom */}
      <div className="di-foot">
        <button
          type="button"
          className="sf-btn"
          onClick={() => exportDiagnostics()}
          title="Download a JSON snapshot of project state for debugging (no credentials)"
        >
          <LifeBuoy size={14} />
          <span>Export diagnostics</span>
        </button>
      </div>
    </nav>
  )
}
