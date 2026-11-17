// The one basemap every DemandIQ map uses: aitt-map (our California map, shared
// with CrashIQ, StreetCanvas and TrafficOpsIQ), drawn by MapLibre inside Leaflet
// via @maplibre/maplibre-gl-leaflet so every existing Leaflet overlay and click
// handler stays as it was. Owner's call (2026-11-16): the light 'day' style in
// both app themes, no satellite.
//
// Lane counts come from the aitt-map road-details layer; one-way arrows come
// with the basemap style.

import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import * as maplibregl from 'maplibre-gl'
import { maplibreGL } from '@maplibre/maplibre-gl-leaflet'
import { Protocol } from 'pmtiles'
import 'maplibre-gl/dist/maplibre-gl.css'
// shortcut: @aitt/map v0.1.0's "exports" only lists ".", so the README's '@aitt/map/src/theme/map-tokens.css'
// import fails to resolve; use the file path until aitt-map exports the CSS.
import '../../node_modules/@aitt/map/src/theme/map-tokens.css'
import { buildStyle, readTokens, trafficLayer, ATTRIBUTION } from '@aitt/map'

const TILES = 'https://tiles.aitransportationtools.com'
export const BASEMAP_URL = `${TILES}/basemap/v20261114.pmtiles`
export const ROAD_DETAILS_URL = `${TILES}/layers/road-details-v20261116.pmtiles`
const GLYPHS = `${TILES}/fonts/{fontstack}/{range}.pbf`
// shortcut: aitt-map's light sprite (POI icons, one-way arrows) is copied into public/sprites/v4
// because the tiles host doesn't serve sprites yet; point this at the tiles host once it does.
const SPRITE = `${globalThis.location?.origin ?? ''}/sprites/v4/light`

maplibregl.addProtocol('pmtiles', new Protocol().tile)

export function basemapStyle(tokens = readTokens()) {
  const style = buildStyle(tokens, 'day', { basemapUrl: 'pmtiles://' + BASEMAP_URL, glyphs: GLYPHS, sprite: SPRITE, attribution: ATTRIBUTION })
  const details = trafficLayer(tokens, 'day', 'pmtiles://' + ROAD_DETAILS_URL)
  style.sources[details.id] = details.source
  // Lane counts only: the layer's stop-sign/crossing/bus icons sit on every
  // block and bury the volume pins and bands.
  style.layers.push(...details.layers.filter((l) => l.id === 'traffic-lanes-label'))
  return style
}

// Drop-in replacement for the old <TileLayer>: put it inside any <MapContainer>.
// preserveDrawingBuffer keeps the WebGL frame readable so the flow-map PNG
// export (html2canvas) captures the basemap instead of a blank canvas. The
// OSM/Protomaps credit goes in Leaflet's attribution control (the one users
// already see): the bridge reads it from the style's sources.
export function AittBasemap() {
  const map = useMap()
  useEffect(() => {
    const layer = maplibreGL({
      style: basemapStyle(),
      canvasContextAttributes: { preserveDrawingBuffer: true },
    }).addTo(map)
    return () => { layer.remove() }
  }, [map])
  return null
}
