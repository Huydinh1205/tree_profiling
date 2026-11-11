"""Stand-in for processes/sat_docker.py (see _fake.py). Same arguments, same printed lines."""
import argparse
import sys
import time
from pathlib import Path

import _fake as F

ap = argparse.ArgumentParser()
ap.add_argument("input")
ap.add_argument("--out", required=True)
ap.add_argument("--log")
ap.add_argument("--max-points", type=int, default=20_000_000)
a = ap.parse_args()
out = Path(a.out)
work = out.parent
F.record(work, "find_trees")
n = F.header_points(a.input)
mode = F.fail_mode("find_trees")
tiles = int(F.CONF.get("tiles", 0))
pause = float(F.CONF.get("tile_seconds", 0))
if a.log:
    Path(a.log).parent.mkdir(parents=True, exist_ok=True)
    Path(a.log).write_text("stand-in Docker output\n")

if mode == "docker":
    print("\nSegmentAnyTree: Docker Desktop is not running. Open Docker Desktop, wait for 'Engine running', "
          "then run again.", flush=True)
    sys.exit(1)
print(f"SegmentAnyTree on {Path(a.input).name} ({n:,} points). Docker output also goes to {a.log}", flush=True)
if mode == "oom":
    print("joblib.externals.loky.process_executor.TerminatedWorkerError: A worker process was terminated "
          "{SIGKILL(-9)}", flush=True)
    print("\nSegmentAnyTree: SegmentAnyTree produced no result (Docker exit code 137).\n"
          "OUT OF RAM: Windows/WSL killed SegmentAnyTree (SIGKILL) because the file is too big for the memory "
          "Docker may use.\nFull output in sat_docker.log.", flush=True)
    sys.exit(1)
if mode == "hang":
    time.sleep(600)

if tiles:
    parts = work / "sat_result_tiles_100x100m"
    parts.mkdir(exist_ok=True)
    print(f"Too many points for one SegmentAnyTree run ({n:,} > {a.max_points:,}): 1 x {tiles} tiles of "
          f"100 x 100 m (+ 20 m buffer), {tiles} Docker runs. A tile already done is skipped.", flush=True)
    for g in range(1, tiles + 1):
        part = parts / f"tile_01_{g:02d}.laz"
        label = f"tile {g}/{tiles} (column 1, row {g}): 1,000 points + buffer = 1,200"
        if part.exists():
            print(f"\n--- {label}: already done ({part.name}), skipped", flush=True)
            continue
        print(f"\n--- {label}", flush=True)
        time.sleep(pause)
        if mode == "crash" and g == int(F.CONF.get("fail_tile", 2)):
            print("Segmentation fault (core dumped)", flush=True)
            print(f"\nSegmentAnyTree produced no result for {label} (Docker exit code 139).\n"
                  "SegmentAnyTree crashed (Segmentation fault).\nFull output in sat_docker.log.", flush=True)
            print(f"\nSegmentAnyTree: SegmentAnyTree failed on 1 tile(s) even thinned: {part.stem}.\n"
                  f"The other tiles are done and kept in {parts}; running again only retries these.", flush=True)
            sys.exit(1)
        part.write_bytes(b"tile")
        print(f"  1,200 result points mapped onto 1,200 input points\n  done in 0.1 min -> {part}", flush=True)
    print(f"\nAll {tiles} tiles done. Putting them together ...", flush=True)
    print(f"Writing {out} ...", flush=True)
else:
    time.sleep(pause)
    print(f"\nSegmentAnyTree done in 0.1 min. Writing {out} ...", flush=True)
F.write_cloud(a.input, out)
print(f"  {int(F.CONF.get('trees', 4)) + 2:,} trees in {out.name}", flush=True)
print(f"Done in 0.2 min -> {out}", flush=True)
