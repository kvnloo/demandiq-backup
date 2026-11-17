// Basemap contract tests — pure ESM, no runner:
//   node frontend/src/lib/themeTiles.test.mjs
//
// themeTiles.js pulls in maplibre-gl, React and CSS, which node can't load, so
// this reads the source as text. Locks: the light 'day' aitt-map style, tiles
// from the aitt-map host, and no Esri/ArcGIS basemap left anywhere in src.

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

let passed = 0, failed = 0
function ok(cond, msg) {
  if (cond) { passed++ } else { failed++; console.error('  FAIL:', msg) }
}

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'themeTiles.js'), 'utf8')

ok(/buildStyle\([^)]*'day'/.test(src), "basemap uses the 'day' style")
ok(!/'night'|"night"/.test(src), 'no night style')
ok(src.includes("'https://tiles.aitransportationtools.com'"), 'tiles come from the aitt-map host')
ok(/BASEMAP_URL = `\$\{TILES\}\/basemap\/[^`]+\.pmtiles`/.test(src), 'basemap is a pmtiles file on the tiles host')
ok(src.includes('preserveDrawingBuffer: true'), 'WebGL buffer kept for the flow-map PNG export')

function walk(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? walk(p) : [p]
  })
}
const self = fileURLToPath(import.meta.url)
for (const f of walk(join(here, '..'))) {
  if (f === self || !/\.(jsx?|mjs|css)$/.test(f)) continue
  ok(!/esri|arcgis/i.test(readFileSync(f, 'utf8')), `no Esri/ArcGIS in ${f}`)
}

console.log(`themeTiles: ${passed} passed, ${failed} failed`)
if (failed) process.exit(1)
