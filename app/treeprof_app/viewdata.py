"""Data for the 3D view of one tree and the surveyed area (spec 6.7, RES-1, RES-7, OPEN-4).

Reads the *_info.laz file in blocks, so memory stays small however big the file is, keeps at most
MAX_POINTS points per tree (a random sample when a tree has more), and saves them in one small file
next to the results. Needs laspy (with a LAZ backend) and numpy; if they are missing the caller
shows "3D view not available" and everything else still works.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

MAX_POINTS = 20_000          # per tree (RES-7)
CHUNK = 2_000_000            # points read at a time
CELL = 5.0                   # m, grid used for the surveyed area (RES-1)
CACHE_NAME = "view_points.npz"


def build(info_laz, out_path, *, max_points: int = MAX_POINTS, chunk: int = CHUNK, seed: int = 0) -> dict:
    """Make the view file. Returns {"trees": n, "area_ha": a, "points_kept": k}."""
    import laspy
    import numpy as np

    rng = np.random.default_rng(seed)
    kept, seen, cells = {}, {}, set()
    with laspy.open(str(info_laz)) as reader:
        names = list(reader.header.point_format.dimension_names)
        field = "TreeID" if "TreeID" in names else next((n for n in names if "instance" in n.lower()), None)
        if field is None:
            raise ValueError("the file has no tree ID per point")
        for pts in reader.chunk_iterator(chunk):
            x, y, z = np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z)
            ids = np.asarray(pts[field]).astype(np.int64)
            gx, gy = np.floor(x / CELL).astype(np.int64), np.floor(y / CELL).astype(np.int64)
            cells.update(map(tuple, np.unique(np.stack([gx, gy], axis=1), axis=0).tolist()))
            tree = ids > 0
            if not tree.any():
                continue
            ids_t = ids[tree]
            xyz = np.stack([x[tree], y[tree], z[tree]], axis=1)
            order = np.argsort(ids_t, kind="stable")
            ids_t, xyz = ids_t[order], xyz[order]
            starts = np.flatnonzero(np.r_[True, ids_t[1:] != ids_t[:-1]])
            ends = np.r_[starts[1:], len(ids_t)]
            for a, b in zip(starts, ends):
                tid = int(ids_t[a])
                block = xyz[a:b]
                seen[tid] = seen.get(tid, 0) + len(block)
                have = kept.get(tid)
                merged = block if have is None else np.concatenate([have, block])
                if len(merged) > max_points:                    # keep a random sample of what was seen so far
                    merged = merged[rng.choice(len(merged), max_points, replace=False)]
                kept[tid] = merged
    tree_ids = np.array(sorted(kept), dtype=np.int64)
    counts = np.array([len(kept[t]) for t in tree_ids], dtype=np.int64)
    offsets = np.r_[0, np.cumsum(counts)]
    points = np.concatenate([kept[t] for t in tree_ids]) if len(tree_ids) else np.zeros((0, 3))
    origin = points.min(axis=0) if len(points) else np.zeros(3)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    with open(part, "wb") as f:
        np.savez_compressed(f, tree_ids=tree_ids, offsets=offsets, origin=origin,
                            points=(points - origin).astype(np.float32))
    part.replace(out_path)
    area_ha = len(cells) * CELL * CELL / 10_000
    return {"trees": int(len(tree_ids)), "area_ha": round(area_ha, 2), "points_kept": int(len(points))}


class TreePoints:
    """The saved view file, opened for reading."""

    def __init__(self, path):
        import numpy as np
        data = np.load(str(path))
        self.tree_ids = data["tree_ids"]
        self.offsets = data["offsets"]
        self.points = data["points"]
        self._index = {int(t): i for i, t in enumerate(self.tree_ids)}

    def __len__(self) -> int:
        return len(self.tree_ids)

    def tree(self, tree_id: int):
        """Points of one tree as an (n, 3) array, moved so the tree stands on z = 0 around x = y = 0.
        None if the tree has no points in the file."""
        i = self._index.get(int(tree_id))
        if i is None:
            return None
        pts = self.points[self.offsets[i]:self.offsets[i + 1]].astype("float64")
        if not len(pts):
            return None
        pts = pts.copy()
        pts[:, 0] -= pts[:, 0].mean()
        pts[:, 1] -= pts[:, 1].mean()
        pts[:, 2] -= pts[:, 2].min()
        return pts


def open_view(path) -> Optional[TreePoints]:
    try:
        return TreePoints(path)
    except Exception:
        return None


def height_colours(z) -> list:
    """One [r, g, b] (0..1) per point: light green at the bottom, dark green at the top."""
    lo, hi = float(z.min()), float(z.max())
    span = (hi - lo) or 1.0
    ramp = [(0.81, 0.91, 0.85), (0.61, 0.81, 0.69), (0.37, 0.68, 0.52), (0.18, 0.52, 0.38), (0.06, 0.29, 0.20)]
    out = []
    for v in z:
        k = min(int((float(v) - lo) / span * 5), 4)
        out.append(list(ramp[k]))
    return out
