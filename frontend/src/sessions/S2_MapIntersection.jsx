import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, RotateCcw, RotateCw, Trash2, AlertTriangle, X } from 'lucide-react'
import 'leaflet/dist/leaflet.css'
import '../styles/forms.css'
import '../styles/map.css'

import MapView from '../components/MapView'
import IntersectionList from '../components/IntersectionList'
import {
  subscribe,
  getSnapshot,
  setIntersections as persistIntersections,
  reorderIntersections,
  setCorridorDir,
  addLink,
  removeLink,
  clearProject,
  pushHistory,
  undo,
  redo,
  canUndo,
  canRedo,
  defaultApproaches,
} from '../store/projectStore'
import { reverseGeocode } from '../lib/photon'
import {
  bearing,
  deviationFromCardinal,
  detectCorridorDir,
  sortAlongCorridor,
  suggestDirection,
} from '../lib/geo'
import {
  approachesForControl,
  stopHint,
  incompleteStopControls,
  incompleteThreeLegs,
  withMissingLeg,
  missingLeg,
  STOP_CONTROL_TYPES,
  AUTO_FILL_TYPES,
  ACTIVE_DIRS,
} from '../lib/stopControl'

const PERPENDICULAR = {
  'EB/WB': 'NB/SB', 'NB/SB': 'EB/WB',
  'NE/SW': 'NW/SE', 'NW/SE': 'NE/SW',
}
const DIRS = ['EB/WB', 'NB/SB', 'NE/SW', 'NW/SE']
const CONTROLS = ['Signalized', 'Two-way stop', 'Side-street stop', 'One-way stop', 'All-way stop', 'Roundabout']
const LEGS = ['3-leg', '4-leg']
// An approach is named by travel direction: the NB approach comes from the south leg.
const APPROACH_FROM = { NB: 'south', SB: 'north', EB: 'west', WB: 'east' }

const newId = () =>
  typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `id-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

// S2 — modeless corridor editor.
//
// Interaction model (no hidden modes; the only lit "mode" is Add-placement):
//   • Select   — click a pin or a link → it highlights and the right Inspector
//                shows its editable properties. Always available.
//   • Add      — the lit "＋ Add intersection" pill arms placement; each map
//                click (pan-guarded) drops a pin + opens an inline add popover.
//                Stays lit so a whole corridor can be placed without re-arming.
//   • Connect  — select a pin → "Connect" (on the pin AND in the Inspector) →
//                click the target pin. Loud armed banner; Esc cancels.
//   • Undo/redo — snapshot history; every destructive action also toasts an Undo.
export default function S2_MapIntersection() {
  const navigate = useNavigate()
  const snap = useSyncExternalStore(subscribe, getSnapshot, () => ({}))
  const intersections = snap.intersections || []
  const links = snap.links || []
  // Corridor direction drives the smart stop-leg default. Prefer the balancing
  // long form ('EB/WB' | 'NB/SB'), fall back to the analysis short form.
  const corridorDir = snap.balancing?.corridorDir ?? snap.analysis?.corridorDir ?? null
  // Hard gate: a stop control with zero approaches selected would ship a stop-
  // less diagram. Block Continue until every stop control has at least one.
  const stopBlockers = incompleteStopControls(intersections)
  const legBlockers = incompleteThreeLegs(intersections)
  // A Single Intersection project is complete with one pin; corridors need two.
  const minCount = snap.project?.projectType === 'Single Intersection' ? 1 : 2
  // Count files and Jev match intersections by name, so duplicates are risky.
  const seenNames = new Map()
  for (const it of intersections) {
    const k = (it.name || '').trim().toLowerCase()
    if (k) seenNames.set(k, [...(seenNames.get(k) ?? []), it.num])
  }
  const dupNames = [...seenNames.values()].filter((nums) => nums.length > 1)
  const [renumber, setRenumber] = useState(null) // { dir, sorted } while the renumber notice is open
  const undoable = canUndo()
  const redoable = canRedo()

  // One selection at a time → one Inspector. { type:'intersection'|'link', id }
  const [selection, setSelection] = useState(null)
  const [addMode, setAddMode] = useState(false)        // sticky placement, lit pill
  const [connectFrom, setConnectFrom] = useState(null) // armed connect source id
  const [draft, setDraft] = useState(null)             // inline add popover state
  const [toast, setToast] = useState(null)             // { msg }
  const toastTimer = useRef(null)

  const selIntersection = selection?.type === 'intersection'
    ? intersections.find((it) => it.id === selection.id) ?? null
    : null
  const selLink = selection?.type === 'link'
    ? links.find((l) => l.id === selection.id) ?? null
    : null

  // A stale selection (id removed by undo/redo/delete) is harmless: the derived
  // selIntersection/selLink resolve to null and the Inspector renders empty.
  // The mutation handlers below also clear selection explicitly.

  const showToast = (msg) => {
    setToast({ msg })
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 6000)
  }
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current) }, [])

  // ── Mutations (each snapshots history BEFORE committing) ───────────────
  const addIntersection = (lat, lng, form, missing = null) => {
    pushHistory()
    // Seed the canonical approach shape at creation so the Inspector's stop-leg
    // checkboxes have something to bind to (S3 still default-fills any older pin
    // that lacks approaches). All four legs exist by default; S3 toggles exists.
    // Apply the stop-control rules for the chosen control up front, so a pin
    // created directly as All-way / Two-way stop already carries correct flags.
    const approaches = approachesForControl(withMissingLeg(defaultApproaches(), missing), form.control, corridorDir)
    persistIntersections([
      ...intersections,
      { id: newId(), num: intersections.length + 1, lat, lng, ...form, approaches },
    ])
  }

  const updateIntersection = (id, partial) => {
    pushHistory()
    persistIntersections(intersections.map((it) => (it.id === id ? { ...it, ...partial } : it)))
  }

  const deleteIntersectionById = (id) => {
    const it = intersections.find((x) => x.id === id)
    if (!it) return
    const linked = links.filter((l) => l.fromId === id || l.toId === id).length
    if (linked > 0) {
      const ok = window.confirm(
        `Delete intersection ${it.num} (${it.name || 'unnamed'})? ` +
        `This also removes ${linked} connected link${linked === 1 ? '' : 's'}. You can undo.`
      )
      if (!ok) return
    }
    pushHistory()
    const next = intersections
      .filter((x) => x.id !== id)
      .map((x, i) => ({ ...x, num: i + 1 }))
    persistIntersections(next)   // store cascade-cleans orphan links/modes/lastRun
    if (selection?.id === id) setSelection(null)
    if (connectFrom === id) setConnectFrom(null)
    showToast(linked > 0 ? `Removed intersection and ${linked} link${linked === 1 ? '' : 's'}` : 'Removed intersection')
  }

  // Connect toggles: if the pair is already linked, clicking the target unlinks
  // it; otherwise it creates the link (direction inferred from geometry).
  const toggleLink = (fromId, toId) => {
    const a = intersections.find((it) => it.id === fromId)
    const b = intersections.find((it) => it.id === toId)
    if (!a || !b) return
    const existing = links.find(
      (l) => (l.fromId === fromId && l.toId === toId) || (l.fromId === toId && l.toId === fromId)
    )
    pushHistory()
    if (existing) {
      removeLink(existing.id)
      if (selection?.id === existing.id) setSelection(null)
      showToast(`Unlinked Int ${a.num} ↔ Int ${b.num}`)
    } else {
      addLink({ fromId, toId, direction: suggestDirection(a, b), mode: 'Full' })
      showToast(`Linked Int ${a.num} ↔ Int ${b.num}`)
    }
  }

  const deleteLinkById = (id) => {
    pushHistory()
    removeLink(id)
    if (selection?.id === id) setSelection(null)
    showToast('Removed link')
  }

  const handleUndo = () => {
    if (undo()) { setSelection(null); setConnectFrom(null); setDraft(null) }
  }
  const handleRedo = () => {
    if (redo()) { setSelection(null); setConnectFrom(null); setDraft(null) }
  }

  // ── Add-placement popover ──────────────────────────────────────────────
  const openDraft = (latlng, pt) => {
    let skewDeg = null
    if (intersections.length > 0) {
      const last = intersections[intersections.length - 1]
      skewDeg = deviationFromCardinal(
        bearing({ lat: last.lat, lng: last.lng }, { lat: latlng.lat, lng: latlng.lng })
      )
    }
    const key = `${latlng.lat},${latlng.lng}`
    // Main-street direction follows the corridor the pins are laid along, so a
    // N–S corridor's pins don't default to EB/WB (which swaps street names).
    const along = intersections.length > 0 ? detectCorridorDir([...intersections, latlng]) : corridorDir
    const mainDir = along === 'NS' || along === 'NB/SB' ? 'NB/SB' : 'EB/WB'
    setDraft({
      lat: latlng.lat, lng: latlng.lng,
      pt: pt || { x: 120, y: 120 },
      key,
      name: '', control: 'Signalized', legs: '4-leg', missingLeg: '',
      mainDir, crossDir: PERPENDICULAR[mainDir],
      geocodeFailed: false, skewDeg,
    })
    reverseGeocode(latlng.lat, latlng.lng)
      .then((name) => setDraft((p) => (p && p.key === key && !p.name)
        ? { ...p, name: name || '', geocodeFailed: !name } : p))
      .catch(() => setDraft((p) => (p && p.key === key) ? { ...p, geocodeFailed: true } : p))
  }

  const commitDraft = () => {
    if (!draft) return
    const name = draft.name.trim()
    if (!name) return
    const parts = name.split('&').map((s) => s.trim())
    try {
      addIntersection(draft.lat, draft.lng, {
        name,
        control: draft.control,
        legs: draft.legs,
        mainStreet: parts[0] || '',
        crossStreet: parts[1] || '',
        mainDir: draft.mainDir,
        crossDir: draft.crossDir,
      }, draft.legs === '3-leg' ? draft.missingLeg : null)
      setDraft(null)   // sticky: addMode stays lit for the next placement
    } catch (err) {
      showToast(`Failed to add intersection: ${err?.message || err}`)
    }
  }

  // ── Map interaction ────────────────────────────────────────────────────
  const handleMapClick = (latlng, pt) => {
    if (connectFrom) { setConnectFrom(null); return }   // empty click cancels connect
    if (draft) return
    if (addMode) { openDraft(latlng, pt); return }
    setSelection(null)                                   // deselect
  }

  const handlePinClick = (id) => {
    if (connectFrom) {
      if (id !== connectFrom) toggleLink(connectFrom, id)
      setConnectFrom(null)
      return
    }
    setSelection({ type: 'intersection', id })
  }

  const handleLinkClick = (id) => {
    if (!id) return            // null = pan/zoom close from MapView → keep selection
    setConnectFrom(null)
    setSelection({ type: 'link', id })
  }

  const startConnect = (id) => {
    setSelection({ type: 'intersection', id })
    setConnectFrom((cur) => (cur === id ? null : id))
  }

  // ── Reorder + continue ─────────────────────────────────────────────────
  const handleReorder = (orderedIds) => {
    if (orderedIds.length !== intersections.length) return
    pushHistory()
    reorderIntersections(orderedIds)
  }

  const handleRemoveByIndex = (idx) => {
    const it = intersections[idx]
    if (it) deleteIntersectionById(it.id)
  }

  const canContinue = intersections.length >= minCount && stopBlockers.length === 0 && legBlockers.length === 0

  // Continue numbers intersections along the corridor. When that would change
  // anyone's number, show the new order first and let the engineer keep theirs
  // — a silent renumber sends Int#-keyed pastes to the wrong intersection.
  const handleContinue = () => {
    if (!canContinue) return // gated; see footer
    const dir = detectCorridorDir(intersections)
    const sorted = sortAlongCorridor(intersections, dir).map((it, i) => ({ ...it, num: i + 1 }))
    const byId = new Map(intersections.map((it) => [it.id, it.num]))
    if (sorted.some((it) => byId.get(it.id) !== it.num)) { setRenumber({ dir, sorted }); return }
    finishContinue(dir, null)
  }

  const finishContinue = (dir, sorted) => {
    setRenumber(null)
    if (sorted) { pushHistory(); persistIntersections(sorted) }
    setCorridorDir(dir)
    navigate('/approaches')
  }

  const resetProject = () => {
    if (!window.confirm('Reset the project? Everything in it is deleted from this browser and cannot be undone. Download the project file first (New project → Download project file) if you need it.')) return
    clearProject(); window.location.assign('/')
  }

  // ── Keyboard layer ─────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target
      const typing = t && t.matches && t.matches('input, select, textarea')
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) handleRedo(); else handleUndo()
        return
      }
      if (e.key === 'Escape') {
        if (draft) { setDraft(null); return }
        if (connectFrom) { setConnectFrom(null); return }
        if (addMode) { setAddMode(false); return }
        if (selection) { setSelection(null); return }
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && !typing && selection) {
        e.preventDefault()
        if (selection.type === 'intersection') deleteIntersectionById(selection.id)
        else deleteLinkById(selection.id)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }) // re-bind each render so the handler closes over current state

  const showEmptyCTA = intersections.length === 0 && !draft
  const showZeroLinkHint = intersections.length >= 2 && links.length === 0 && !connectFrom

  return (
    <div className="di-s2">
      <div className={`di-s2__map${addMode ? ' di-s2__map--adding' : ''}`}>
        {/* Top toolbar: Add pill + undo/redo (always visible, never hidden state) */}
        <div className="di-s2-tools">
          <button
            type="button"
            aria-pressed={addMode}
            className={`di-s2-tools__add${addMode ? ' di-s2-tools__add--on' : ''}`}
            onClick={() => { setAddMode((v) => !v); setConnectFrom(null) }}
            title="Place intersections by clicking the map"
          >
            <Plus size={14} /> {addMode ? 'Placing… (Esc to stop)' : 'Add intersection'}
          </button>
          <div className="di-s2-tools__sep" />
          <button
            type="button"
            className="di-s2-tools__icon"
            onClick={handleUndo}
            disabled={!undoable}
            title="Undo (⌘Z)"
            aria-label="Undo"
          ><RotateCcw size={14} /></button>
          <button
            type="button"
            className="di-s2-tools__icon"
            onClick={handleRedo}
            disabled={!redoable}
            title="Redo (⇧⌘Z)"
            aria-label="Redo"
          ><RotateCw size={14} /></button>
        </div>

        {/* Loud armed-connect banner (persistent until target picked / Esc) */}
        {connectFrom && (
          <div className="di-s2-armed" role="status">
            Click another intersection to link or unlink
            {' · '}<kbd>Esc</kbd> to cancel
          </div>
        )}

        {/* Empty-state CTA — non-interactive so it never eats the first click */}
        {showEmptyCTA && (
          <div className="di-s2-ghost" aria-hidden="true">
            <div className="di-s2-ghost__title">No intersections yet</div>
            <div className="di-s2-ghost__body">Click <strong>＋ Add intersection</strong>, then click the map to drop your first pin.</div>
          </div>
        )}
        {showZeroLinkHint && (
          <div className="di-s2-ghost di-s2-ghost--corner" aria-hidden="true">
            Select an intersection and press <strong>🔗 Link</strong> to connect it to another.
          </div>
        )}

        <MapView
          intersections={intersections}
          pendingPin={draft ? { lat: draft.lat, lng: draft.lng } : null}
          onMapClick={handleMapClick}
          mode="intersections"
          editablePins
          selectedId={selIntersection?.id ?? null}
          connectFromId={connectFrom}
          links={links}
          selectedLinkId={selLink?.id ?? null}
          onIntersectionClick={handlePinClick}
          onSelectLink={handleLinkClick}
          onConnectStart={startConnect}
          onDeleteIntersection={deleteIntersectionById}
          viewMode="edit"
        />

        {/* Inline add popover, anchored at the drop point */}
        {draft && (
          <AddPopover
            draft={draft}
            onChange={(patch) => setDraft((p) => ({ ...p, ...patch }))}
            onCommit={commitDraft}
            onCancel={() => setDraft(null)}
          />
        )}

        {/* Toast with inline Undo for every destructive action */}
        {toast && (
          <div className="di-s2-toast" role="status">
            <span>{toast.msg}</span>
            <button type="button" className="di-s2-toast__undo" onClick={() => { handleUndo(); setToast(null) }}>
              Undo
            </button>
            <button type="button" className="di-s2-toast__x" aria-label="Dismiss" onClick={() => setToast(null)}>
              <X size={13} />
            </button>
          </div>
        )}
      </div>

      <IntersectionList
        intersections={intersections}
        selectedId={selIntersection?.id ?? null}
        onSelect={(id) => handlePinClick(id)}
        onRemove={handleRemoveByIndex}
        onReorder={handleReorder}
        onContinue={handleContinue}
        canContinue={canContinue}
        minCount={minCount}
        stopBlockers={stopBlockers}
        legBlockers={legBlockers}
        dupNames={dupNames}
        inspector={
          <Inspector
            intersection={selIntersection}
            link={selLink}
            intersections={intersections}
            connectFrom={connectFrom}
            corridorDir={corridorDir}
            onUpdateIntersection={updateIntersection}
            onDeleteIntersection={deleteIntersectionById}
            onStartConnect={startConnect}
            onDeleteLink={deleteLinkById}
            onResetProject={resetProject}
          />
        }
      />
      {renumber && (
        <div className="di-modal-backdrop">
          <div className="di-modal" role="dialog" aria-modal="true" aria-labelledby="di-renum-title">
            <div className="di-modal__header">
              <h2 id="di-renum-title" className="di-modal__title">Number the intersections along the corridor?</h2>
            </div>
            <div className="di-modal__body">
              <p style={{ fontSize: 13, margin: '0 0 8px', lineHeight: 1.5 }}>
                Listed {renumber.dir === 'NS' ? 'south to north' : 'west to east'}, the intersections would be numbered:
              </p>
              <ol className="di-renum-list">
                {renumber.sorted.map((it) => {
                  const was = intersections.find((x) => x.id === it.id)?.num
                  return (
                    <li key={it.id}>
                      {it.num}. {it.name || 'Unnamed'}{was !== it.num ? ` (was ${was})` : ''}
                    </li>
                  )
                })}
              </ol>
            </div>
            <div className="di-modal__footer">
              <button type="button" className="di-btn-secondary" onClick={() => setRenumber(null)}>Back</button>
              <button type="button" className="di-btn-secondary" onClick={() => finishContinue(renumber.dir, null)}>Keep my numbering</button>
              <button type="button" className="di-btn-primary" onClick={() => finishContinue(renumber.dir, renumber.sorted)}>Renumber and continue</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Inline add popover (replaces the old full-screen Add modal) ───────────
function AddPopover({ draft, onChange, onCommit, onCancel }) {
  const ref = useRef(null)
  const nameRef = useRef(null)
  useEffect(() => { nameRef.current?.focus() }, [])
  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onCancel() }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [onCancel])

  const canAdd = draft.name.trim().length > 0 && (draft.legs !== '3-leg' || Boolean(draft.missingLeg))
  // Clamp the anchor so the card stays on screen near the drop point.
  const left = Math.min(Math.max(draft.pt.x, 150), 9999)

  return (
    <div
      ref={ref}
      className="di-add-pop"
      style={{ left, top: Math.max(draft.pt.y, 8) }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="di-add-pop__title">New intersection</div>
      {draft.geocodeFailed && !draft.name && (
        <div className="di-add-pop__hint">Auto-name unavailable — type it in.</div>
      )}
      {draft.skewDeg != null && draft.skewDeg > 20 && (
        <div className="di-add-pop__hint di-add-pop__hint--warn">
          <AlertTriangle size={12} /> ~{Math.round(draft.skewDeg)}° off cardinal — check directions.
        </div>
      )}
      <input
        ref={nameRef}
        className="di-input"
        type="text"
        value={draft.name}
        placeholder="e.g. Main St & 1st Ave"
        autoComplete="off"
        onChange={(e) => onChange({ name: e.target.value })}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); onCommit() }
          if (e.key === 'Escape') { e.preventDefault(); onCancel() }
        }}
      />
      <Segmented label="Control" value={draft.control} options={CONTROLS} onSelect={(v) => onChange({ control: v })} />
      <Segmented label="Legs" value={draft.legs} options={LEGS} onSelect={(v) => onChange({ legs: v })} />
      {draft.legs === '3-leg' && (
        <MissingLegSelect value={draft.missingLeg} onChange={(d) => onChange({ missingLeg: d })} className="di-add-pop__row" />
      )}
      <label className="di-add-pop__row">
        <span>Direction</span>
        <select
          className="di-select"
          value={draft.mainDir}
          onChange={(e) => onChange({ mainDir: e.target.value, crossDir: PERPENDICULAR[e.target.value] ?? e.target.value })}
        >
          {DIRS.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </label>
      <div className="di-add-pop__actions">
        <button type="button" className="di-btn-secondary di-add-pop__btn" onClick={onCancel}>Cancel</button>
        <button type="button" className="di-btn-primary di-add-pop__btn" disabled={!canAdd} onClick={onCommit}>Add ↵</button>
      </div>
    </div>
  )
}

// ── Unified right-side Inspector (pin OR link) ────────────────────────────
function Inspector({
  intersection, link, intersections, connectFrom, corridorDir,
  onUpdateIntersection, onDeleteIntersection, onStartConnect,
  onDeleteLink, onResetProject,
}) {
  if (!intersection && !link) {
    return (
      <div className="di-inspector di-inspector--empty">
        <p>Select an intersection or link to edit it. Use <strong>＋ Add intersection</strong> to place pins, then <strong>Connect</strong> to link them.</p>
        <button type="button" className="di-inspector__reset" onClick={onResetProject}>Reset project</button>
      </div>
    )
  }

  if (link) {
    const from = intersections.find((i) => i.id === link.fromId)
    const to = intersections.find((i) => i.id === link.toId)
    return (
      <div className="di-inspector">
        <div className="di-inspector__head">LINK · Int {from?.num ?? '?'} ↔ Int {to?.num ?? '?'}</div>
        <button type="button" className="di-inspector__delete" onClick={() => onDeleteLink(link.id)}>
          <Trash2 size={13} /> Delete link
        </button>
      </div>
    )
  }

  // intersection
  const armed = connectFrom === intersection.id

  // Stop-control state for the selected intersection.
  const control = intersection.control
  const approaches = intersection.approaches || {}
  const showStopRow = STOP_CONTROL_TYPES.has(control) || AUTO_FILL_TYPES.has(control)
  const autoFillStops = AUTO_FILL_TYPES.has(control)
  const stopDirs = ACTIVE_DIRS.filter((d) => approaches[d]?.exists === true)
  const stoppedCount = stopDirs.filter((d) => approaches[d]?.stopControlled).length

  // Changing control re-applies the per-control stop rules (AWS auto-fills,
  // Two-way/Side-street smart-default to the cross street, others clear) in the
  // SAME update as the control change so the diagram and checkboxes stay in sync.
  const handleControlChange = (v) => {
    const partial = { control: v }
    if (intersection.approaches) {
      partial.approaches = approachesForControl(intersection.approaches, v, corridorDir)
    }
    onUpdateIntersection(intersection.id, partial)
  }

  const toggleStop = (dir) => {
    if (autoFillStops) return
    const ap = approaches[dir]
    onUpdateIntersection(intersection.id, {
      approaches: { ...approaches, [dir]: { ...ap, stopControlled: !ap?.stopControlled } },
    })
  }

  return (
    <div className="di-inspector">
      <div className="di-inspector__head">Intersection {intersection.num}</div>
      <NameField
        key={intersection.id}
        value={intersection.name || ''}
        onCommit={(name) => onUpdateIntersection(intersection.id, { name })}
      />
      <Segmented label="Control" value={control} options={CONTROLS}
        onSelect={handleControlChange} />
      {showStopRow && (
        <div className="di-inspector__row di-inspector__row--stack">
          <span className="di-inspector__sublabel">Stop-controlled approaches</span>
          <div className="di-stopleg-grid">
            {stopDirs.map((dir) => {
              const checked = autoFillStops ? true : Boolean(approaches[dir]?.stopControlled)
              return (
                <label
                  key={dir}
                  className={`di-stopleg${checked ? ' di-stopleg--on' : ''}${autoFillStops ? ' di-stopleg--locked' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={autoFillStops}
                    onChange={() => toggleStop(dir)}
                  />
                  <span>{dir}</span>
                </label>
              )
            })}
          </div>
          {stopHint(control, stoppedCount) && (
            <span className="di-stopleg__hint">{stopHint(control, stoppedCount)}</span>
          )}
        </div>
      )}
      <Segmented label="Legs" value={intersection.legs} options={LEGS}
        onSelect={(v) => onUpdateIntersection(intersection.id, v === '4-leg'
          ? { legs: v, approaches: approachesForControl(withMissingLeg(approaches, null), control, corridorDir) }
          : { legs: v })} />
      {intersection.legs === '3-leg' && (
        <MissingLegSelect
          className="di-inspector__row"
          value={missingLeg(approaches) ?? ''}
          onChange={(d) => onUpdateIntersection(intersection.id, {
            approaches: approachesForControl(withMissingLeg(approaches, d), control, corridorDir),
          })}
        />
      )}
      <label className="di-inspector__row">
        <span>Direction</span>
        <select
          className="di-select"
          value={intersection.mainDir}
          onChange={(e) => onUpdateIntersection(intersection.id, {
            mainDir: e.target.value, crossDir: PERPENDICULAR[e.target.value] ?? e.target.value,
          })}
        >
          {DIRS.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </label>
      <div className="di-inspector__actions">
        <button
          type="button"
          className={`di-inspector__connect${armed ? ' di-inspector__connect--on' : ''}`}
          onClick={() => onStartConnect(intersection.id)}
        >{armed ? 'Click a target… (Esc)' : '🔗 Link'}</button>
        <button type="button" className="di-inspector__delete" onClick={() => onDeleteIntersection(intersection.id)}>
          <Trash2 size={13} /> Delete
        </button>
      </div>
    </div>
  )
}

// Text field that holds a local draft and commits once (on blur / Enter) so
// undo history coalesces to one snapshot per edit, not one per keystroke.
// Keyed by intersection id at the call site, so selecting a different pin
// remounts it with the new value (no prop→state sync effect needed).
function NameField({ value, onCommit }) {
  const [draft, setDraft] = useState(value)
  const commit = () => { if (draft.trim() !== value) onCommit(draft.trim()) }
  return (
    <label className="di-inspector__row di-inspector__row--stack">
      <span>Name</span>
      <input
        className="di-input"
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
      />
    </label>
  )
}

// Which approach a 3-leg intersection lacks. Required: the grid, balancing and
// diagrams all treat a leg as present until it is marked missing.
function MissingLegSelect({ value, onChange, className }) {
  return (
    <label className={className}>
      <span>Missing approach</span>
      <select className="di-select" value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={!value || undefined}>
        <option value="" disabled>Pick the missing leg…</option>
        {ACTIVE_DIRS.map((d) => <option key={d} value={d}>{d} (no {APPROACH_FROM[d]} leg)</option>)}
      </select>
    </label>
  )
}

function Segmented({ label, value, options, onSelect }) {
  return (
    <div className="di-inspector__row di-inspector__row--stack">
      <span>{label}</span>
      <div className="di-seg">
        {options.map((opt) => (
          <button
            key={opt}
            type="button"
            aria-pressed={value === opt}
            className={`di-seg__opt${value === opt ? ' di-seg__opt--on' : ''}`}
            onClick={() => onSelect(opt)}
          >{opt}</button>
        ))}
      </div>
    </div>
  )
}
