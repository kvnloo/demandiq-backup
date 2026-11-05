// Shared /forecast/run payload assembly + POST. Used by F3 (initial run) and F4
// (scenario re-run), so the request shape is defined in exactly one place — the
// scenario factor rides the SAME payload the baseline run sends, plus one field.
import { getIntersections, getLinks, resolveLinkMode } from '../store/projectStore.js'
import { NO_MODEL, parseTrend } from './forecastMethods.js'

const DIRS = ['NB', 'SB', 'EB', 'WB']

// The settings a run is actually made under: refineMethod collapses to 'ratio'
// unless a valid K unlocked a K method (the backend would fall back the same way).
export function runSettings(settings) {
  const s = settings ?? {}
  const kRaw = s.peakHourFactor
  const kValid = kRaw !== '' && kRaw != null && Number(kRaw) > 0
  const chosen = s.refineMethod || 'ratio'
  const refineMethod = NO_MODEL.has(chosen) || kValid ? chosen : 'ratio'
  return {
    refineMethod,
    turnMethod: s.turnMethod || 'furness',
    method: s.method ?? 'larger',
    peakHourFactor: kValid ? Number(kRaw) : '',
    fuzzyBand: s.fuzzyBand === '' || s.fuzzyBand == null ? 5 : Number(s.fuzzyBand),
    trendText: refineMethod === 'trend' ? (s.trendText ?? '') : '',
    growthRate: refineMethod === 'rate' ? (s.growthRate ?? '') : '',
  }
}

// What a network pull depended on (F1 region + model years).
export const pullKey = (fc) => JSON.stringify([
  fc?.region || 'SANDAG',
  (fc?.networks ?? []).map((n) => [n.role, Number(n.scenarioYear)]),
])

// What a forecast run depended on. Results are stale when this no longer matches
// the key stored with them — so old numbers are never shown under new labels.
export function runKey(fc, settings) {
  const rs = runSettings(settings)
  const model = NO_MODEL.has(rs.refineMethod) ? null : [pullKey(fc), fc?.matches ?? null]
  return JSON.stringify([model, String(fc?.baseYear ?? ''), String(fc?.horizonYear ?? ''), rs])
}

// The snapshot stored next to results: labels F4/S8 show come from here.
export function runRecord(fc, settings) {
  return {
    key: runKey(fc, settings),
    region: fc?.region || 'SANDAG',
    baseYear: fc?.baseYear ?? '',
    horizonYear: fc?.horizonYear ?? '',
    modelYears: { base: fc?.layers?.base?.scenario_year ?? null, horizon: fc?.layers?.horizon?.scenario_year ?? null },
    settings: runSettings(settings),
  }
}

export const isMatchStale = (fc) => !!fc?.matches && fc.pulledFor !== pullKey(fc)
export const isRunStale = (fc, settings) => !!fc?.results && fc.run?.key !== runKey(fc, settings ?? fc.settings)

// Plain-words error for a banner: a failed fetch means the server is unreachable.
export const friendlyError = (err) => (err instanceof TypeError
  ? "Couldn't reach the DemandIQ server — check your connection and try again."
  : err?.message || 'Something went wrong.')

export function buildRunPayload({ snap, settings, scenarioFactor = null }) {
  const fc = snap.forecasting ?? {}
  const matches = fc.matches ?? []
  const intersections = getIntersections()
  const byId = new Map(intersections.map((it) => [it.id, it.num]))

  // Balanced existing TMCs → {AM:{num:{mv}}, PM:{...}} (the count C).
  const balanced_tmcs = { AM: {}, PM: {} }
  for (const it of intersections) {
    for (const p of ['AM', 'PM']) {
      const b = it.balanced?.[p]
      if (b && Object.keys(b).length) balanced_tmcs[p][it.num] = b
    }
  }

  // Corridor links by num, with their effective (S5) modes.
  const links = getLinks()
    .map((l) => ({
      fromNum: byId.get(l.fromId),
      toNum: byId.get(l.toId),
      direction: l.direction,
      mode: resolveLinkMode(l.fromId, l.toId),
    }))
    .filter((l) => l.fromNum != null && l.toNum != null)

  const coords = {}
  for (const it of intersections) coords[it.num] = { lat: it.lat, lng: it.lng }

  // Manual targets for unmatched legs the engineer entered by hand.
  const manual_targets = {}
  for (const m of matches) {
    for (const d of DIRS) {
      const leg = m.legs?.[d]
      if (leg?.status === 'unmatched' && leg.decision === 'manual' && leg.manual) {
        manual_targets[m.intersection_num] ??= {}
        manual_targets[m.intersection_num][d] = {
          inbound: Number(leg.manual.inbound),
          outbound: Number(leg.manual.outbound),
        }
      }
    }
  }

  const periods = snap.project?.analysisPeriods?.length
    ? snap.project.analysisPeriods
    : ['AM', 'PM']

  const rs = runSettings(settings)
  const noModel = NO_MODEL.has(rs.refineMethod)

  const payload = {
    layers: noModel ? null : fc.layers,
    matches,
    balanced_tmcs,
    links,
    coords,
    periods,
    method: rs.method,
    refine_method: rs.refineMethod,
    peak_hour_factor: rs.peakHourFactor === '' ? null : rs.peakHourFactor,
    manual_targets,
    // Analysis years: the backend interpolates the model runs to these.
    turn_method: rs.turnMethod,
    fuzzy_band_pct: rs.fuzzyBand,
    trend_points: rs.refineMethod === 'trend' ? parseTrend(rs.trendText) : undefined,
    growth_rate_pct: rs.refineMethod === 'rate' ? rs.growthRate : undefined,
    count_year: fc.baseYear ? Number(fc.baseYear) : null,
    horizon_year: fc.horizonYear ? Number(fc.horizonYear) : null,
  }
  // Scenario demand-reduction factor — the engineer's mitigation ASSUMPTION; it
  // scales the VMT figures only. Only added when set; the tool never
  // generates or suggests one.
  if (scenarioFactor != null) payload.scenario_reduction_factor = scenarioFactor
  return payload
}

export async function runForecast({ snap, settings, scenarioFactor = null }) {
  const res = await fetch('/api/forecast/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildRunPayload({ snap, settings, scenarioFactor })),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { const j = await res.json(); if (j?.detail) detail = j.detail } catch { /* non-JSON */ }
    throw new Error(detail)
  }
  return res.json()
}
