"""Takes the place of processes/sat_docker.py when the real merge_sat.py and tree_info.py are tested
on a computer without Docker: it hands over a result made beforehand (TREEPROF_TEST_SAT_RESULT) and
prints the line the real script prints first. Same arguments as the real script.
TREEPROF_TEST_SAT_SECONDS makes it wait first, as if Docker was working."""
import argparse
import os
import shutil
import struct
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("input")
ap.add_argument("--out", required=True)
ap.add_argument("--log")
ap.add_argument("--max-points", type=int, default=20_000_000)
a = ap.parse_args()
with open(a.input, "rb") as f:
    head = f.read(375)
n = struct.unpack_from("<I", head, 107)[0]
if a.log:
    Path(a.log).parent.mkdir(parents=True, exist_ok=True)
    Path(a.log).write_text("no Docker in this test\n", encoding="utf-8")
print(f"SegmentAnyTree on {Path(a.input).name} ({n:,} points). Docker output also goes to {a.log}", flush=True)
time.sleep(float(os.environ.get("TREEPROF_TEST_SAT_SECONDS", "0")))      # so a person can see the Running screen
shutil.copyfile(os.environ["TREEPROF_TEST_SAT_RESULT"], a.out)
print(f"SegmentAnyTree done: {Path(a.out).name}", flush=True)
