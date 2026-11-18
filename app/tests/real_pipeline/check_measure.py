"""Run with the scripts' Python: python check_measure.py <processes folder>. Prints OK, or stops with an error.

Checks the two faults fixed on 18/11/2026 in the measuring code, on a small made-up stand at map-grid coordinates:
  1. canopy density came out empty when coordinates were large (x 541,000, y 6,789,000);
  2. one tree ID made of several separate clusters was measured as one tree (crown width and volume spanned
     the gap between the clusters).
"""
import sys
import types

import numpy as np

sys.path.insert(0, sys.argv[1])
import merge_sat  # noqa: E402
import tree_info  # noqa: E402
import treeseg as T  # noqa: E402

rng = np.random.default_rng(7)


def crown(cx, cy, height, radius, n):
    """A stem and a half-ellipsoid crown of random points. Heights are above ground."""
    r = radius * np.sqrt(rng.random(n)); a = rng.random(n) * 2 * np.pi
    h = height * (0.45 + 0.55 * rng.random(n) * np.sqrt(np.clip(1 - (r / radius) ** 2, 0, 1)))
    stem = np.c_[cx + rng.normal(0, 0.05, 60), cy + rng.normal(0, 0.05, 60), np.linspace(0.2, height * 0.45, 60)]
    return np.r_[np.c_[cx + r * np.cos(a), cy + r * np.sin(a), h], stem]


E, N = 541000.0, 6789000.0                                   # a corner like the company surveys (MGA)
a = crown(E + 10, N + 10, 18, 4, 6000)                       # tree 1
b = crown(E + 50, N + 12, 15, 3, 3000)                       # a second tree 40 m away that got the ID of tree 1
bit = np.c_[E + 25 + rng.random(4) * 0.3, N + 30 + rng.random(4) * 0.3, 6 + rng.random(4)]   # 4 loose points, same ID
c = crown(E + 22, N + 12, 16, 3.5, 4000)                     # tree 2, on its own
xyz = np.r_[a, b, bit, c]
ids = np.r_[np.full(len(a) + len(b) + len(bit), 1), np.full(len(c), 2)].astype(np.int64)
x, y, h = xyz[:, 0], xyz[:, 1], xyz[:, 2]
cloud = types.SimpleNamespace(x=x, y=y, hag=h)

# --- fault 2: one ID, several clusters
before = {r["tree_id"]: r for r in T.tree_table(cloud, ids)}
assert before[1]["crown_width_max_m"] > 40, before[1]                       # measured across the gap
out, new, parent = T.split_clusters(x, y, ids)
assert len(new) == 2 and set(parent.tolist()) == {1}, (new, parent)
assert (out[:len(a)] == 1).all(), "the largest cluster keeps the ID"
assert (out[ids == 2] == 2).all(), "a tree in one piece is not touched"
assert len(T.split_clusters(x, y, out)[1]) == 0, "nothing left to split"

clean, kept, left_out = tree_info.one_cluster_per_tree(x, y, ids)
assert (kept, left_out) == (1, 1), (kept, left_out)                           # the far tree is kept, the loose bit is not
assert (clean[len(a) + len(b):len(a) + len(b) + len(bit)] == 0).all()
after = {r["tree_id"]: r for r in T.tree_table(cloud, clean)}
assert len(after) == 3, len(after)
assert after[1]["crown_width_max_m"] < 10 and after[1]["crown_volume_m3"] < 0.5 * before[1]["crown_volume_m3"], after[1]

merged, log, stats = merge_sat.merge_oversegments(x, y, h, ids)
assert stats["split"] == 2 and stats["dropped_stray"] == 1, stats
assert len(np.unique(merged[merged > 0])) == 3, np.unique(merged)
assert len(T.split_clusters(x, y, merged)[1]) == 0, "every tree of a merged result is one cluster"
same = tree_info.one_cluster_per_tree(x, y, merged)
assert same[1:] == (0, 0) and (same[0] == merged).all(), "tree_info changes nothing in a merged result"

# --- fault 1: large coordinates
local = types.SimpleNamespace(x=x - E, y=y - N, hag=h)
for big, small in zip(T.tree_table(cloud, clean), T.tree_table(local, clean)):
    assert big["canopy_density_pct"] is not None and 5 < big["canopy_density_pct"] <= 100, big
    for key in ("height_m", "crown_area_m2", "crown_diam_m", "crown_width_max_m", "crown_volume_m3",
                "foliage_volume_m3", "crown_base_m", "canopy_density_pct"):
        assert abs(big[key] - small[key]) <= 0.01 * max(abs(small[key]), 1), (key, big[key], small[key])
print("OK")
