"""A small made-up forest for tests: sloping ground and a few trees (stem + crown), about 125,000
points. Nothing here comes from a real site.

    cloud.las        what a drone export looks like (no tree IDs)
    sat_result.laz   the same points with PredInstance_SAT; three crowns are cut in two pieces, the
                     way SegmentAnyTree sometimes does, so merge_sat.py has something to join
"""
import sys
from pathlib import Path

import laspy
import numpy as np

EPSG = 7855


def forest(n_trees=12, seed=1, size=60.0):
    rng = np.random.default_rng(seed)
    gx, gy = rng.uniform(0, size, 60000), rng.uniform(0, size, 60000)
    gz = 100 + 0.02 * gx + rng.normal(0, 0.03, len(gx))
    xs, ys, zs, ids = [gx], [gy], [gz], [np.zeros(len(gx), np.int32)]
    side = int(np.ceil(np.sqrt(n_trees)))
    k = 0
    for i in range(side):
        for j in range(side):
            if k >= n_trees:
                break
            k += 1
            cx = (i + 0.5) * size / side + rng.normal(0, 0.8)
            cy = (j + 0.5) * size / side + rng.normal(0, 0.8)
            h, r = rng.uniform(12, 24), rng.uniform(2.5, 4.5)
            base = 100 + 0.02 * cx
            ns, nc = 400, 5000
            xs.append(cx + rng.normal(0, 0.12, ns))
            ys.append(cy + rng.normal(0, 0.12, ns))
            zs.append(base + rng.uniform(0.2, h * 0.55, ns))
            ids.append(np.full(ns, k, np.int32))
            u = rng.normal(size=(nc, 3))
            u /= np.linalg.norm(u, axis=1)[:, None]
            rad = rng.uniform(0.6, 1.0, nc) ** (1 / 3)
            xs.append(cx + u[:, 0] * rad * r)
            ys.append(cy + u[:, 1] * rad * r)
            zs.append(base + h * 0.72 + u[:, 2] * rad * h * 0.27)
            ids.append(np.full(nc, k, np.int32))
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(zs), np.concatenate(ids)


def write(path, x, y, z, ids=None, epsg=EPSG):
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.offsets = [np.floor(x.min()), np.floor(y.min()), np.floor(z.min())]
    header.scales = [0.001, 0.001, 0.001]
    if epsg:
        from pyproj import CRS
        header.add_crs(CRS.from_epsg(epsg))
    las = laspy.LasData(header)
    las.x, las.y, las.z = x, y, z
    if ids is not None:
        las.add_extra_dim(laspy.ExtraBytesParams(name="PredInstance_SAT", type=np.int32))
        las["PredInstance_SAT"] = ids
    las.write(str(path))


def make(cloud_path, sat_path, n_trees=12):
    """Returns (number of points, number of trees, number of pieces SegmentAnyTree 'found')."""
    x, y, z, ids = forest(n_trees)
    x, y = x + 500000, y + 6250000
    Path(cloud_path).parent.mkdir(parents=True, exist_ok=True)
    Path(sat_path).parent.mkdir(parents=True, exist_ok=True)
    write(cloud_path, x, y, z)
    sat = ids.copy()
    for t in (2, 5, 9):
        piece = (ids == t) & (x > x[ids == t].mean() + 1.2)
        sat[piece] = n_trees + t
    write(sat_path, x, y, z, sat)
    return len(x), n_trees, int(len(np.unique(sat[sat > 0])))


if __name__ == "__main__":
    out = Path(sys.argv[1])
    print(make(out / "cloud.las", out / "sat_result.laz", int(sys.argv[2]) if len(sys.argv) > 2 else 12))
