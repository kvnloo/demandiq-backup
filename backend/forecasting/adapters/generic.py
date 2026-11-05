"""
DemandIQ Forecasting — generic adapter for an engineer's own model file.

Turns an uploaded loaded network (any MPO's shapefile / GeoPackage / GeoJSON)
plus the engineer-confirmed column mapping (forecasting.upload.ROLES) into the
same NetworkLayer the SANDAG/MTC adapters emit. Reads only a padded bbox around
the project, like adapters/sandag.py.

Source shapes handled:
  - Two-way records (A→B and B→A volume columns on one row, SANDAG-style).
    Geometry is assumed digitized from-node → to-node, as in SANDAG.
  - One-way records (one volume column; MTC-style). Reverse records are paired
    into one link (from = lower node), AB = low→high, BA = the reverse.
  - No node columns: node ids come from the rounded endpoint coordinates, so
    links meeting at a point share a node and ids are stable across model years.
"""

import hashlib
import math

from forecasting.adapters.sandag import _coords_list, _float0, _py
from forecasting.contract import DirectionalFlow, NetworkLayer, NetworkLink, Period
from forecasting.geo import polyline_length_mi

REGION = "UPLOAD"
PROJECT_EPSG = 4326


def _key(v):
    """Comparable text for a class value: 10, 10.0 and '10' all → '10'."""
    v = _py(v)
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(v).strip()


def _node_from_xy(xy):
    # ~0.1 m in WGS84; a deterministic id so base and horizon files agree.
    h = hashlib.sha1(f"{xy[0]:.6f},{xy[1]:.6f}".encode()).hexdigest()
    return int(h[:13], 16)


def _int(v):
    pv = _py(v)
    return None if pv is None else int(float(pv))


def ingest_upload(path, scenario_year, project_intersections_4326, *, mapping, filter_radius_ft=300):
    """Uploaded model file + checked mapping → NetworkLayer (EPSG:4326)."""
    import geopandas as gpd
    import pyogrio
    from pyproj import CRS, Transformer

    pts = [(float(p["lng"]), float(p["lat"])) for p in project_intersections_4326
           if p.get("lat") is not None and p.get("lng") is not None]
    if not pts:
        raise ValueError("no project intersections with lat/lng — cannot scope the network.")

    src_crs = CRS(pyogrio.read_info(path).get("crs") or "EPSG:4326")
    to_src = Transformer.from_crs(PROJECT_EPSG, src_crs, always_xy=True)
    # Distances in a local metric frame around the project (any source CRS).
    lng0, lat0 = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={lat0} +lon_0={lng0} +units=ft")
    to_local = Transformer.from_crs(src_crs, local, always_xy=True)
    to_out = Transformer.from_crs(src_crs, PROJECT_EPSG, always_xy=True)

    # Padded bbox in the source CRS: corners of the project box ± the radius.
    m_deg = (filter_radius_ft * 1.2 + 100) / 364000     # ft → degrees of latitude
    lo_x, lo_y = min(p[0] for p in pts) - m_deg / math.cos(math.radians(lat0)), min(p[1] for p in pts) - m_deg
    hi_x, hi_y = max(p[0] for p in pts) + m_deg / math.cos(math.radians(lat0)), max(p[1] for p in pts) + m_deg
    bbox = to_src.transform_bounds(lo_x, lo_y, hi_x, hi_y)
    cols = sorted({v for k, v in mapping.items() if k != "connector_values"})
    gdf = gpd.read_file(path, bbox=bbox, columns=cols)

    proj_local = [Transformer.from_crs(PROJECT_EPSG, local, always_xy=True).transform(*p) for p in pts]
    r2 = filter_radius_ft ** 2

    def near(xy):
        x, y = to_local.transform(*xy)
        return any((x - px) ** 2 + (y - py) ** 2 <= r2 for px, py in proj_local)

    drop = {_key(v) for v in mapping.get("connector_values") or []}
    two_way = "am_ba" in mapping
    periods = {Period.AM: ("am_ab", "am_ba"), Period.PM: ("pm_ab", "pm_ba")}
    periods = {p: c for p, c in periods.items() if c[0] in mapping}
    g = lambda row, role: row[mapping[role]] if role in mapping else None   # noqa: E731

    recs = []   # (from, to, coords_4326, row)
    for _, row in gdf.iterrows():
        if row.geometry is None or (drop and _key(row[mapping["connector_field"]]) in drop):
            continue
        cs = _coords_list(row.geometry)
        if not (near(cs[0]) or near(cs[-1])):
            continue
        out = [tuple(float(c) for c in to_out.transform(x, y)) for x, y, *_ in cs]
        try:
            a, b = (_int(g(row, "from_node")), _int(g(row, "to_node"))) if "from_node" in mapping else (None, None)
        except (TypeError, ValueError):
            raise ValueError("the node columns must hold node numbers — pick other columns, or leave "
                             "them blank to join links by their end points.")
        if a is None or b is None:
            a, b = _node_from_xy(out[0]), _node_from_xy(out[-1])
        recs.append((a, b, out, row))
    if not recs:
        raise ValueError(f"no road in your model file within {filter_radius_ft} ft of the project "
                         "intersections — does the file cover the project area?")

    def link(lid, a, b, coords, ab_row, ba_row, ab_cols, ba_cols):
        either = ab_row if ab_row is not None else ba_row
        lanes = [_int(g(r, k)) or 0 for r, k in ((ab_row, "lanes_ab"), (ba_row, "lanes_ba"),
                                                 (ba_row, "lanes_ab")) if r is not None and k in mapping]
        rows = [ab_row] if ab_row is ba_row else [r for r in (ab_row, ba_row) if r is not None]
        name = _py(g(either, "name"))
        fc = _py(g(either, "fclass"))
        return NetworkLink(
            link_id=lid, from_node=a, to_node=b, geometry=coords,
            from_node_xy=coords[0], to_node_xy=coords[-1],
            length_mi=(_float0(g(either, "length_mi")) or polyline_length_mi(coords)),
            name=str(name) if name is not None else None,
            functional_class=str(fc) if fc is not None else None,
            lanes=max(lanes) if lanes else None,
            flow=DirectionalFlow(
                ab={p: _float0(ab_row[mapping[ab_cols(c)]]) if ab_row is not None else 0.0 for p, c in periods.items()},
                ba={p: _float0(ba_row[mapping[ba_cols(c)]]) if ba_row is not None else 0.0 for p, c in periods.items()},
            ),
            daily_total=sum(_float0(g(r, "daily")) for r in rows) if "daily" in mapping else None,
            source_attributes={mapping[k]: _py(g(either, k)) for k in sorted(mapping)
                               if k not in ("connector_values",) and mapping[k] in either.index},
        )

    links, seen = [], {}
    if two_way:
        for a, b, coords, row in recs:
            lid = _key(g(row, "link_id")) if "link_id" in mapping else f"{a}-{b}"
            seen[lid] = seen.get(lid, 0) + 1
            if seen[lid] > 1:          # parallel links between the same nodes
                lid = f"{lid}#{seen[lid]}"
            links.append(link(lid, a, b, coords, row, row, lambda c: c[0], lambda c: c[1]))
    else:
        by_dir = {(a, b): (coords, row) for a, b, coords, row in recs}
        for lo, hi in sorted({tuple(sorted(k)) for k in by_dir}):
            ab, ba = by_dir.get((lo, hi)), by_dir.get((hi, lo))
            coords = ab[0] if ab else ba[0][::-1]    # digitize lo → hi
            links.append(link(f"{lo}-{hi}", lo, hi, coords, ab and ab[1], ba and ba[1],
                              lambda c: c[0], lambda c: c[0]))

    return NetworkLayer(region=REGION, source_crs_epsg=src_crs.to_epsg() or 0,
                        crs_epsg=PROJECT_EPSG, scenario_year=int(scenario_year), links=links)
