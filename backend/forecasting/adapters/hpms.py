"""
DemandIQ Forecasting — statewide California adapter (FHWA HPMS).

Covers every California county with no model files: reads the public FHWA
Highway Performance Monitoring System (HPMS) 2024 release for California
(geo.dot.gov, ~958k road sections) around the project intersections and emits a
canonical NetworkLayer, so the region-blind engine (matcher → refinement → Pass
1/2 → VMT) runs unchanged.

What HPMS carries, and how it maps onto the contract
  - `aadt` (2024 count/estimate) on all major public roads; `future_aadt` (a ~20
    year forecast, year in `future_aadt_vd`) on state highways and a sample of
    arterials. A section without its own forecast BORROWS the median annual growth
    of forecast sections of the same road class within GROWTH_RADIUS_MI (or of any
    class if too few). Every link records which source it used.
  - Scenario-year volume is linear between 2024 and the forecast year:
        AADT(y) = aadt × (1 + rate × (y − 2024)),  rate = (future/aadt − 1)/(fy − 2024)
  - Flow basis is DAILY, not period volumes. Each direction's AM and PM "period"
    flow is AADT(y) × K / 2 (K = the section's k_factor, else DEFAULT_K). The
    operative RATIO refinement is invariant to K and to the 50/50 split (it only
    uses future/base on the same link), and VMT's daily/period ratio becomes 1/K,
    i.e. a peak-hour → daily expansion. shortcut: AM and PM grow at the same rate
    and the directional split is assumed even; upgrade when an MPO model with
    directional period flows covers the project (use that region instead).
  - HPMS sections are linear-referenced pieces, not node-to-node model links. The
    adapter builds the topology the matcher expects: each project intersection is
    a node, and every road passing within SNAP_FT of it becomes one half-link per
    side (filter_radius_ft long). base and horizon layers come from the same
    sections, so link_ids are identical across years.

Geo deps (shapely/pyproj) are imported inside ingest_hpms, as in the SANDAG
adapter, so importing this module costs the lean app nothing.
"""

import hashlib
import json
import math
import statistics
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from forecasting.contract import DirectionalFlow, NetworkLayer, NetworkLink, Period
from forecasting.geo import polyline_length_mi

HPMS_URL = ("https://geo.dot.gov/server/rest/services/Hosted/"
            "HPMS_FULL_CA_2024/FeatureServer/0/query")
HPMS_DATA_YEAR = 2024
FIELDS = ("route_id,begin_point,end_point,routename,f_system,county_id,"
          "through_lanes,aadt,future_aadt,future_aadt_vd,k_factor")
REGION = "HPMS_CA"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "hpms"

SNAP_FT = 75.0            # a road counts as "through this intersection" within this
MIN_SIDE_FT = 50.0        # shorter than this past the node = the road ends here (T leg)
GROWTH_RADIUS_MI = 2.0    # where borrowed growth is looked up
MIN_GROWTH_SAMPLES = 3
DEFAULT_K = 0.09          # peak-hour share of AADT when the section has no k_factor
FT_PER_M = 3.28084
PAGE = 2000               # FeatureServer maxRecordCount

F_SYSTEM = {1: "Freeway", 2: "Freeway", 3: "Principal Arterial", 4: "Minor Arterial",
            5: "Collector", 6: "Collector", 7: "Local"}


# ── Data access: live FeatureServer with an on-disk cache ─────────────────────
def _query(where, bbox):
    """All GeoJSON features matching `where` inside bbox (lng0, lat0, lng1, lat1).
    Cached under data/hpms/cache so a re-pull (and the base + horizon calls) hit
    the network once."""
    params = {
        "where": where, "geometry": ",".join(f"{v:.6f}" for v in bbox),
        "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": FIELDS,
        "outSR": 4326, "f": "geojson", "orderByFields": "objectid",
    }
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cache = CACHE_DIR / "cache" / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    feats, offset = [], 0
    while True:
        url = HPMS_URL + "?" + urllib.parse.urlencode({**params, "resultOffset": offset,
                                                       "resultRecordCount": PAGE})
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                page = json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise ValueError(f"ingest_hpms: could not reach the FHWA HPMS service ({e}). "
                             "Check the internet connection and retry.") from e
        if "error" in page:
            raise ValueError(f"ingest_hpms: HPMS service error: {page['error']}")
        got = page.get("features", [])
        feats.extend(got)
        if len(got) < PAGE:
            break
        offset += PAGE
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(feats))
    return feats


def _load(source, where_future, bbox):
    """Features from a local GeoJSON file (offline/tests) or the live service."""
    if source and source != "live":
        data = json.loads(Path(source).read_text())
        feats = data["features"] if isinstance(data, dict) else data
        from shapely.geometry import box, shape
        area = box(*bbox)                    # intersects, as the live service does
        feats = [f for f in feats if shape(f["geometry"]).intersects(area)]
        if where_future:
            feats = [f for f in feats if (f["properties"].get("future_aadt") or 0) > 0]
        return feats
    return _query("future_aadt>0 AND aadt>0" if where_future else "aadt>0", bbox)


# ── Small helpers ─────────────────────────────────────────────────────────────
def _flat_coords(geom):
    if geom["type"] == "LineString":
        return [tuple(c[:2]) for c in geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return [tuple(c[:2]) for part in geom["coordinates"] for c in part]
    return []


def _year(v):
    """future_aadt_vd arrives as '2044-01-01', an epoch-ms number, or None."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        import datetime
        return datetime.datetime.utcfromtimestamp(v / 1000).year if v > 10_000 else int(v)
    try:
        return int(str(v)[:4])
    except ValueError:
        return None


def _rate(props):
    """Linear annual growth from the section's own forecast, or None."""
    aadt, fut, fy = props.get("aadt"), props.get("future_aadt"), _year(props.get("future_aadt_vd"))
    if not aadt or not fut or not fy or fy <= HPMS_DATA_YEAR:
        return None
    return (fut / aadt - 1.0) / (fy - HPMS_DATA_YEAR)


def _street_name(route_id, routename):
    """'ALA_FMT_DECOTO RD_P' → 'Decoto Rd'; 'SHS_013._P' → 'SR 13'."""
    if routename:
        return str(routename).title()
    parts = (route_id or "").split("_")
    if parts and parts[0] == "SHS" and len(parts) > 1:
        return "SR " + parts[1].rstrip(".").lstrip("0")
    return parts[2].title() if len(parts) >= 4 else (route_id or None)


def _node_id(i):
    return 900_000 + i


def _far_node_id(link_id):
    return int(hashlib.sha1(link_id.encode()).hexdigest()[:8], 16)


# ── Adapter ───────────────────────────────────────────────────────────────────
def ingest_hpms(source, scenario_year, project_intersections_4326, *, filter_radius_ft=300):
    """RegionAdapter for California statewide (HPMS). `source` is "live" (the
    FHWA service, cached) or a path to a GeoJSON FeatureCollection with the same
    fields. Returns NetworkLayer(region='HPMS_CA', crs 4326)."""
    from pyproj import Transformer
    from shapely.geometry import LineString, MultiLineString, Point
    from shapely.ops import linemerge, substring

    pts = [(float(p["lng"]), float(p["lat"]), p.get("num", i + 1))
           for i, p in enumerate(project_intersections_4326)
           if p.get("lat") is not None and p.get("lng") is not None]
    if not pts:
        raise ValueError("ingest_hpms: no project intersections with lat/lng provided — "
                         "cannot scope the network.")

    to_m = Transformer.from_crs(4326, 3310, always_xy=True)       # CA Albers, metres
    to_ll = Transformer.from_crs(3310, 4326, always_xy=True)
    lngs, lats = [p[0] for p in pts], [p[1] for p in pts]
    pad = (filter_radius_ft + 300) / 364_000                         # ≈ ft → degrees
    gpad = GROWTH_RADIUS_MI / 69.0                                    # degrees of latitude
    gpad_x = GROWTH_RADIUS_MI / (69.172 * math.cos(math.radians(max(abs(l) for l in lats))))  # longitude degrees shrink with cos(lat)
    near = _load(source, False, (min(lngs) - pad, min(lats) - pad, max(lngs) + pad, max(lats) + pad))
    wide = _load(source, True, (min(lngs) - gpad_x, min(lats) - gpad, max(lngs) + gpad_x, max(lats) + gpad))

    def to_line(geom):
        parts = [[to_m.transform(x, y) for x, y in seg]
                 for seg in ([geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"])]
        parts = [p for p in parts if len(p) >= 2]
        return MultiLineString(parts) if len(parts) > 1 else (LineString(parts[0]) if parts else None)

    sections = []                                # (route_id, line_m, props)
    for f in near:
        g = to_line(f["geometry"])
        if g is not None and not g.is_empty and (f["properties"].get("aadt") or 0) > 0:
            sections.append((f["properties"].get("route_id"), g, f["properties"]))
    growth_pool = []                             # (point_m, f_system, rate)
    for f in wide:
        r = _rate(f["properties"])
        g = to_line(f["geometry"])
        if r is not None and g is not None and not g.is_empty:
            growth_pool.append((g.interpolate(0.5, normalized=True), f["properties"].get("f_system"), r))

    def borrowed(p_m, fsys):
        radius_m = GROWTH_RADIUS_MI * 1609.344
        local = [(fs, r) for q, fs, r in growth_pool if q.distance(p_m) <= radius_m]
        same = [r for fs, r in local if fs == fsys]
        if len(same) >= MIN_GROWTH_SAMPLES:
            return statistics.median(same), f"median of {len(same)} nearby forecast sections, same road class"
        if len(local) >= MIN_GROWTH_SAMPLES:
            return statistics.median([r for _, r in local]), f"median of {len(local)} nearby forecast sections, all road classes"
        return 0.0, "no HPMS forecast within 2 miles — held at 2024 volume"

    by_route = {}
    for rid, g, props in sections:
        by_route.setdefault(rid, []).append((g, props))

    snap_m, side_m, min_m = SNAP_FT / FT_PER_M, filter_radius_ft / FT_PER_M, MIN_SIDE_FT / FT_PER_M
    links = []
    for idx, (lng, lat, num) in enumerate(pts):
        P = Point(to_m.transform(lng, lat))
        node = _node_id(idx)
        for rid, pieces in sorted(by_route.items(), key=lambda kv: str(kv[0])):
            if min(g.distance(P) for g, _ in pieces) > snap_m:
                continue
            merged = linemerge([seg for g, _ in pieces
                                for seg in (g.geoms if isinstance(g, MultiLineString) else [g])])
            line = (min(merged.geoms, key=lambda s: s.distance(P))
                    if isinstance(merged, MultiLineString) else merged)
            if line.distance(P) > snap_m:
                continue
            d, L = line.project(P), line.length
            for side, (a, b) in (("a", (max(0.0, d - side_m), d)), ("b", (d, min(L, d + side_m)))):
                if b - a < min_m:
                    continue                                      # road ends here
                seg = substring(line, a, b)
                cs = list(seg.coords)
                if side == "a":
                    cs.reverse()                                  # start at the intersection
                cs[0] = (P.x, P.y)
                probe = LineString(cs).interpolate(min(20.0, (b - a) / 2))
                g0, props = min(pieces, key=lambda gp: gp[0].distance(probe))
                rate = _rate(props)
                if rate is not None:
                    gsrc = f"this road's HPMS forecast ({props.get('future_aadt'):,} in {_year(props.get('future_aadt_vd'))})"
                else:
                    rate, gsrc = borrowed(P, props.get("f_system"))
                aadt = float(props["aadt"])
                vol = max(0.0, aadt * (1.0 + rate * (int(scenario_year) - HPMS_DATA_YEAR)))
                k = (props.get("k_factor") or 0) / 100 or DEFAULT_K
                per_dir = vol * k / 2.0
                coords = [tuple(round(v, 7) for v in to_ll.transform(x, y)) for x, y in cs]
                link_id = f"{rid}@{num}{side}"
                links.append(NetworkLink(
                    link_id=link_id,
                    from_node=node,
                    to_node=_far_node_id(link_id),
                    geometry=coords,
                    from_node_xy=coords[0],
                    to_node_xy=coords[-1],
                    length_mi=polyline_length_mi(coords),
                    name=_street_name(rid, props.get("routename")),
                    functional_class=F_SYSTEM.get(props.get("f_system"), "Other"),
                    lanes=props.get("through_lanes"),
                    flow=DirectionalFlow(
                        ab={Period.AM: per_dir, Period.PM: per_dir},
                        ba={Period.AM: per_dir, Period.PM: per_dir},
                    ),
                    daily_total=vol,
                    source_attributes={
                        "route_id": rid, "county_fips": props.get("county_id"),
                        "f_system": props.get("f_system"),
                        "aadt_2024": props.get("aadt"),
                        "future_aadt": props.get("future_aadt"),
                        "future_year": _year(props.get("future_aadt_vd")),
                        "annual_growth_pct": round(rate * 100, 3),
                        "growth_source": gsrc,
                        "k_used": round(k, 4),
                        "k_source": "HPMS k_factor" if props.get("k_factor") else f"default {DEFAULT_K:.0%}",
                    },
                ))

    if not links:
        raise ValueError(
            f"ingest_hpms: no HPMS road with a traffic volume passes within {SNAP_FT:.0f} ft "
            f"of any of the {len(pts)} project intersection(s). HPMS covers state highways "
            "and major roads; local streets may be missing.")

    return NetworkLayer(region=REGION, source_crs_epsg=4326, crs_epsg=4326,
                        scenario_year=int(scenario_year), links=links)
