// Human label for an intersection in tables: number (to cross-reference the
// map and diagrams) plus its cross street, or full name when that is unknown.
export function intLabel(it, num) {
  if (!it) return `Int ${num}`
  return `${it.num} · ${it.crossStreet || it.name || `Int ${it.num}`}`
}
