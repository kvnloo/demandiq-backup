"""
DemandIQ Forecasting — Module 3: network ingest orchestration.

A thin layer over the region adapters. The adapter (forecasting.adapters.sandag)
already does the heavy lifting — CRS reprojection (4326↔2230), the spatial filter
to the project area, and the canonical transform. This module:

  1. Picks the server-side network file for each model year.
  2. Calls the region adapter once per supplied model year.
  3. Runs a base↔horizon link_id overlap sanity check when both are present.

Filter-first ordering is the whole point: each returned NetworkLayer is already
spatially filtered to the 3-5 project intersections, so it is KB-scale and
serializes straight into the project model. The 90+ MB shapefile is read,
filtered, and discarded per call — never persisted whole.

Two-model-year note: NCHRP 765 Method 1 needs a BASE and a HORIZON loaded
network (refinement in module 5 joins them by link_id). This function accepts 1
or 2 networks so the second year can slot in when available. With both, the
overlap check doubles as a link_id-stability check across model years — if
SANDAG renumbered links between series, it fails loud here rather than silently
corrupting the refinement join.
"""

from pathlib import Path

from forecasting import upload
from forecasting.adapters.generic import ingest_upload
from forecasting.adapters.hpms import ingest_hpms
from forecasting.adapters.mtc import ingest_mtc
from forecasting.adapters.sandag import ingest_sandag

# ── Region adapter registry ───────────────────────────────────────────────────
# A RegionAdapter is the callable contract `ingest_sandag` satisfies:
#
#   ingest_<region>(source, scenario_year, project_intersections_4326,
#                   *, filter_radius_ft=300) -> NetworkLayer
#
# It reads a region's raw network, spatially filters to the project, reprojects
# to the project CRS, and emits a canonical NetworkLayer whose links populate the
# fields the engine consumes (flow, length_mi, daily_total). Adding a region =
# implement that contract and register it here — NOT editing a conditional. See
# docs/forecasting-multicounty.md ("How to add a region"). HPMS_CA is the
# statewide fallback: every California county, read live from FHWA (no files).
ADAPTERS = {
    "SANDAG": ingest_sandag,
    "HPMS_CA": ingest_hpms,
    "MTC": ingest_mtc,        # Bay Area; file picked server-side by model year
    "UPLOAD": ingest_upload,  # the engineer's own model file (upload id + column mapping)
}
# Server-side SANDAG network assets live under the repo's data/ dir (gitignored),
# one file per model year: data/sandag/hwyLoad_<year>.shp. Like MTC, the file is
# picked server-side from the model year — a client never sends a path.
REPO_ROOT = Path(__file__).resolve().parents[2]
SANDAG_DATA_DIR = REPO_ROOT / "data" / "sandag"


def sandag_years():
    """Model years with a hwyLoad_<year>.shp on this server."""
    return sorted(int(p.stem[len("hwyLoad_"):])
                  for p in SANDAG_DATA_DIR.glob("hwyLoad_[0-9][0-9][0-9][0-9].shp"))


def sandag_path(year):
    """data/sandag/hwyLoad_<year>.shp, or ValueError listing the available years."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        raise ValueError(f"model year must be a year, got {year!r}")
    path = SANDAG_DATA_DIR / f"hwyLoad_{y}.shp"
    if not path.exists():
        raise ValueError(f"no SANDAG model run for {y} — available model years: {sandag_years()}")
    return path


def _base_horizon_overlap(base_layer, horizon_layer):
    """Fraction of base-year links whose link_id also appears in the horizon
    network. ~1.0 when IDs are stable and the corridor is unchanged; ~0 when the
    wrong files were tagged or IDs were renumbered across model series."""
    base_ids = {l.link_id for l in base_layer.links}
    horizon_ids = {l.link_id for l in horizon_layer.links}
    if not base_ids:
        return 0.0
    return len(base_ids & horizon_ids) / len(base_ids)


def ingest_networks(
    project_intersections_4326,
    networks,
    *,
    region="SANDAG",
    filter_radius_ft=300,
    min_overlap=0.5,
):
    """Ingest one or two SANDAG model-year networks, filtered to the project.

    Args:
        project_intersections_4326: iterable of {lat, lng, ...} (EPSG:4326).
        networks: list of specs, each:
            { "role": "base"|"horizon", "scenario_year": int, "path"?: shp_path }
            `path` is for tests/tools only; without it SANDAG picks
            data/sandag/hwyLoad_<scenario_year>.shp (the API never passes one).
            UPLOAD specs carry "upload_id" + "mapping" instead; the uploads are
            deleted once every layer is ingested.
        region: a key of ADAPTERS — "SANDAG" (model file) or "HPMS_CA" (statewide, live).
        filter_radius_ft: passed through to the adapter (default 300).
        min_overlap: minimum base→horizon link_id overlap fraction required when
            both roles are present (default 0.5). Below it → fail loud.

    Returns:
        { role: NetworkLayer } — one filtered NetworkLayer per supplied network.

    Raises:
        ValueError: region with no registered adapter, empty networks, missing
            role/scenario_year, duplicate role, no model file for the year, or base↔horizon
            overlap below min_overlap.
    """
    adapter = ADAPTERS.get(region)
    if adapter is None:
        raise ValueError(
            f"ingest_networks: no adapter registered for region {region!r} "
            f"(available: {sorted(ADAPTERS)}). Register one in ADAPTERS — see "
            "docs/forecasting-multicounty.md.")
    if not networks:
        raise ValueError("ingest_networks: no networks supplied")

    layers = {}
    for spec in networks:
        role = spec.get("role")
        if role is None:
            raise ValueError(f"ingest_networks: network spec missing 'role': {spec}")
        if role in layers:
            raise ValueError(f"ingest_networks: duplicate network role {role!r}")
        if "scenario_year" not in spec:
            raise ValueError(f"ingest_networks: network role {role!r} missing 'scenario_year'")
        kw = {}
        if region == "UPLOAD":
            path = upload.resolve(spec.get("upload_id"))
            kw["mapping"] = upload.check_mapping(spec.get("mapping"), upload.fields_of(spec["upload_id"]))
        else:
            path = spec.get("path") or (sandag_path(spec["scenario_year"]) if region == "SANDAG" else "live")
        layers[role] = adapter(
            path, spec["scenario_year"], project_intersections_4326,
            filter_radius_ft=filter_radius_ft, **kw,
        )

    if "base" in layers and "horizon" in layers:
        frac = _base_horizon_overlap(layers["base"], layers["horizon"])
        if frac < min_overlap:
            raise ValueError(
                f"ingest_networks: base↔horizon link_id overlap {frac:.0%} is below "
                f"{min_overlap:.0%} — the two networks likely aren't the same corridor "
                "across model years (wrong files tagged, or SANDAG renumbered link IDs "
                "between series). Refinement joins by link_id and would be unreliable."
            )

    if region == "UPLOAD":   # the clipped layers are what the project keeps
        for spec in networks:
            upload.delete(spec.get("upload_id"))
    return layers
