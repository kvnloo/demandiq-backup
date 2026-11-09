// S8 card: the forecast workbook (Forecast Summary + Forecast TMCs + Corridor VMT).
// Lives beside F4 so S8 only mounts it; numbers come verbatim from the run F4 shows.
import { useState } from 'react'
import { FileSpreadsheet, Loader2 } from 'lucide-react'

import { friendlyError, isRunStale } from '../lib/forecastRun'

export default function ForecastExportCard({ snap, filename }) {
  const [status, setStatus] = useState('idle')   // idle | loading | error
  const [error, setError] = useState(null)
  const fc = snap.forecasting ?? {}
  const stale = isRunStale(fc)
  const run = fc.run ?? {}

  const download = async () => {
    setStatus('loading')
    setError(null)
    try {
      const res = await fetch('/api/forecast/export-excel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(snap),
      })
      if (!res.ok) {
        let detail = `HTTP ${res.status}`
        try { const j = await res.json(); if (j?.detail) detail = j.detail } catch { /* non-JSON */ }
        throw new Error(detail)
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${filename}-forecast.xlsx`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      setStatus('idle')
    } catch (err) {
      setError(friendlyError(err))
      setStatus('error')
    }
  }

  return (
    <div className="di-s8-card">
      <div className="di-s8-card__head">
        <div className="di-s8-card__icon"><FileSpreadsheet size={20} /></div>
        <div className="di-s8-card__title-wrap">
          <div className="di-s8-card__title">Forecast Workbook</div>
        </div>
      </div>
      <p className="di-s8-card__desc">
        Future turning movements per intersection beside existing
        {run.baseYear && run.horizonYear ? ` (${run.baseYear} → ${run.horizonYear})` : ''}, the methods,
        K, model runs used, every note and decline, and corridor VMT.
      </p>
      <button
        type="button"
        className="di-btn-primary di-s8-card__btn"
        disabled={stale || status === 'loading'}
        onClick={download}
        title={stale ? 'Inputs changed since the last forecast run — re-run it first' : undefined}
      >
        {status === 'loading'
          ? <><Loader2 size={14} className="di-spin" style={{ verticalAlign: '-2px' }} />{' '}Generating…</>
          : <>Download forecast .xlsx</>}
      </button>
      {stale && (
        <div className="di-s8-error">
          Inputs changed since the last forecast run — run the forecast again (Forecasting step 3) before exporting.
        </div>
      )}
      {status === 'error' && error && <div className="di-s8-error">{error}</div>}
    </div>
  )
}
