"""Shared by the stand-in scripts in this folder (test data D6).

They take the same arguments as sat_docker.py, merge_sat.py and tree_info.py, print the same kind of
lines and leave the same files, but do no real work, so the app can be tested without Docker, a
graphics card or a point cloud library. The FAKE_PIPELINE environment variable (JSON) sets how they
behave:  {"tiles": 3, "tile_seconds": 0.2, "trees": 4, "fail": {"join": "error"}, "fail_tile": 2}
"""
import json
import os
import struct
import sys
from pathlib import Path

CONF = json.loads(os.environ.get("FAKE_PIPELINE") or "{}")


def fail_mode(step):
    return (CONF.get("fail") or {}).get(step)


def record(workdir, step):
    """Remember that this step was called (the tests check that a finished step is not called again)."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    with open(workdir / "fake_calls.txt", "a", encoding="utf-8") as f:
        f.write(step + " " + " ".join(sys.argv[1:]) + "\n")
    (workdir / "fake_pid.txt").write_text(str(os.getpid()))


def header_points(path):
    head = Path(path).read_bytes()[:375]
    n = struct.unpack_from("<I", head, 107)[0]
    if head[24:26] >= bytes((1, 4)) and len(head) >= 255:
        n = struct.unpack_from("<Q", head, 247)[0] or n
    return n


def write_cloud(src, dst, drop_points=0):
    """A stand-in LAS/LAZ file: the source's header, optionally claiming fewer points."""
    head = bytearray(Path(src).read_bytes()[:375])
    if drop_points:
        struct.pack_into("<Q", head, 247, header_points(src) - drop_points)
    Path(dst).write_bytes(bytes(head))
