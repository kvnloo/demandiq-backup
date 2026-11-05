// Every note a forecast run carries for one period — leg-level (e.g. the
// extrapolation flag), inbound/outbound, Pass 1 and Pass 2 — one entry per
// distinct message with where it applies. F3 and F4 render these.
const DIRS = ['NB', 'SB', 'EB', 'WB']

// Notes that change how far the numbers can be trusted: shown up front, not collapsed.
export const WARN_CODES = new Set(['REFINE-EXTRAPOLATED', 'REFINE-EXTRAPOLATED-BELOW-ZERO', 'REFINE-DECLINE'])

export function collectNotes(data) {
  const byMsg = new Map()
  const note = (f, where) => {
    const cur = byMsg.get(f.message) ?? { code: f.code ?? f.check, message: f.message, where: [] }
    cur.where.push(where)
    byMsg.set(f.message, cur)
  }
  for (const r of data?.refinement ?? []) {
    for (const d of DIRS) {
      const leg = r.approaches?.[d]
      if (!leg) continue
      for (const f of leg.flags ?? []) note(f, `Int ${r.intersection_num} ${d}`)
      for (const io of ['inbound', 'outbound']) {
        for (const f of leg[io]?.flags ?? []) note(f, `Int ${r.intersection_num} ${d} ${io === 'inbound' ? 'in' : 'out'}`)
      }
    }
  }
  for (const [n, m] of Object.entries(data?.pass1?.meta ?? {})) {
    for (const f of m?.flags ?? []) note(f, `Int ${n}`)
  }
  return [...byMsg.values()]
}

// "Int 1 NB in, Int 1 NB out: msg" — or "msg (12 places)" when it is everywhere.
export const noteText = (n) => (n.where.length > 3 ? `${n.message} (${n.where.length} places)` : `${n.where.join(', ')}: ${n.message}`)

// Declines get ONE line ("falls on 7 legs: Int 2 NB in −2.4%/yr, …"), not a banner each.
export function declineSummary(notes) {
  const ds = notes.filter((n) => n.code === 'REFINE-DECLINE')
  if (!ds.length) return null
  const parts = ds.map((n) => `${n.where.length > 3 ? `${n.where.length} places` : n.where.join(', ')} ${n.message.match(/-?\d+(?:\.\d+)?%\/yr/)?.[0] ?? ''}`.trim())
  const legs = ds.reduce((k, n) => k + n.where.length, 0)
  return `Traffic falls on ${legs} approach/exit total${legs === 1 ? '' : 's'} — kept as is (no floor); note the reason in the report: ${parts.join('; ')}.`
}
