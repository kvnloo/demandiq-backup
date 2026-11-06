// Presentation only: QC flags remain solver output; fixes reuse edit routes.
const VOLUME_FLAGS = new Set(['SOURCE-OUTLIER', 'FRACTIONAL-VOLUME'])

export function flagPresentation(flag, intNum) {
  const message = String(flag.message ?? '')
  const check = String(flag.check ?? 'QC flag')
  // Only name a movement when the source actually supplies a movement code.
  const movement = /\b((?:EB|WB|NB|SB)[ULTR])\b/.exec(message)?.[1]
    ?? (typeof flag.movement === 'string' && /^(?:EB|WB|NB|SB)[ULTR]$/.test(flag.movement)
      ? flag.movement : null)
  const approach = /\b(EB|WB|NB|SB)\s+approach\b/i.exec(message)?.[1]
  const subject = movement ?? (approach ? approach.toUpperCase() + ' approach' : 'Int ' + intNum)

  let action = 'analysis'
  let actionLabel = 'Review analysis'
  if (VOLUME_FLAGS.has(check)) {
    action = 'volumes'
    actionLabel = 'Edit counts'
  } else if (check === 'GEOMETRY-FALLBACK') {
    action = 'map'
    actionLabel = 'Review map'
  } else if (check === 'NCHRP-CONV') {
    action = 'balancing'
    actionLabel = 'Adjust balancing'
  }
  return { title: subject + ' · ' + check, message, action, actionLabel }
}
