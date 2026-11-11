"""Stand-in for processes/merge_sat.py (see _fake.py). Like the real script it does not flush what
it prints and writes straight to the final file name."""
import argparse
import sys
import time
from pathlib import Path

import _fake as F

ap = argparse.ArgumentParser()
ap.add_argument("files", nargs="+")
a = ap.parse_args()
path = Path(a.files[0])
F.record(path.parent, "join")
mode = F.fail_mode("join")
trees = int(F.CONF.get("trees", 4))
stem = path.stem
out = path.with_name(stem + "_merged.laz")

if mode == "error":
    print("Traceback (most recent call last):\n  File \"merge_sat.py\", line 1, in <module>\nValueError: boom")
    sys.exit(1)
if mode == "hang":
    time.sleep(600)
print(f"{path.name}: {F.header_points(path):,} points, ground from file, field PredInstance_SAT, {trees + 2} trees")
print(f"  merged into {trees} trees  ({{'no own top': 2, 'fragment': 0}}; 35% of the pieces taller than 5 m show "
      "a stem; 0 trees lower than 3 m dropped)")
F.write_cloud(path, out, drop_points=1 if mode == "partial" else 0)      # partial: stopped while writing
F.write_cloud(path, path.with_name(stem + "_merged_colour.laz"))
path.with_name(stem + "_merged.png").write_bytes(b"png")
path.with_name(stem + "_merged_log.csv").write_text("piece,into,rule\n")
print(f"  wrote {out.name}, {stem}_merged_colour.laz, .png and _log.csv in 1 s")
