# Model drop

Put regional travel-model files here (any format) — Hermes picks them up and
works out what's inside.

**One folder per model**, e.g. `model-drop/caltrans-d11-2019/`. Add a line
below saying where it came from (agency, model name, base / future year)
if you know.

What helps most:
- **Shapefile** — drop all the parts together (`.shp .shx .dbf .prj`, plus `.cpg` if there),
  or one `.zip` of them.
- **Cube** — the network files (`.net`) and any loaded-network or volume files.
  Cube's `.net` is a closed binary format; if you can, also export the network
  from Cube to a shapefile or `.dbf`/`.csv`.
- Any read-me, data dictionary or report that names the fields.

Limits: GitHub's web upload takes files up to **25 MB**; git push up to **100 MB**
per file. Bigger than that → zip it, or leave it in Box/Drive and paste the link here.

| Folder | Source | Base year | Future year | Notes |
|---|---|---|---|---|
| | | | | |
