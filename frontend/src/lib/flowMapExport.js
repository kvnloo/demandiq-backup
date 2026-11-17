// Stage 6 — PNG export for the balance flow map.
//
// Renders the live Leaflet map to a 2×-DPI PNG, with:
//   - the aitt-map basemap as drawn (always the light 'day' style, so already
//     report-ready; its WebGL canvas keeps preserveDrawingBuffer for this —
//     see lib/themeTiles.js)
//   - a title overlay (top-left) and legend overlay (bottom-right)

import html2canvas from 'html2canvas'
import { IMBALANCE_COLORS } from './flowMap'

// Inject a temporary title + legend overlay into the map container. The
// overlay is removed after capture so it doesn't persist in the live UI.
function injectOverlays({ container, title, viewType, method, dateStr }) {
  const title_el = document.createElement('div')
  title_el.className = 'di-flow-export__title'
  title_el.style.cssText = [
    'position:absolute', 'top:14px', 'left:16px', 'z-index:9000',
    'background:rgba(255,255,255,0.92)', 'color:#1A1F2C',
    'padding:8px 14px', 'border-radius:6px',
    'font-family:ui-sans-serif,system-ui,sans-serif',
    'font-size:14px', 'font-weight:600',
    'box-shadow:0 2px 8px rgba(0,0,0,0.15)',
  ].join(';')
  title_el.textContent = `${title} — ${viewType} balance flow`
  container.appendChild(title_el)

  const legend = document.createElement('div')
  legend.className = 'di-flow-export__legend'
  legend.style.cssText = [
    'position:absolute', 'bottom:14px', 'right:14px', 'z-index:9000',
    'background:rgba(255,255,255,0.94)', 'color:#1A1F2C',
    'padding:10px 14px', 'border-radius:6px',
    'font-family:ui-sans-serif,system-ui,sans-serif',
    'font-size:11px', 'line-height:1.55',
    'box-shadow:0 2px 8px rgba(0,0,0,0.18)',
    'min-width:200px',
  ].join(';')
  const swatch = (hex, label) =>
    `<div style="display:flex;align-items:center;gap:6px;">
       <span style="display:inline-block;width:14px;height:4px;background:${hex};border-radius:2px;"></span>
       <span>${label}</span>
     </div>`
  legend.innerHTML = `
    <div style="font-weight:600;font-size:12px;margin-bottom:6px;">Legend</div>
    ${swatch(IMBALANCE_COLORS.green, 'Imbalance ≤ 5%')}
    ${swatch(IMBALANCE_COLORS.amber, '5–15%')}
    ${swatch(IMBALANCE_COLORS.red,   '&gt; 15%')}
    <div style="margin-top:6px;color:#4A5568;">Format: AM (PM)</div>
    ${method ? `<div style="color:#4A5568;">Method: ${method}</div>` : ''}
    <div style="color:#4A5568;">${dateStr}</div>
  `
  container.appendChild(legend)
  return () => {
    title_el.remove()
    legend.remove()
  }
}

// Public: capture at 2×, trigger download.
//
// Args:
//   container: the .leaflet-container DOM element
//   meta:      { projectName, viewType ('Pre' | 'Post'), method, date }
//
// Caller is responsible for re-entering nothing during capture.
export async function exportFlowMapPNG({ container, meta }) {
  if (!container) {
    throw new Error('exportFlowMapPNG: container is required')
  }
  // 1. Inject overlays
  const dateStr = new Date(meta.date || Date.now()).toLocaleString()
  const removeOverlays = injectOverlays({
    container,
    title: meta.projectName || 'Project',
    viewType: meta.viewType || 'Post',
    method: meta.method,
    dateStr,
  })

  try {
    // 2. Capture at 2× DPI
    const canvas = await html2canvas(container, {
      useCORS: true,
      allowTaint: false,
      backgroundColor: '#FFFFFF',
      scale: 2,
      logging: false,
      // html2canvas 1.4 throws on the color-mix() attribution background
      // (shell.css), which killed every export; keep the OSM credit, plain white.
      // It also ignores the Leaflet SVG renderer's translate3d and viewBox
      // origin, so the flow bands land off the streets: bake the offset into
      // the clone's <g> instead.
      onclone: (doc) => {
        doc.querySelectorAll('.leaflet-control-attribution')
          .forEach((el) => el.style.setProperty('background', 'rgba(255,255,255,0.8)', 'important'))
        doc.querySelectorAll('.leaflet-overlay-pane svg[viewBox]').forEach((svg) => {
          const [x, y, w, h] = svg.getAttribute('viewBox').split(' ').map(Number)
          svg.setAttribute('viewBox', `0 0 ${w} ${h}`)
          svg.style.transform = 'none'
          svg.querySelectorAll(':scope > g').forEach((g) => g.setAttribute('transform', `translate(${-x} ${-y})`))
        })
      },
    })
    // 3. Trigger download
    const safe = (s) => String(s || '').replace(/[^a-z0-9]+/gi, '_').replace(/^_+|_+$/g, '')
    const stamp = new Date().toISOString().slice(0, 10)
    const filename = `${safe(meta.projectName) || 'project'}_${safe(meta.viewType) || 'flow'}_flow_map_${stamp}.png`
    await new Promise((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (!blob) return reject(new Error('canvas.toBlob returned null'))
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = filename
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)
        resolve()
      }, 'image/png')
    })
  } finally {
    // 4. Always remove the overlays.
    removeOverlays()
  }
}
