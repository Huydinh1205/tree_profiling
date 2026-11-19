"""Stand-in for processes/process2_segment.py (see _fake.py): writes the report folder that the app
turns into report.html (RES-11), without measuring anything."""
import argparse
import base64
import sys
from pathlib import Path

import _fake as F

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

ap = argparse.ArgumentParser()
ap.add_argument("las")
ap.add_argument("--out")
ap.add_argument("--models", nargs="+")
ap.add_argument("--external", nargs="*", default=[])
ap.add_argument("--site-name")
a = ap.parse_args()
out = Path(a.out)
F.record(out.parent, "report")
if F.fail_mode("report") == "error":
    print("Traceback (most recent call last):\n  File \"process2_segment.py\", line 1, in <module>\nImportError: no rasterio")
    sys.exit(1)
out.mkdir(parents=True, exist_ok=True)
for name in ("chm_watershed", "external_sat", "external_sat_merged"):
    (out / name).mkdir(exist_ok=True)
    (out / name / "points_treeid.laz").write_bytes(b"big")
    (out / name / "trees.csv").write_text("tree_id\n")
(out / "fig_crowns_on_chm.png").write_bytes(PNG)
site = a.site_name or Path(a.las).stem
(out / "report.md").write_text(
    f"# Tree profiling: {site}\n\n## Results on this site\n\n| Model | Trees found |\n|---|---|\n"
    "| CHM watershed | 5 |\n| sat | 6 |\n| sat_merged | 4 |\n\n## Figures\n\nCrown outlines over the canopy height model:\n\n"
    "![crowns](fig_crowns_on_chm.png)\n\n## Notes\n\n- Outputs per model are in sub-folders: `trees_crowns.gpkg`\n"
    "- `run_metadata.json` records the input file.\n- Crown base, volumes and density are LiDAR proxies.\n",
    encoding="utf-8")
print(f"\nDone in 1s. Open {out / 'report.html'}")
