import { detailsModalData } from '../../lib/reviewMovements'
import { flagPresentation } from '../../lib/flagActions'

// Every flag identifies its source and has the existing edit action beside it.
export default function FlagIndicator({
  lastRun, intNum, period, onGoToVerdict, onEditCounts, onReviewMap, onAdjustBalancing,
}) {
  const { flags } = detailsModalData(lastRun, intNum, period)
  if (flags.length === 0) return null
  const actions = {
    analysis: onGoToVerdict,
    volumes: onEditCounts,
    map: onReviewMap,
    balancing: onAdjustBalancing,
  }
  return (
    <section className="di-rm-card di-rm-flags" aria-label="Reconciliation flags">
      <h3 className="di-rm-card__label">
        Check the source · {flags.length} flag{flags.length === 1 ? '' : 's'}
      </h3>
      <ul className="di-rm-flags__list">
        {flags.map((flag, index) => {
          const item = flagPresentation(flag, intNum)
          return (
            <li className="di-rm-flag" key={index}>
              <div className="di-rm-flag__detail">
                <strong className="di-rm-flag__title">{item.title}</strong>
                <p className="di-rm-flag__message">{item.message}</p>
              </div>
              <button type="button" className="di-btn-ghost di-rm-flag__action"
                onClick={actions[item.action]}>
                {item.actionLabel}
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
