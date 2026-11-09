import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { isResultsStale } from '../store/projectStore'

// One banner for every results screen (S6–S8): results computed from counts
// that have since changed, or a run made with balancing switched off.
// onRun (S6) re-runs in place; elsewhere the action links to Run Analysis.
export default function RunStatusBanner({ snap, onRun, running = false }) {
  const stale = isResultsStale(snap)
  const notBalanced = !stale && snap.analysis?.lastRun?.notBalanced
  if (!stale && !notBalanced) return null
  return (
    <div className="di-banner di-banner--warn di-run-banner" role="alert">
      <AlertTriangle size={14} aria-hidden="true" />
      <span>
        {stale
          ? 'Counts changed since the last run — run balancing again. The results below are out of date.'
          : 'Not balanced: balancing was switched off for this run, so these are your raw counts, unchanged.'}
      </span>
      {stale && (onRun
        ? <button type="button" className="di-btn-primary di-btn-primary--sm" disabled={running} onClick={onRun}>Run balancing again</button>
        : <Link to="/analysis" className="di-btn-primary di-btn-primary--sm">Run balancing again</Link>)}
    </div>
  )
}
