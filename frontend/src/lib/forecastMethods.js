// Link-level growth methods (NCHRP 255 Ch. 4 / NCHRP 765 Ch. 6 / ODOT APM Ch. 6).
// All model methods but Ratio add the model's change in vehicles, so they need K.
export const METHODS = [
  { id: 'ratio', label: 'Ratio (growth)', help: 'Count × model future ÷ model base. NCHRP 765 default; works without K.' },
  { id: 'difference', label: 'Difference (incremental)', help: 'Count + model change. Best where the model grows a lot or starts near zero.' },
  { id: 'combined', label: 'Average of ratio and difference', help: 'NCHRP 255 averaging. NCHRP 765 no longer recommends it.' },
  { id: 'weighted', label: 'Weighted growth', help: 'Ratio leaning toward difference as model growth rises (NCHRP 765 Table 6-2, ODOT).' },
  { id: 'modified_average', label: 'Modified average', help: 'Average of weighted growth and difference (NCHRP 765 Table 6-2, ODOT).' },
  { id: 'nchrp765', label: 'Auto — NCHRP 765 rule', help: 'Picks per leg by how far the count is from the model (Table 6-2).' },
  { id: 'odot', label: 'Auto — ODOT decision tree', help: 'Picks per leg by model change and method agreement (ODOT APM Ch. 6).' },
  { id: 'trend', label: 'Historical trend (no model)', noModel: true, help: 'Fits your past counts (straight line or curve, whichever fits better) and grows every approach by it (NCHRP 765 §10.1). Good when the future looks like the past.' },
  { id: 'rate', label: 'Growth rate I enter (no model)', noModel: true, help: 'One yearly rate for every approach, straight-line. Negative for a decline. Quick studies with no model.' },
]

// How road forecasts become turning movements.
export const TURN_METHODS = [
  { id: 'furness', short: 'NCHRP standard turns', label: 'NCHRP standard — turns per intersection, then balance', help: "Each intersection keeps today's turn pattern, scaled to its forecast approach and exit totals (Furness, NCHRP 765 §6.4); then the corridor is balanced so neighbours agree (§6.9)." },
  { id: 'fratar', short: 'Fratar trip table', label: 'Trip table — Fratar', help: 'Works out the trips through the corridor from today\'s counts (where they enter → where they leave), grows them to each end and side street\'s forecast (Fratar), and adds them up into turns. Neighbours agree by design. Uses only end and side-street forecasts.' },
  { id: 'fuzzy', short: 'Fuzzy trip table', label: 'Trip table — fuzzy (Visum-style)', help: "The Fratar trip table, then nudged so every leg — main street included — lands within ± the band of its own forecast. Like PTV Visum's TFlowFuzzy: each forecast counts as \"about right\"." },
]

export const NO_MODEL = new Set(METHODS.filter((m) => m.noModel).map((m) => m.id))

// Model time periods per region — the denominator of K (peak hour ÷ model period).
// SANDAG: Regional Plan Appendix M Table M.2; MTC: TM1 TimePeriods.
export const MODEL_PERIODS = {
  SANDAG: { AM: '6–9 am (3 h)', PM: '3:30–7 pm (3.5 h)', typicalK: '0.30–0.45' },
  MTC: { AM: '6–10 am (4 h)', PM: '3–7 pm (4 h)', typicalK: '0.25–0.35' },
}
export const K_WARN = { min: 0.15, max: 0.6 }

// One past count per line: "2015 18200", "2015, 18,200", "2015;18200".
// Returns { points: [{year, volume}], dropped: [line text] } — every non-blank
// line that isn't a 4-digit year + positive volume is reported, never ignored.
export function readTrend(text) {
  const points = []
  const dropped = []
  for (const raw of String(text || '').split(/\n+/)) {
    const line = raw.trim()
    if (!line) continue
    const m = line.match(/^(\d{4})\s*[\s,;\t]\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)$/)
    const year = m ? Number(m[1]) : NaN
    const volume = m ? Number(m[2].replace(/,/g, '')) : NaN
    if (year > 1900 && volume > 0) points.push({ year, volume })
    else dropped.push(line)
  }
  return { points, dropped }
}

export const parseTrend = (text) => readTrend(text).points
