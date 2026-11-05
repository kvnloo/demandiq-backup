import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useNavigate } from 'react-router-dom'
import NeedsStep from '../components/NeedsStep'
import { ChevronDown, Lock, Info, X as XIcon } from 'lucide-react'

import {
  subscribe,
  getSnapshot,
  setBalancing,
  setLinkBalanceMode,
  ensureBalancingDefaults,
  resolveLinkMode,
} from '../store/projectStore'
import { haversineMi } from '../lib/geo'
import { intLabel } from '../lib/labels'

import '../styles/forms.css'
import '../styles/map.css' // .di-banner, .di-btn-primary, .di-btn-secondary
import '../styles/balancing.css'

const DIR_OPTS = [
  { value: 'EB/WB', label: 'EB/WB' },
  { value: 'NB/SB', label: 'NB/SB' },
]
const PEAK_MODE_OPTS = [
  { value: 'Corridor', label: 'Corridor (shared peak)' },
  { value: 'Independent', label: 'Independent (per-intersection peak)' },
]
// Stage 4 / NCHRP rewrite: link disagreement resolution. Default is "larger"
// (max of the two adjacent counts) which is conservative for design-year work.
// "average" matches standard NCHRP report practice. See the info modal for
// the full rationale.
const LINK_RES_OPTS = [
  { value: 'larger',  label: 'Larger value' },
  { value: 'average', label: 'Average' },
]
const LINK_MODES = ['Full', 'Land Use', 'None']

// hh:mm string compare works because format is fixed-width zero-padded
const timeLT = (a, b) => a < b
const timeLE = (a, b) => a <= b

function hasAnyVolumes(intersections) {
  for (const it of intersections) {
    for (const period of ['AM', 'PM']) {
      const v = it.volumes?.[period]
      if (v && Object.keys(v).length > 0) return true
    }
  }
  return false
}

export default function S5_BalancingSettings() {
  const navigate = useNavigate()
  const snap = useSyncExternalStore(subscribe, getSnapshot, () => ({}))
  const intersections = snap.intersections || []
  const balancing = snap.balancing || {}

  const needsVolumes = intersections.length === 0 || !hasAnyVolumes(intersections)

  // ── One-time defaults ───────────────────────────────────────────────
  const didInit = useRef(false)
  useEffect(() => {
    if (didInit.current || needsVolumes) return
    didInit.current = true
    ensureBalancingDefaults()
  }, [needsVolumes])


  const [advancedOpen, setAdvancedOpen] = useState(false)
  // Modal toggles must be declared before the needsVolumes early return below —
  // a hook after a conditional return changes the hook count between renders
  // (rules-of-hooks crash when needsVolumes flips).
  const [linkInfoOpen, setLinkInfoOpen] = useState(false)

  if (needsVolumes) {
    return <NeedsStep title="Enter volumes first" body="Balancing needs counts to balance." to="/volumes" label="Go to Volume Entry" />
  }

  // Pull current values, with safe fallbacks during the brief window before the
  // defaults effect persists. UI stays consistent either way.
  const enabled        = balancing.enabled ?? true
  const linkResolution = balancing.linkResolution ?? 'larger'
  const corrDir        = balancing.corridorDir ?? 'EB/WB'

  // (linkInfoOpen hoisted above the early return — see note there.)
  const peakMode   = balancing.peakMode ?? 'Corridor'
  const am         = balancing.amWindow ?? { start: '07:00', end: '09:00' }
  const pm         = balancing.pmWindow ?? { start: '16:00', end: '18:00' }
  const convStored = balancing.convergenceThreshold ?? 0.01
  const maxIter    = balancing.maxIterations ?? 500

  // ── Validation ──────────────────────────────────────────────────────
  const errors = {
    amEnd: !timeLT(am.start, am.end) ? 'AM end must be after AM start' : null,
    pmEnd: !timeLT(pm.start, pm.end) ? 'PM end must be after PM start' : null,
    pmStart: !timeLE(am.end, pm.start) ? 'PM window cannot overlap AM' : null,
    conv: !(convStored > 0 && convStored * 100 >= 0.1 && convStored * 100 <= 5)
      ? 'Must be between 0.1% and 5.0%'
      : null,
    iter: !(maxIter >= 10 && maxIter <= 1000)
      ? 'Must be between 10 and 1000'
      : null,
  }
  const isValid = Object.values(errors).every((e) => e === null)

  // ── Per-link table: the corridor's actual links (S2), not neighbours in
  // list order — these are exactly the links S6 sends, with the same mode.
  const byId = new Map(intersections.map((it) => [it.id, it]))
  const pairs = (snap.links ?? [])
    .filter((L) => byId.has(L.fromId) && byId.has(L.toId))
    .map((L) => {
      const a = byId.get(L.fromId), b = byId.get(L.toId)
      return { id: `${a.id}__${b.id}`, a, b, distance: haversineMi(a, b), mode: resolveLinkMode(a.id, b.id) }
    })

  // ── Mode-count breakdown for summary ───────────────────────────────
  const modeCounts = pairs.reduce(
    (acc, p) => {
      acc[p.mode] = (acc[p.mode] ?? 0) + 1
      return acc
    },
    { Full: 0, 'Land Use': 0, None: 0 }
  )

  return (
    <div className="di-s5">
      <div className="di-s5__body">

        {/* ────────── LEFT FORM ────────── */}
        <div className="di-s5__form">

          {/* Section A — Balancing */}
          <section className="di-s5-section">
            <h3 className="di-s5-section__title">Balancing</h3>

            <div className="di-s5-row">
              <div className="di-s5-row__label">
                Balance volumes
                <div className="di-s5-row__sub">When off, your raw counts are used unchanged and every result is labelled “not balanced”.</div>
              </div>
              <ToggleSwitch checked={enabled} onChange={(v) => setBalancing({ enabled: v })} />
            </div>

            <div className="di-s5-row">
              <div className="di-s5-row__label">
                <span className="di-s5-row__label-with-info">
                  Link disagreement resolution
                  <button
                    type="button"
                    className="di-s5-info-btn"
                    aria-label="What is link disagreement resolution?"
                    onClick={() => setLinkInfoOpen(true)}
                  >
                    <Info size={14} />
                  </button>
                </span>
                <div className="di-s5-row__sub">
                  Neighbouring intersections often count different volumes on the street
                  between them (different days or crews). Balancing first settles each link on
                  one target volume, then fits the turning movements to it.
                </div>
              </div>
              <RadioGroup
                options={LINK_RES_OPTS}
                value={linkResolution}
                disabled={!enabled}
                onChange={(v) => setBalancing({ linkResolution: v })}
              />
            </div>

            <div className="di-s5-row">
              <div className="di-s5-row__label">Corridor direction</div>
              <Segmented
                options={DIR_OPTS}
                value={corrDir}
                disabled={!enabled}
                onChange={(v) => setBalancing({ corridorDir: v })}
              />
            </div>

            <div className="di-s5-row">
              <div className="di-s5-row__label">Peak hour mode</div>
              <Segmented
                options={PEAK_MODE_OPTS}
                value={peakMode}
                disabled={!enabled}
                onChange={(v) => setBalancing({ peakMode: v })}
              />
            </div>
          </section>

          {/* Section B — Peak windows */}
          <section className="di-s5-section">
            <h3 className="di-s5-section__title">Peak windows</h3>

            <div className="di-s5-stack">
              <div className="di-s5-stack__label">AM window</div>
              <div className="di-s5-time">
                <input
                  type="time"
                  value={am.start}
                  disabled={!enabled}
                  onChange={(e) => setBalancing({ amWindow: { ...am, start: e.target.value } })}
                />
                <span className="di-s5-time__sep">—</span>
                <input
                  type="time"
                  value={am.end}
                  disabled={!enabled}
                  onChange={(e) => setBalancing({ amWindow: { ...am, end: e.target.value } })}
                />
              </div>
              {errors.amEnd && <div className="di-s5-err">{errors.amEnd}</div>}
              <div className="di-s5-stack__sub">
                For your records and the report. Balancing uses the peak-hour counts you entered; these windows don&apos;t change the numbers.
              </div>
            </div>

            <div className="di-s5-stack">
              <div className="di-s5-stack__label">PM window</div>
              <div className="di-s5-time">
                <input
                  type="time"
                  value={pm.start}
                  disabled={!enabled}
                  onChange={(e) => setBalancing({ pmWindow: { ...pm, start: e.target.value } })}
                />
                <span className="di-s5-time__sep">—</span>
                <input
                  type="time"
                  value={pm.end}
                  disabled={!enabled}
                  onChange={(e) => setBalancing({ pmWindow: { ...pm, end: e.target.value } })}
                />
              </div>
              {errors.pmStart && <div className="di-s5-err">{errors.pmStart}</div>}
              {errors.pmEnd && <div className="di-s5-err">{errors.pmEnd}</div>}
              <div className="di-s5-stack__sub">
                For your records and the report. Balancing uses the peak-hour counts you entered; these windows don&apos;t change the numbers.
              </div>
            </div>
          </section>

          {/* Section C — iteration controls */}
          <section className="di-s5-section">
            <h3 className="di-s5-section__title">When to stop balancing</h3>
            <div className="di-s5-stack__sub" style={{ marginBottom: 8 }}>
              Each intersection is adjusted in repeated passes until its volumes stop changing
              (convergence threshold) or the pass limit is reached.
            </div>

            <div className="di-s5-row">
              <div className="di-s5-row__label">Convergence threshold</div>
              <div>
                <NumWithSuffix
                  value={Math.round(convStored * 1000) / 10}
                  suffix="%"
                  step={0.1}
                  min={0.1}
                  max={5}
                  err={!!errors.conv}
                  disabled={!enabled}
                  onChange={(v) =>
                    setBalancing({ convergenceThreshold: v == null ? null : v / 100 })
                  }
                />
                {errors.conv && <div className="di-s5-err">{errors.conv}</div>}
              </div>
            </div>

            <div className="di-s5-row">
              <div className="di-s5-row__label">Max passes (iterations)</div>
              <div>
                <NumWithSuffix
                  value={maxIter}
                  step={10}
                  min={10}
                  max={1000}
                  err={!!errors.iter}
                  disabled={!enabled}
                  onChange={(v) =>
                    setBalancing({ maxIterations: v == null ? null : Math.round(v) })
                  }
                />
                {errors.iter && <div className="di-s5-err">{errors.iter}</div>}
              </div>
            </div>
          </section>

          {/* Section D — Locked rules */}
          <section className="di-s5-section">
            <h3 className="di-s5-section__title">Locked rules</h3>
            <div className="di-s5-locked">
              <div className="di-s5-locked__row">
                <Lock size={12} className="di-s5-locked__icon" />
                <span><b>Rounding:</b> balanced volumes are rounded up to the next multiple of 5; a counted 0 stays 0</span>
              </div>
              <div className="di-s5-locked__row">
                <Lock size={12} className="di-s5-locked__icon" />
                <span><b>U-turns:</b> extracted but excluded from balancing (pass-through)</span>
              </div>
            </div>
          </section>

          {/* Advanced — per-link */}
          <section className="di-s5-section">
            {pairs.length === 0 ? (
              <div className="di-s5-disc__note" style={{ padding: '12px 0' }}>
                No links between intersections, so there are no per-link settings. Link intersections on the map (step 2) to balance them as a corridor.
              </div>
            ) : (
              <div className={`di-s5-disc${advancedOpen ? ' di-s5-disc--open' : ''}`}>
                <button
                  type="button"
                  className="di-s5-disc__btn"
                  onClick={() => setAdvancedOpen((o) => !o)}
                  aria-expanded={advancedOpen}
                >
                  <span>Advanced — per-link balance modes</span>
                  <ChevronDown size={16} className="di-s5-disc__chev" />
                </button>
                {advancedOpen && (
                  <div className="di-s5-disc__body">
                    <div className="di-s5-link__head di-s5-link--2col">
                      <span>Link</span>
                      <span>Mode</span>
                    </div>
                    {pairs.map((p) => (
                      <div key={p.id} className="di-s5-link di-s5-link--2col">
                        <div className="di-s5-link__label">
                          {intLabel(p.a)} → {intLabel(p.b)}
                          <span className="di-s5-link__dist">({p.distance.toFixed(1)} mi)</span>
                        </div>
                        <select
                          className="di-select--sm"
                          value={p.mode}
                          disabled={!enabled}
                          onChange={(e) => setLinkBalanceMode(p.id, e.target.value, 0)}
                        >
                          {LINK_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
                        </select>
                      </div>
                    ))}
                    <div className="di-s5-stack__sub" style={{ marginTop: 10 }}>
                      <b>Full</b>: the two counts on the link are made to agree. <b>Land Use</b>: both
                      intersections are balanced, but a gap between them is allowed (a driveway or
                      development between them adds or removes traffic) and reported. <b>None</b>: the
                      link is ignored.
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>

        {/* ────────── RIGHT SUMMARY ────────── */}
        <aside className="di-s5__summary">
          <h3 className="di-s5-sum__title">Configuration</h3>
          <p className="di-s5-sum__sub">
            Method: NCHRP 255/765 turning-movement balancing. Each link gets one target
            volume, then each intersection&apos;s movements are scaled to meet its targets
            and rounded up to the next 5. Individual movements can still end up slightly
            below their raw count — Run Analysis lists every drop for review.
          </p>
          {!enabled ? (
            <div className="di-s5-sum--off">Balancing disabled — values will pass through.</div>
          ) : (
            <div className="di-s5-sum__list">
              <SumRow k="Link resolution" v={linkResolution === 'larger' ? 'Larger value' : 'Average'} />
              <SumRow k="Direction" v={corrDir} />
              <SumRow
                k="Peak mode"
                v={peakMode === 'Corridor' ? 'Corridor (shared)' : 'Independent (per-int)'}
              />
              <SumRow k="AM" v={`${am.start} – ${am.end}`} />
              <SumRow k="PM" v={`${pm.start} – ${pm.end}`} />
              <SumRow k="Convergence" v={`${(convStored * 100).toFixed(1)}%`} />
              <SumRow k="Max iterations" v={String(maxIter)} />
              {pairs.length > 0 && (
                <div className="di-s5-sum__row">
                  <span className="di-s5-sum__key">Links</span>
                  <span className="di-s5-sum__val">
                    {pairs.length}
                    <div className="di-s5-sum__breakdown">
                      Full: {modeCounts.Full} · Land Use: {modeCounts['Land Use']} · None: {modeCounts.None}
                    </div>
                  </span>
                </div>
              )}
            </div>
          )}
        </aside>
      </div>

      <footer className="di-s5__footer">
        <button
          type="button"
          className="di-btn-primary"
          disabled={!isValid}
          onClick={() => navigate('/analysis')}
        >
          Continue to analysis →
        </button>
      </footer>

      {linkInfoOpen && <LinkResolutionInfoModal onClose={() => setLinkInfoOpen(false)} />}
    </div>
  )
}

// ── Link disagreement resolution — info modal ─────────────────────────────
function LinkResolutionInfoModal({ onClose }) {
  // Close on Esc, lock body scroll
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [onClose])

  return (
    <div className="di-modal-backdrop" onMouseDown={onClose}>
      <div
        className="di-modal di-modal--info"
        role="dialog"
        aria-modal="true"
        aria-labelledby="di-link-info-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="di-modal__header">
          <h2 id="di-link-info-title" className="di-modal__title">
            Link Disagreement Resolution
          </h2>
          <button
            type="button"
            className="di-modal__close"
            aria-label="Close"
            onClick={onClose}
          >
            <XIcon size={16} />
          </button>
        </div>
        <div className="di-modal__body di-link-info__body">
          <p>
            Adjacent intersection counts on the same segment often disagree because counts
            are taken on different days, by different crews, or for different durations.
            The standard method (NCHRP Reports 255 and 765) settles each disagreement on one
            value before balancing the turning movements.
          </p>
          <p>
            <strong>Example:</strong> On a segment between Int 1 and Int 2, Int 1's
            outflow says 278 cars went east, but Int 2's inflow says only 164 cars
            arrived from the west. Which is correct?
          </p>
          <hr className="di-link-info__rule" />
          <h3 className="di-link-info__heading">Larger value (default)</h3>
          <p className="di-link-info__formula">Result: max(278, 164) = 278</p>
          <p>
            Treats the higher count as more reliable. Undercounting is more common than
            overcounting in field surveys. Use this for design-year capacity studies or
            when counts may have been short-duration. Note: this systematically biases
            balanced volumes upward, which is conservative for design purposes.
          </p>
          <h3 className="di-link-info__heading">Average</h3>
          <p className="di-link-info__formula">Result: (278 + 164) / 2 = 221</p>
          <p>
            Treats both counts as equally reliable — the usual practice in NCHRP reports.
            Use when both counts are equal quality.
          </p>
          <hr className="di-link-info__rule" />
          <p className="di-link-info__footnote">
            Either way, the <strong>link targets</strong> use the larger (or average) of the
            two counts. Individual turning movements can still drop a little below their
            collected value when an intersection is re-balanced to meet its targets — Run
            Analysis and Review movements list every drop. Final volumes are rounded up to
            the next multiple of 5.
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Radio group (vertical, 2-4 options) ──────────────────────────────────
function RadioGroup({ options, value, onChange, disabled }) {
  return (
    <div className="di-radio-group" role="radiogroup">
      {options.map((opt) => (
        <label
          key={opt.value}
          className={`di-radio${value === opt.value ? ' di-radio--on' : ''}${disabled ? ' di-radio--disabled' : ''}`}
        >
          <input
            type="radio"
            className="di-radio__input"
            value={opt.value}
            checked={value === opt.value}
            disabled={disabled}
            onChange={() => onChange(opt.value)}
          />
          <span className="di-radio__dot" />
          <span className="di-radio__label">{opt.label}</span>
        </label>
      ))}
    </div>
  )
}

// ── Inline helpers ──────────────────────────────────────────────────────

function ToggleSwitch({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      className={`di-toggle${checked ? ' di-toggle--on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="di-toggle__handle" />
    </button>
  )
}

function Segmented({ options, value, onChange, disabled }) {
  return (
    <div className="di-segmented">
      {options.map((opt) => {
        const isOn = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={isOn}
            disabled={disabled}
            className={`di-segmented__opt${isOn ? ' di-segmented__opt--on' : ''}`}
            onClick={() => onChange(opt.value)}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

function NumWithSuffix({ value, suffix, step, min, max, err, disabled, onChange }) {
  return (
    <div className={`di-s5-num${err ? ' di-s5-num--err' : ''}`}>
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        disabled={disabled}
        value={value ?? ''}
        onChange={(e) => {
          const raw = e.target.value
          onChange(raw === '' ? null : Number(raw))
        }}
      />
      {suffix && <span className="di-s5-num__suffix">{suffix}</span>}
    </div>
  )
}

function SumRow({ k, v }) {
  return (
    <div className="di-s5-sum__row">
      <span className="di-s5-sum__key">{k}</span>
      <span className="di-s5-sum__val">{v}</span>
    </div>
  )
}
