import { useState } from 'react'
import { clearProject, exportProjectFile, getSnapshot } from '../store/projectStore'

const slug = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'project'

// "Start a new project" confirm (TopBar + S8). The browser holds one project,
// so starting over deletes it: offer the project file download first.
export default function NewProjectDialog({ onCancel }) {
  const [saved, setSaved] = useState(false)
  const name = getSnapshot().project?.name?.trim()
  const download = () => { exportProjectFile(slug(name)); setSaved(true) }
  const startFresh = () => { clearProject(); window.location.assign('/') }
  return (
    <div className="di-modal-backdrop">
      <div className="di-modal" role="dialog" aria-modal="true" aria-labelledby="di-newproj-title">
        <div className="di-modal__header">
          <h2 id="di-newproj-title" className="di-modal__title">Start a new project?</h2>
        </div>
        <div className="di-modal__body">
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.55 }}>
            This replaces {name ? <strong>{name}</strong> : 'the current project'} — it is deleted from
            this browser and cannot be undone. Download the project file first if you may need it again;
            you can reopen it from Project Setup.
          </p>
          {saved && <p className="di-help" role="status" style={{ marginTop: 8 }}>Project file downloaded.</p>}
        </div>
        <div className="di-modal__footer">
          <button type="button" className="di-btn-secondary" onClick={onCancel}>Cancel</button>
          <button type="button" className="di-btn-secondary" onClick={download}>Download project file</button>
          <button type="button" className="di-btn-primary" onClick={startFresh}>Delete &amp; start new</button>
        </div>
      </div>
    </div>
  )
}
