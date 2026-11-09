"""
DemandIQ Forecasting — the engineer's own model file (upload, inspect, guess).

An engineer uploads their agency's loaded network (zipped shapefile, the
shapefile parts, a GeoPackage or GeoJSON). It is streamed into a private temp
dir under a random token, checked (line geometry, a coordinate system), and its
columns are read from the schema plus a few rows — never the whole file. The
token + a column mapping later drive adapters/generic.py through
/forecast/ingest-match; the file is deleted after that ingest or after TTL.

Security: a client never names a server path. Saved files get server-chosen
names (layer.<ext>); zip member paths are ignored (basename only); upload ids
are checked against the token alphabet before they touch the filesystem.
"""

import json
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

UPLOAD_ROOT = Path(tempfile.gettempdir()) / "demandiq-uploads"
TTL_S = 2 * 3600
MAX_EXTRACTED_BYTES = 1024 ** 3     # unzipped size cap (zip-bomb guard)
_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")   # secrets.token_urlsafe(16)
# Saved name per accepted extension; anything else (.sbn, .xml, .qmd…) is ignored.
_KEEP = {".shp": ".shp", ".shx": ".shx", ".dbf": ".dbf", ".prj": ".prj", ".cpg": ".cpg",
         ".gpkg": ".gpkg", ".geojson": ".geojson", ".json": ".geojson"}
CHUNK = 1024 * 1024


class UploadError(ValueError):
    """Bad upload — the message is shown to the engineer as-is."""


def _cleanup():
    """Lazily drop uploads older than the TTL."""
    if not UPLOAD_ROOT.exists():
        return
    now = time.time()
    for d in UPLOAD_ROOT.iterdir():
        try:
            if now - d.stat().st_mtime > TTL_S:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _store(d, name, src, budget):
    """Copy one stream into d as layer.<ext>; returns bytes written."""
    ext = _KEEP.get(Path(name.replace("\\", "/")).suffix.lower())
    if ext is None:
        return 0
    out = d / f"layer{ext}"
    if out.exists():
        raise UploadError("The upload holds more than one map layer — upload one network file at a time.")
    n = 0
    with open(out, "wb") as f:
        while chunk := src.read(CHUNK):
            n += len(chunk)
            if n > budget:
                raise UploadError("The unzipped file is too large (limit 1 GB).")
            f.write(chunk)
    return n


def save_upload(files):
    """files: [(filename, binary stream)] → (upload id, dir). Streams to disk."""
    _cleanup()
    UPLOAD_ROOT.mkdir(mode=0o700, exist_ok=True)
    uid = secrets.token_urlsafe(16)
    d = UPLOAD_ROOT / uid
    d.mkdir(mode=0o700)
    try:
        budget = MAX_EXTRACTED_BYTES
        for name, stream in files:
            if Path(name or "").suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(stream) as z:
                        for m in z.infolist():
                            base = Path(m.filename.replace("\\", "/")).name
                            if m.is_dir() or base.startswith(".") or "__MACOSX" in m.filename:
                                continue
                            with z.open(m) as src:
                                budget -= _store(d, base, src, budget)
                except zipfile.BadZipFile:
                    raise UploadError("That .zip file can't be opened — is it complete?")
            else:
                budget -= _store(d, name or "", stream, budget)
        return uid, d
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise


def layer_path(d):
    """The one readable layer in an upload dir, or UploadError saying what's missing."""
    have = {p.suffix for p in d.iterdir()}
    kinds = have & {".shp", ".gpkg", ".geojson"}
    if not kinds:
        raise UploadError("No map file found. Upload a zipped shapefile (.zip), the .shp with its "
                          ".shx, .dbf and .prj files, a GeoPackage (.gpkg) or GeoJSON. "
                          "Cube .net files can't be read — export the loaded network to a shapefile first.")
    if len(kinds) > 1:
        raise UploadError("The upload holds more than one kind of map file — upload one network at a time.")
    kind = kinds.pop()
    if kind == ".shp":
        missing = [e for e in (".shx", ".dbf") if e not in have]
        if missing:
            raise UploadError(f"The shapefile is missing its {' and '.join(missing)} file — "
                              "upload all the parts, or zip them together.")
        if ".prj" not in have:
            raise UploadError("The shapefile has no .prj file, so its coordinate system is unknown. "
                              "Add the .prj that came with it (or re-export with a coordinate system).")
    return d / f"layer{kind}"


def resolve(upload_id):
    """Upload id → layer path. Never accepts a path; expired/unknown → UploadError."""
    if not isinstance(upload_id, str) or not _ID.match(upload_id):
        raise UploadError("Unknown upload — upload the model file again in step 1.")
    _cleanup()
    d = UPLOAD_ROOT / upload_id
    meta = d / "meta.json"
    if not meta.exists():
        raise UploadError("This uploaded model file has expired (files are kept 2 hours) — "
                          "upload it again in step 1.")
    return d / json.loads(meta.read_text())["file"]


def delete(upload_id):
    if isinstance(upload_id, str) and _ID.match(upload_id):
        shutil.rmtree(UPLOAD_ROOT / upload_id, ignore_errors=True)


# ── Inspect ───────────────────────────────────────────────────────────────────
def inspect_upload(uid, d, intersections=()):
    """Validate the saved layer and describe it for the column-check step."""
    import pyogrio
    from pyproj import CRS, Transformer

    path = layer_path(d)
    try:
        info = pyogrio.read_info(path, force_total_bounds=True)
        sample = pyogrio.read_dataframe(path, read_geometry=False, max_features=5)
    except Exception:
        raise UploadError("DemandIQ can't read that file as a map layer — is it a shapefile, "
                          "GeoPackage or GeoJSON?")
    if "LINESTRING" not in (info.get("geometry_type") or "").upper():
        raise UploadError(f"The file holds {info.get('geometry_type') or 'no'} shapes, not road lines. "
                          "Upload the loaded highway network (links), not zones or nodes.")
    crs = info.get("crs") or ("EPSG:4326" if path.suffix == ".geojson" else None)
    if not crs:
        raise UploadError("The file has no coordinate system. Re-export it with one.")
    try:
        bounds = Transformer.from_crs(CRS(crs), 4326, always_xy=True).transform_bounds(*info["total_bounds"])
    except Exception:
        raise UploadError("The file's coordinate system isn't recognised. Re-export it in a standard one.")

    fields = []
    for name, dtype in zip(info["fields"], info["dtypes"]):
        vals = [v for v in sample[name].tolist() if v is not None and v == v] if name in sample else []
        fields.append({"name": name, "type": "number" if dtype.startswith(("int", "float")) else "text",
                       "sample": _plain(vals[0]) if vals else None})
    guess = guess_mapping(fields)
    # Distinct values of the class columns, so the engineer can pick connector values.
    m = guess["mapping"]
    values = {}
    for c in {m.get("fclass"), m.get("connector_field")} - {None}:
        col = pyogrio.read_dataframe(path, columns=[c], read_geometry=False)[c]
        values[c] = sorted({_plain(v) for v in col.dropna().unique()[:200]}, key=str)[:50]
    cf = m.get("fclass")
    if cf:
        if not m.get("connector_values"):
            hits = [v for v in values[cf] if re.search(r"connector|centroid|dummy", str(v), re.I)]
            if hits:
                guess["mapping"]["connector_field"], guess["mapping"]["connector_values"] = cf, hits
                guess["confidence"]["connector_field"] = "low"

    outside = [it.get("num") for it in intersections
               if it.get("lat") is not None and it.get("lng") is not None
               and not (bounds[0] <= float(it["lng"]) <= bounds[2] and bounds[1] <= float(it["lat"]) <= bounds[3])]
    (d / "meta.json").write_text(json.dumps({"file": path.name, "fields": [f["name"] for f in fields],
                                             "crs": crs}))
    return {"upload_id": uid, "format": {".shp": "Shapefile", ".gpkg": "GeoPackage",
                                         ".geojson": "GeoJSON"}[path.suffix],
            "feature_count": info.get("features"), "crs": crs,
            "bounds_4326": [round(b, 6) for b in bounds], "intersections_outside": outside,
            "fields": fields, "values": values, **guess}


def _plain(v):
    return v.item() if hasattr(v, "item") else v


def fields_of(upload_id):
    resolve(upload_id)
    return json.loads((UPLOAD_ROOT / upload_id / "meta.json").read_text())["fields"]


# ── Column mapping ────────────────────────────────────────────────────────────
# Roles a mapping can fill. Volumes are model PERIOD totals per direction. With
# no B→A columns, each record is one direction and reverse records are paired.
ROLES = ("link_id", "from_node", "to_node", "am_ab", "am_ba", "pm_ab", "pm_ba",
         "daily", "length_mi", "name", "lanes_ab", "lanes_ba", "fclass", "connector_field")

KNOWN_SCHEMAS = {
    "SANDAG": {"link_id": "hwycov_id", "from_node": "from_node", "to_node": "to_node",
               "am_ab": "ab_am_flow", "am_ba": "ba_am_flow", "pm_ab": "ab_pm_flow", "pm_ba": "ba_pm_flow",
               "daily": "total_flow", "length_mi": "len_mile", "name": "link_name",
               "lanes_ab": "ab_am_lane", "lanes_ba": "ba_am_lane", "fclass": "fc_desc",
               "connector_field": "fc", "connector_values": [10]},
    # TM1 links are one-way records (no B→A columns). Daily = VOL24HR_TOT here;
    # the built-in MTC adapter sums periods because the 2050 DBF truncates it.
    "MTC": {"from_node": "A", "to_node": "B", "am_ab": "VOLAM_TOT", "pm_ab": "VOLPM_TOT",
            "daily": "VOL24HR_TOT", "length_mi": "DISTANCE", "lanes_ab": "LANES", "fclass": "FT",
            "connector_field": "FT", "connector_values": [6]},
}

_NAMES = {
    "link_id": {"linkid", "link_id", "hwycov_id", "linkno", "link_no", "link"},
    "from_node": {"a", "anode", "a_node", "from_node", "fromnode", "fnode", "from", "inode", "from_id", "fromid"},
    "to_node": {"b", "bnode", "b_node", "to_node", "tonode", "tnode", "to", "jnode", "to_id", "toid"},
    "daily": {"total_flow", "tot_flow", "vol24hr_tot", "adt", "aadt", "daily", "daily_vol", "dly_vol",
              "vol_daily", "daily_volume", "vol24", "totflow", "dy_vol"},
    "length_mi": {"len_mile", "length_mi", "len_mi", "miles", "mile", "distance", "dist_mi", "length_mile"},
    "name": {"name", "street", "link_name", "st_name", "streetname", "street_name", "roadname",
             "road_name", "fullname", "rd_name", "linkname"},
    "fclass": {"fc_desc", "fc", "ft", "fclass", "func_class", "functional_class", "facility_type",
               "factype", "fac_type", "ftype", "class", "classname", "road_class", "fcdesc"},
}
_VOL_WORD = re.compile(r"volume|vol|flow|tot(?:al)?")
_DIR = {"atob": "ab", "a2b": "ab", "fwd": "ab", "forward": "ab",
        "btoa": "ba", "b2a": "ba", "rev": "ba", "reverse": "ba", "bwd": "ba"}


def _tokens(name):
    """'ab_am_flow' → ['ab','am']; 'VOLAM_TOT' → ['am']; 'AM_A_B_VOL' → ['am','ab']."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()   # camelCase → snake
    out = []
    for t in re.split(r"[^a-z0-9]+", s):
        t = _DIR.get(t, _VOL_WORD.sub("", t))
        if out and out[-1] + t in ("ab", "ba"):   # a_b / b_a
            out[-1] += t
        elif t:
            out.append(t)
    return out


def _directional(fields, word_ok, prefer):
    """{(period, dir): [(extra tokens, field)]} for numeric fields."""
    out = {}
    for f in fields:
        if f["type"] != "number" or not word_ok(f["name"].lower()):
            continue
        toks = _tokens(f["name"])
        per = [t for t in toks if t in ("am", "pm")]
        dirs = [t for t in toks if t in ("ab", "ba")]
        if len(per) > 1 or len(dirs) > 1:
            continue
        extra = len([t for t in toks if t not in ("am", "pm", "ab", "ba") and t not in prefer])
        out.setdefault((per[0] if per else None, dirs[0] if dirs else None), []).append((extra, f["name"]))
    return out


def guess_mapping(fields):
    """Best-guess column mapping + 'high'/'low' confidence per role.

    Exact known schemas (SANDAG, MTC) first; else name heuristics. A role with
    no candidate is left out; the UI asks the engineer to check 'low' ones.
    """
    names = {f["name"] for f in fields}
    for schema, m in KNOWN_SCHEMAS.items():
        if all(v in names for k, v in m.items() if k != "connector_values"):
            return {"schema": schema, "mapping": dict(m), "confidence": {k: "high" for k in m if k in ROLES}}

    mapping, conf = {}, {}
    by_lower = {f["name"].lower(): f for f in fields}
    for role, opts in _NAMES.items():
        hits = [by_lower[o]["name"] for o in opts if o in by_lower
                and (role in ("name", "fclass") or by_lower[o]["type"] == "number")]
        if role == "fclass":   # prefer a descriptive text column for display
            hits.sort(key=lambda n: by_lower[n.lower()]["type"] != "text")
        if hits:
            mapping[role], conf[role] = hits[0], "high" if len(hits) == 1 else "low"

    vols = _directional(fields, lambda s: bool(_VOL_WORD.search(s)), {"tot"})
    for per in ("am", "pm"):
        two_way = vols.get((per, "ab")) and vols.get((per, "ba"))
        for d in ("ab", "ba") if two_way else (None,):
            cands = sorted(vols.get((per, d), []))
            if cands:
                role = f"{per}_{d or 'ab'}"
                mapping[role] = cands[0][1]
                conf[role] = "high" if cands[0][0] == 0 and (len(cands) == 1 or cands[1][0] > 0) else "low"
    lanes = _directional(fields, lambda s: "lane" in s, {"lane", "lanes", "am"})
    for d in ("ab", "ba"):
        cands = sorted(lanes.get((None, d), []) + lanes.get(("am", d), []))
        if cands:
            mapping[f"lanes_{d}"], conf[f"lanes_{d}"] = cands[0][1], "low"
    if "lanes_ab" not in mapping and "lanes" in by_lower and by_lower["lanes"]["type"] == "number":
        mapping["lanes_ab"], conf["lanes_ab"] = by_lower["lanes"]["name"], "high"
    return {"schema": None, "mapping": mapping, "confidence": conf}


def check_mapping(mapping, fields):
    """Validate a client mapping against the file's own columns → clean dict."""
    if not isinstance(mapping, dict):
        raise UploadError("Pick the file's columns in step 1 first.")
    m = {}
    for role in ROLES:
        v = mapping.get(role)
        if v in (None, ""):
            continue
        if v not in fields:
            raise UploadError(f"Column {str(v)[:40]!r} isn't in the uploaded file.")
        m[role] = v
    if "am_ab" not in m:
        raise UploadError("Pick the column with the morning-peak volume.")
    if ("from_node" in m) != ("to_node" in m):
        raise UploadError("Pick both node columns (start and end of each link), or neither.")
    if "am_ba" not in m and "pm_ba" in m:
        raise UploadError("The file has a B→A evening column but no B→A morning column — pick both or neither.")
    if "am_ba" in m and "pm_ab" in m and "pm_ba" not in m:
        raise UploadError("Pick the evening-peak B→A column too (the morning has both directions).")
    vals = mapping.get("connector_values") or []
    if "connector_field" in m:
        if not isinstance(vals, list) or not vals:
            raise UploadError("Say which values mark centroid connectors, or clear that column.")
        m["connector_values"] = [str(v) for v in vals][:50]
    return m
