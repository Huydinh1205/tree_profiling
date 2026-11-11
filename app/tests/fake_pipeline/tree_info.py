"""Stand-in for processes/tree_info.py (see _fake.py)."""
import argparse
import sys
from pathlib import Path

import _fake as F
import _shp

COLUMNS = ("tree_uid,tree_id,top_x,top_y,top_z,height_m,crown_diam_m,crown_width_max_m,crown_area_m2,"
           "crown_volume_m3,foliage_volume_m3,crown_base_m,crown_depth_m,canopy_density_pct,nn_spacing_m,n_points,"
           "h_p25_m,h_p50_m,h_p75_m,height_sd_m,point_density_m2,qc_flag,species_code,species_prob,"
           "foliage_condition,thermal_anomaly,confidence,koala_value")

ap = argparse.ArgumentParser()
ap.add_argument("files", nargs="+")
ap.add_argument("--site")
a = ap.parse_args()
path = Path(a.files[0])
F.record(path.parent, "measure")
mode = F.fail_mode("measure")
trees = int(F.CONF.get("trees", 4))
stem = path.stem

print(f"\nReading {path.name} ...")
if mode == "error":
    print("Traceback (most recent call last):\n  File \"tree_info.py\", line 1, in <module>\nKeyError: 'boom'")
    sys.exit(1)
print(f"  {F.header_points(path):,} points, tree IDs from 'PredInstance_SAT', ground: file")
if mode == "no_trees" or trees == 0:
    sys.exit("No trees in this file (all tree IDs are 0).")
tag = "".join(ch if ch.isalnum() else "_" for ch in (a.site or stem)).strip("_").upper() or "SITE"
print(f"  {trees} trees measured")
F.write_cloud(path, path.with_name(stem + "_info.laz"))
rows = [COLUMNS]
for i in range(1, trees + 1):
    h = 10.0 * i
    rows.append(f"{tag}_TREE{i:05d},{i},{500000 + i},{6250000 + i},{100 + h},{h},4.0,4.5,12.6,60.0,20.0,3.0,7.0,33.3,"
                f"5.0,{800 * i},5.0,6.5,8.0,2.0,63.5,ok,,,,,,")
path.with_name(stem + "_trees.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
crowns = [(i, [(500000 + i - 2, 6250000 + i - 2), (500000 + i + 2, 6250000 + i - 2), (500000 + i + 2, 6250000 + i + 2),
               (500000 + i - 2, 6250000 + i + 2)]) for i in range(1, trees + 1)]
_shp.write_crowns(path.with_name(stem + "_crowns.shp"), crowns, z=100.0)
path.with_name(stem + "_crowns.prj").write_text("PROJCS[...]", encoding="utf-8")
print(f"  -> {stem}_info.laz, {stem}_trees.csv, {trees} crown outlines -> {stem}_crowns.shp  (1s)")
