"""
DemandIQ Forecasting — Bay Area adapter (MTC Travel Model One).

Reads MTC's published Plan Bay Area 2050 loaded highway networks (free, from
MTC's Box share; see Docs/forecasting-multicounty.md) — one folder per model
year under data/mtc/<year>/network_links.shp, EPSG:26910 (UTM 10N, metres).
Covers all nine Bay Area counties.

Source shape → contract
  - TM1 links are ONE-WAY (A→B and B→A are separate records). The contract wants
    one link per road piece with AB/BA flows, so each pair is merged into one
    NetworkLink: from_node = lower node number, AB = the low→high record, BA = the
    reverse. link_id "<lo>-<hi>" is stable across model years because TM1 keeps
    its node numbers between runs.
  - Period volumes VOL{EA,AM,MD,PM,EV}_TOT are 3–8 hour PERIOD totals (AM = 6–10
    am, PM = 3–7 pm per MTC's TimePeriods page), like SANDAG — so the ratio
    method is the operative one and difference needs a peak-hour factor K.
  - Daily = the five period totals summed over both directions.
  - Dummy links (FT 6, zone centroid connectors) are dropped: they meet the
    network at real intersections and would otherwise bind to a leg.

TM1 links carry no street names; matching is by position and bearing.
"""

from pathlib import Path

from forecasting.contract import DirectionalFlow, NetworkLayer, NetworkLink, Period
from forecasting.geo import polyline_length_mi

REGION = "MTC"
MTC_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "mtc"
SOURCE_EPSG = 26910
PROJECT_EPSG = 4326
FT_PER_M = 3.28084
PERIOD_COLS = {p: f"VOL{p.value}_TOT" for p in Period}
DUMMY_FT = 6
# TM1 facility type (FT) → canonical class.
FT_MAP = {1: "Ramp", 2: "Freeway", 3: "Expressway", 4: "Collector", 5: "Ramp",
          7: "Major Arterial", 8: "Freeway", 9: "Other", 10: "Other"}


def available_years():
    return sorted(int(p.name) for p in MTC_DATA_DIR.glob("[0-9]*")
                  if (p / "network_links.shp").exists())


def _f(v):
    try:
        return 0.0 if v is None or v != v else float(v)
    except (TypeError, ValueError):
        return 0.0


def ingest_mtc(_path, scenario_year, project_intersections_4326, *, filter_radius_ft=300):
    """MTC loaded network for one model year → NetworkLayer (EPSG:4326).

    `_path` is ignored: the file is chosen server-side from scenario_year, so a
    client can never point the reader at an arbitrary path.
    """
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString, Point
    from shapely.ops import substring

    from forecasting.matcher import SNAP_TOLERANCE_FT

    year = int(scenario_year)
    shp = MTC_DATA_DIR / str(year) / "network_links.shp"
    if not shp.exists():
        raise ValueError(f"no MTC model run for {year} — available model years: {available_years()}")

    pts = [(float(p["lng"]), float(p["lat"])) for p in project_intersections_4326
           if p.get("lat") is not None and p.get("lng") is not None]
    if not pts:
        raise ValueError("ingest_mtc: no project intersections with lat/lng.")

    to_src = Transformer.from_crs(PROJECT_EPSG, SOURCE_EPSG, always_xy=True)
    to_out = Transformer.from_crs(SOURCE_EPSG, PROJECT_EPSG, always_xy=True)
    xy = [to_src.transform(*p) for p in pts]
    r = filter_radius_ft / FT_PER_M
    pad = r + 50
    bbox = (min(x for x, _ in xy) - pad, min(y for _, y in xy) - pad,
            max(x for x, _ in xy) + pad, max(y for _, y in xy) + pad)
    gdf = gpd.read_file(shp, bbox=bbox)   # spatial read: only the project area

    pts_src = [Point(p) for p in xy]
    rec = {}   # (A, B) → row; keep links whose LINE passes within the radius (ends can be a mile off)
    for _, row in gdf.iterrows():
        if int(row["FT"]) == DUMMY_FT:
            continue
        if any(row.geometry.distance(p) <= r for p in pts_src):
            rec[(int(row["A"]), int(row["B"]))] = row
    if not rec:
        raise ValueError(f"ingest_mtc: no MTC road within {filter_radius_ft} ft of the project "
                         "intersections — is the project inside the nine Bay Area counties?")

    # [link_id, from_node, to_node, AB row, BA row, line from→to (metres), share of the link]
    pieces = []
    for lo, hi in sorted({tuple(sorted(k)) for k in rec}):
        ab, ba = rec.get((lo, hi)), rec.get((hi, lo))
        line = (ab if ab is not None else ba).geometry
        if ab is None:
            line = LineString(list(line.coords)[::-1])   # digitize lo → hi
        pieces.append([f"{lo}-{hi}", lo, hi, ab, ba, line, 1.0])

    # TM1 has no node at most cross streets, so an intersection can sit mid-link. With no
    # node within the matcher's snap distance, split the nearest link there; both halves
    # keep the link's flows. The new node is -(i+1): the same in base and horizon.
    snap = SNAP_TOLERANCE_FT / FT_PER_M
    for i, p in enumerate(pts_src):
        if not pieces or min(Point(c).distance(p) for pc in pieces
                             for c in (pc[5].coords[0], pc[5].coords[-1])) <= snap:
            continue
        k = min(range(len(pieces)), key=lambda j: pieces[j][5].distance(p))
        lid, a, b, ab, ba, line, share = pieces[k]
        if line.distance(p) > snap:
            continue
        d, v = line.project(p), -(i + 1)
        pieces[k:k + 1] = [
            [f"{lid}a", a, v, ab, ba, substring(line, 0, d), share * d / line.length],
            [f"{lid}b", v, b, ab, ba, substring(line, d, line.length), share * (1 - d / line.length)]]

    links = []
    for lid, a, b, ab, ba, line, share in pieces:
        either = ab if ab is not None else ba
        lo, hi = sorted((int(either["A"]), int(either["B"])))   # the model's own nodes, for provenance
        coords = [to_out.transform(x, y) for x, y in line.coords]
        links.append(NetworkLink(
            link_id=lid,
            from_node=a, to_node=b,
            geometry=coords, from_node_xy=coords[0], to_node_xy=coords[-1],
            length_mi=_f(either["DISTANCE"]) * share or polyline_length_mi(coords),
            name=None,
            functional_class=FT_MAP.get(int(either["FT"]), "Other"),
            lanes=int(max(_f(ab["LANES"]) if ab is not None else 0,
                          _f(ba["LANES"]) if ba is not None else 0)),
            flow=DirectionalFlow(
                ab={p: _f(ab[c]) if ab is not None else 0.0 for p, c in PERIOD_COLS.items()},
                ba={p: _f(ba[c]) if ba is not None else 0.0 for p, c in PERIOD_COLS.items()},
            ),
            # the five periods cover 24 h; summed because the 2050 DBF truncates VOL24HR_TOT
            daily_total=sum(_f(x[c]) for x in (ab, ba) if x is not None for c in PERIOD_COLS.values()),
            source_attributes={"A": lo, "B": hi, "FT": int(either["FT"]),
                               "CITYNAME": either.get("CITYNAME"), "one_way": ab is None or ba is None},
        ))

    return NetworkLayer(region=REGION, source_crs_epsg=SOURCE_EPSG, crs_epsg=PROJECT_EPSG,
                        scenario_year=year, links=links)
