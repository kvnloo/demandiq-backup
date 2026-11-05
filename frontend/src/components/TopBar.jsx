import { useState, useSyncExternalStore } from 'react'
import { Link } from 'react-router-dom'
import { Sun, Moon } from 'lucide-react'
import { getStoredTheme, applyTheme, otherTheme } from '../lib/theme'
import { subscribe, getSnapshot } from '../store/projectStore'
import NewProjectDialog from './NewProjectDialog'
// Pulled in so the confirm modal + buttons are styled even on routes (e.g. S1)
// whose own modules don't import these sheets.
import '../styles/forms.css'
import '../styles/map.css'

export default function TopBar() {
  const snap = useSyncExternalStore(subscribe, getSnapshot, () => ({}))
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [theme, setTheme] = useState(getStoredTheme)
  const isDay = theme === 'signal-flow-day'

  const name = snap.project?.name?.trim()
  const count = (snap.intersections || []).length
  const hasName = Boolean(name)

  let display
  if (hasName) {
    display = count > 0
      ? `${name} · ${count} intersection${count === 1 ? '' : 's'}`
      : name
  } else {
    display = 'Untitled Project'
  }

  return (
    <header className="di-top">
      <Link to="/" className="di-brand" aria-label="DemandIQ home">
        <span className="sf-sig" data-s="g" aria-hidden="true"><i /><i /><i /></span>
        <span>Demand<b>IQ</b></span>
      </Link>
      <div className={`di-proj${hasName ? ' di-proj--set' : ''}`} title={display}>{display}</div>

      <button
        type="button"
        className="di-theme-btn di-toplink"
        onClick={() => setTheme(applyTheme(otherTheme(theme)))}
        aria-label={isDay ? 'Switch to dark theme' : 'Switch to day theme'}
        title={isDay ? 'Dark theme' : 'Day theme (for daylight and printing)'}
      >
        {isDay ? <Moon size={16} /> : <Sun size={16} />}
      </button>

      <button
        type="button"
        className="di-btn-secondary"
        style={{ height: 32, padding: '0 14px', fontSize: 13 }}
        onClick={() => setConfirmOpen(true)}
      >
        New project
      </button>

      {confirmOpen && <NewProjectDialog onCancel={() => setConfirmOpen(false)} />}
    </header>
  )
}
