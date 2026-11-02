// Which existing-count steps are finished, from project state alone — drives the
// green "done" lamps in the rail. Steps with no saved outcome (Results, Export)
// are never marked: a lamp only turns green when there is data behind it.
// Pure (takes the snapshot) so it is testable without a DOM.
const hasKeys = (o) => Boolean(o) && Object.keys(o).length > 0

// Mirrors projectStore.isIntersectionConfigured, but on a plain object.
const configured = (it) => {
  const a = it.approaches
  if (!a) return false
  return ['EB', 'WB', 'NB', 'SB'].every((d) => !a[d] || a[d].exists === false || a[d].lanes?.length > 0)
}

export function doneSteps(snap = {}) {
  const ints = snap.intersections ?? []
  const some = ints.length > 0
  const analysed = ints.some((it) => hasKeys(it.balanced?.AM) || hasKeys(it.balanced?.PM))
  return new Set([
    snap.project?.name?.trim() && 1,
    some && 2,
    some && ints.every(configured) && 3,
    some && ints.every((it) => hasKeys(it.volumes?.AM) || hasKeys(it.volumes?.PM)) && 4,
    analysed && 5, // balancing settings count as used once a run balanced with them
    analysed && 6,
  ].filter(Boolean))
}
