import { useEffect, useRef, useState } from 'react'
import { friendlyError } from '../lib/apiError'

// Talk to fill Project Setup. Records in the browser, the server turns speech into the form
// fields (backend/voice.py), and the engineer checks the filled form before continuing.
const LABELS = {
  name: 'Project Name', client: 'Client / Agency', projectNumber: 'Project Number',
  analyst: 'Analyst Initials', checker: 'Checker Initials', date: 'Date',
  scenario: 'Scenario Name', projectType: 'Project Type', analysisPeriods: 'Analysis Period',
}
const canRecord = () => typeof window !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder)

export default function VoiceSetup({ onFields }) {
  const [state, setState] = useState('idle') // idle | recording | working
  const [result, setResult] = useState(null) // { transcript, filled: [labels] }
  const [error, setError] = useState(null)
  const [typing, setTyping] = useState(() => !canRecord())
  const [text, setText] = useState('')
  const rec = useRef(null)

  // Release the mic if the engineer leaves the page mid-recording.
  useEffect(() => () => rec.current?.stream.getTracks().forEach((t) => t.stop()), [])

  const send = async (body) => {
    setState('working')
    setError(null)
    try {
      const res = await fetch('/api/voice-setup', { method: 'POST', body })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`)
      const fields = data.fields || {}
      onFields(fields)
      setResult({ transcript: data.transcript, filled: Object.keys(fields).map((k) => LABELS[k]).filter(Boolean) })
    } catch (err) {
      setError(friendlyError(err))
    } finally {
      setState('idle')
    }
  }

  const start = async () => {
    setError(null)
    let stream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setError('The browser blocked the microphone. Allow it, or type it instead.')
      setTyping(true)
      return
    }
    const chunks = []
    const r = new MediaRecorder(stream)
    r.ondataavailable = (e) => e.data.size && chunks.push(e.data)
    r.onstop = () => {
      stream.getTracks().forEach((t) => t.stop())
      const blob = new Blob(chunks, { type: r.mimeType || 'audio/webm' })
      const fd = new FormData()
      fd.append('audio', blob, /mp4/.test(blob.type) ? 'talk.mp4' : 'talk.webm') // Safari records mp4
      send(fd)
    }
    rec.current = r
    r.start()
    setState('recording')
  }

  const stop = () => rec.current?.state === 'recording' && rec.current.stop()

  const sendText = (e) => {
    e.preventDefault()
    if (!text.trim()) return
    const fd = new FormData()
    fd.append('text', text)
    send(fd)
  }

  return (
    <div className="di-voice">
      <div className="di-voice__row">
        {!typing && (
          <button
            type="button"
            className={state === 'recording' ? 'di-btn-primary di-voice__btn is-on' : 'di-btn-secondary di-voice__btn'}
            onClick={state === 'recording' ? stop : start}
            disabled={state === 'working'}
            aria-pressed={state === 'recording'}
          >
            {state === 'recording' ? 'Done talking' : state === 'working' ? 'Filling the form…' : 'Talk it through'}
          </button>
        )}
        <span className="di-help">
          {state === 'recording'
            ? 'Listening. Say the project, client, scenario, type and peak hours.'
            : 'Describe the project out loud; we fill the form, you check it. Audio is not stored.'}
        </span>
        {!typing && state === 'idle' && (
          <button type="button" className="di-voice__link" onClick={() => setTyping(true)}>Type instead</button>
        )}
      </div>

      {typing && (
        <div className="di-voice__type">
          <textarea
            className="di-input"
            rows={2}
            value={text}
            aria-label="Describe the project"
            placeholder="e.g. Main Street corridor study for the City of Fresno, existing conditions, AM and PM peaks, analyst AK"
            onChange={(e) => setText(e.target.value)}
          />
          <button type="button" className="di-btn-secondary" onClick={sendText} disabled={state === 'working' || !text.trim()}>
            {state === 'working' ? 'Filling…' : 'Fill the form'}
          </button>
        </div>
      )}

      {error && <div className="di-field-error" role="alert">{error}</div>}
      {result && !error && (
        <div className="di-voice__result" role="status">
          <q>{result.transcript}</q>
          <span>
            {result.filled.length
              ? `Filled: ${result.filled.join(', ')}. Check them below.`
              : 'Nothing to fill from that. Try naming the project, client or peak hours.'}
          </span>
        </div>
      )}
    </div>
  )
}
