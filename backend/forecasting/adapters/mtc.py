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

    def near(c):
        return any((c[0] - x) ** 2 + (c[1] - y) ** 2 <= r * r for x, y in xy)

    rec = {}   # (A, B) → row; keep links with an endpoint within the radius
    for _, row in gdf.iterrows():
        if int(row["FT"]) == DUMMY_FT:
            continue
        cs = list(row.geometry.coords)
        if near(cs[0]) or near(cs[-1]):
            rec[(int(row["A"]), int(row["B"]))] = row
    if not rec:
        raise ValueError(f"ingest_mtc: no MTC road within {filter_radius_ft} ft of the project "
                         "intersections — is the project inside the nine Bay Area counties?")

    links = []
    for lo, hi in sorted({tuple(sorted(k)) for k in rec}):
        ab, ba = rec.get((lo, hi)), rec.get((hi, lo))
        geom_row = ab if ab is not None else ba
        cs = list(geom_row.geometry.coords)
        if ab is None:
            cs = cs[::-1]            # digitize lo → hi
        coords = [to_out.transform(x, y) for x, y in cs]
        either = ab if ab is not None else ba
        links.append(NetworkLink(
            link_id=f"{lo}-{hi}",
            from_node=lo, to_node=hi,
            geometry=coords, from_node_xy=coords[0], to_node_xy=coords[-1],
            length_mi=_f(either["DISTANCE"]) or polyline_length_mi(coords),
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
