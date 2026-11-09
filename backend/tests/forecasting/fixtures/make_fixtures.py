"""Rebuild the tiny SANDAG upload fixtures (needs the real data/sandag files).

Clips hwyLoad_<year>.shp to ~1 km around El Cajon Blvd & 54th St and keeps the
columns the SANDAG adapter reads. renamed_<year>.zip is the same data under an
unknown MPO's column names, to exercise the mapping step.
"""
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[3] / "data" / "sandag"
KEEP = ["hwycov_id", "link_name", "len_mile", "fc", "fc_desc", "from_node", "to_node",
        "total_flow", "ab_am_flow", "ba_am_flow", "ab_pm_flow", "ba_pm_flow",
        "ab_ea_flow", "ba_ea_flow", "ab_md_flow", "ba_md_flow", "ab_ev_flow", "ba_ev_flow",
        "ab_am_lane", "ba_am_lane"]
RENAME = {"hwycov_id": "LINKNO", "link_name": "STREET", "len_mile": "MILES", "fc": "CLASS",
          "fc_desc": "CLASSNAME", "from_node": "ANODE", "to_node": "BNODE", "total_flow": "ADT",
          "ab_am_flow": "AM_AB_VOL", "ba_am_flow": "AM_BA_VOL", "ab_pm_flow": "PM_AB_VOL",
          "ba_pm_flow": "PM_BA_VOL", "ab_am_lane": "LANES_AB", "ba_am_lane": "LANES_BA"}
CENTER = (-117.07935, 32.75773)
RADIUS_FT = 3281


def zip_layer(gdf, out_zip, stem):
    tmp = HERE / f"_{stem}"
    tmp.mkdir(exist_ok=True)
    gdf.to_file(tmp / f"{stem}.shp")
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(tmp.iterdir()):
            z.write(p, f"{stem}/{p.name}")   # a folder inside the zip, like a real export
            p.unlink()
    tmp.rmdir()


def main():
    for year in (2025, 2050):
        c = gpd.GeoSeries([Point(CENTER)], crs=4326).to_crs(2230).iloc[0]
        gdf = gpd.read_file(DATA / f"hwyLoad_{year}.shp",
                            bbox=(c.x - RADIUS_FT, c.y - RADIUS_FT, c.x + RADIUS_FT, c.y + RADIUS_FT))
        gdf = gdf[KEEP + ["geometry"]]
        out = HERE / f"sandag_{year}"
        out.mkdir(exist_ok=True)
        gdf.to_file(out / f"hwyLoad_{year}.shp")
        zip_layer(gdf.rename(columns=RENAME), HERE / f"renamed_{year}.zip", f"network_{year}")
        print(year, len(gdf), "links")


if __name__ == "__main__":
    sys.exit(main())
