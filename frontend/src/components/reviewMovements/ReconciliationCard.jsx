import { reconciliationVerdict, statusClass } from '../../lib/reviewMovements'

// Solver verdict stays read-only; edits use existing settings/analysis screens.
export default function ReconciliationCard({
  lastRun, intNum, period, onAdjustBalancing, onReviewVerdict,
}) {
  const v = reconciliationVerdict(lastRun, intNum, period)
  const glyph = v.status === 'green' ? '✓' : '⚠'
  const needsSettings = v.state === 'did-not-converge'
  return (
    <div className="di-rm-card">
      <div className="di-rm-card__label">Flow reconciles</div>
      <div className={'di-rm-status ' + statusClass(v.status)}>
        <span aria-hidden="true">{glyph}</span>
        <span>{v.label}</span>
      </div>
      {v.status !== 'green' && (
        <button type="button" className="di-btn-ghost di-rm-fix"
          onClick={needsSettings ? onAdjustBalancing : onReviewVerdict}>
          {needsSettings ? 'Adjust balancing' : 'Review analysis'}
        </button>
      )}
    </div>
  )
}
