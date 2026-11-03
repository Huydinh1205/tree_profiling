"""Fix over-segmentation in a SegmentAnyTree result by merging crown pieces that belong to one tree.

SegmentAnyTree was trained mostly on European/temperate forest plots. On broad, clumpy, open-grown crowns
(eucalypts, paddock trees, windbreak rows) it often cuts one crown into several pieces. This script merges
pieces back together with three rules, always into a neighbour the piece actually touches (3D contact):

  1. fragment   piece with a top-view area under --min-area m2
  2. stemless   piece whose lowest points (2nd percentile) are more than --stemless m above ground, i.e. a crown
                piece with no stem of its own
  3. no valley  two touching pieces whose canopy profile between their tops has no real dip
                (dip < max(--dip-min m, --dip-frac x lower top height)) and whose tops are closer than
                --top-dist x taller top height

A merge is only allowed if the merged crown still fits inside a circle of radius
--crown-k x height + --crown-c m around the taller top. Raise --crown-k to merge more, lower it to merge less.

Usage:
  python merge_sat.py "Galaxy16-[Processed Files].laz"            (several files allowed)
  python merge_sat.py result.laz --crown-k 0.35                     (more cautious)

Writes next to each input:
  <name>_merged.laz         same points and attributes (true RGB kept); PredInstance_SAT = merged tree IDs,
                            PredInstance_SAT_raw = original IDs
  <name>_merged_colour.laz  one colour per merged tree, like colour_trees.bat, to look at in CloudCompare
  <name>_merged.png         top view: RGB (if the file has colour), SAT raw, merged
  <name>_merged_log.csv     one row per merge: which piece went into which tree and why

The _merged.laz can go straight into the benchmark (process1 --external sat_merged=<file>) or process2, because
it keeps the PredInstance_SAT field name.
"""
from __future__ import annotations
import argparse, csv, sys, time
from pathlib import Path
import numpy as np
import laspy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import treeseg as ts

FIELDS = ["PredInstance_SAT", "PredInstance", "preds_instance_segmentation", "treeID", "TreeID"]


def merge_oversegments(x, y, h, ids, res=0.5, voxel=0.5, min_contact=5, min_area=4.0, stemless=3.0,
                       dip_frac=0.2, dip_min=1.0, top_dist=0.5, crown_k=0.45, crown_c=2.0):
    """Return (merged ids, log rows). ids: one int per point, 0 = not a tree. h = height above ground."""
    ids = ts.relabel(ids)
    x0, y0 = x.min(), y.min()
    ci = ((x - x0) / res).astype(np.int64); cj = ((y - y0) / res).astype(np.int64)
    H = int(cj.max()) + 2
    hp = np.clip(h, 0, None)
    tree = ids > 0

    # --- 3D contacts between pieces (26-neighbour voxels holding different IDs)
    vi = ((x[tree] - x0) / voxel).astype(np.int64) + 1
    vj = ((y[tree] - y0) / voxel).astype(np.int64) + 1
    vk = (hp[tree] / voxel).astype(np.int64) + 1
    VJ, VK = int(vj.max()) + 2, int(vk.max()) + 2
    key = vi * VJ * VK + vj * VK + vk
    o = np.argsort(key, kind="stable"); uk, first = np.unique(key[o], return_index=True)
    vlab = ids[tree][o][first]
    nmax = int(ids.max()) + 1
    pairs = {}
    for dx in (0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if (dx, dy, dz) <= (0, 0, 0): continue
                off = dx * VJ * VK + dy * VK + dz
                pos = np.searchsorted(uk, uk + off); pos[pos >= len(uk)] = 0
                hit = uk[pos] == uk + off
                a, b = vlab[hit], vlab[pos[hit]]; m = a != b
                a, b = np.minimum(a[m], b[m]), np.maximum(a[m], b[m])
                pk, pc = np.unique(a * nmax + b, return_counts=True)
                for kk, cc in zip(pk.tolist(), pc.tolist()): pairs[kk] = pairs.get(kk, 0) + cc
    contact = {}
    for kk, cc in pairs.items():
        if cc >= min_contact:
            a, b = divmod(kk, nmax); contact.setdefault(a, {})[b] = cc; contact.setdefault(b, {})[a] = cc

    # --- per piece: top, base, top-view raster (cell -> max height)
    order = np.argsort(ids, kind="stable"); sid = ids[order]
    u = np.unique(ids[tree])
    starts = np.searchsorted(sid, u); ends = np.searchsorted(sid, u, side="right")
    info = {}
    for t, s, e in zip(u.tolist(), starts, ends):
        p = order[s:e]; j = p[np.argmax(hp[p])]
        cc = ci[p] * H + cj[p]; oo = np.argsort(hp[p])
        r = dict(zip(cc[oo].tolist(), hp[p][oo].tolist()))
        info[t] = dict(top=float(hp[j]), tx=float(x[j]), ty=float(y[j]), base=float(np.percentile(hp[p], 2)),
                       n=len(p), r=r)

    parent = {t: t for t in info}
    def find(t):
        while parent[t] != t: parent[t] = parent[parent[t]]; t = parent[t]
        return t

    def area(t): return len(info[t]["r"]) * res * res

    def fits(a, b):
        A, B = (info[a], info[b]) if info[a]["top"] >= info[b]["top"] else (info[b], info[a])
        c = np.fromiter(B["r"].keys(), np.int64)
        px = x0 + (c // H + 0.5) * res; py = y0 + (c % H + 0.5) * res
        return float(np.hypot(px - A["tx"], py - A["ty"]).max()) <= crown_k * A["top"] + crown_c

    def dip(a, b):
        """Drop of the two pieces' own canopy below the lower top, along the line between the tops."""
        A, B = info[a], info[b]
        L = float(np.hypot(B["tx"] - A["tx"], B["ty"] - A["ty"])); n = max(int(L / (res / 2)), 2)
        s = np.linspace(0, 1, n)
        qi = ((A["tx"] + (B["tx"] - A["tx"]) * s - x0) / res).astype(np.int64)
        qj = ((A["ty"] + (B["ty"] - A["ty"]) * s - y0) / res).astype(np.int64)
        ra, rb = A["r"], B["r"]; low = np.inf
        for i_, j_ in zip(qi.tolist(), qj.tolist()):
            v = 0.0
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    c = (i_ + di) * H + j_ + dj; v = max(v, ra.get(c, 0.0), rb.get(c, 0.0))
            low = min(low, v)
        return min(A["top"], B["top"]) - low, L

    log = []
    def union(a, b, why):
        if info[b]["top"] > info[a]["top"]: a, b = b, a
        log.append(dict(piece=b, into=a, rule=why, piece_top_m=round(info[b]["top"], 2),
                        piece_area_m2=round(area(b), 1), x=round(info[b]["tx"], 2), y=round(info[b]["ty"], 2)))
        parent[b] = a; A, B = info[a], info[b]
        A["n"] += B["n"]; A["base"] = min(A["base"], B["base"])
        for c, v in B["r"].items():
            if v > A["r"].get(c, -1.0): A["r"][c] = v
        for nb, cc in contact.pop(b, {}).items():
            if nb == a: continue
            contact.setdefault(a, {}); contact[a][nb] = contact[a].get(nb, 0) + cc
            contact[nb].pop(b, None); contact[nb][a] = contact[nb].get(a, 0) + cc
        contact.get(a, {}).pop(b, None)
        return a

    # pass 1: fragments and stemless pieces, smallest first, into the touching neighbour with most contact
    changed = True
    while changed:
        changed = False
        for t in sorted(contact, key=lambda t: info[t]["n"]):
            if find(t) != t or not contact.get(t): continue
            frag = area(t) < min_area
            stem = stemless is not None and info[t]["base"] > stemless
            if not (frag or stem): continue
            for nb, _ in sorted(contact[t].items(), key=lambda kv: -kv[1]):
                if fits(nb, t):
                    union(nb, t, "fragment" if frag else "stemless"); changed = True; break

    # pass 2: no valley between tops; best pair first, re-evaluate pairs touching the merged tree
    cache = {}
    def cand(a, b):
        k = (min(a, b), max(a, b))
        if k not in cache:
            d, L = dip(a, b); lo = min(info[a]["top"], info[b]["top"]); hi = max(info[a]["top"], info[b]["top"])
            ok = d < max(dip_min, dip_frac * lo) and L < top_dist * hi and fits(a, b)
            cache[k] = (d / max(lo, 0.1)) if ok else None
        return cache[k]
    while True:
        best = None
        for a, nbs in contact.items():
            for b in nbs:
                if a < b:
                    c = cand(a, b)
                    if c is not None and (best is None or c < best[0]): best = (c, a, b)
        if best is None: break
        root = union(best[1], best[2], "no valley")
        cache = {k: v for k, v in cache.items() if best[1] not in k and best[2] not in k}

    lut = np.zeros(int(ids.max()) + 1, np.int64)
    for t in info: lut[t] = find(t)
    return ts.relabel(lut[ids]), log


def fast_hag(x, y, z, ground, res=0.5):
    """Height above a 0.5 m DTM (lowest ground point per cell, gaps filled from the nearest ground cell).
    Much faster than treeseg.height_above_ground on 10M+ points, and precise enough for merging."""
    from scipy import ndimage
    x0, y0 = x.min(), y.min()
    i = ((x - x0) / res).astype(np.int64); j = ((y - y0) / res).astype(np.int64)
    W, H = int(i.max()) + 1, int(j.max()) + 1
    dtm = np.full(W * H, np.inf); np.minimum.at(dtm, i[ground] * H + j[ground], z[ground])
    dtm = dtm.reshape(W, H); miss = ~np.isfinite(dtm)
    if miss.any():
        _, (ni, nj) = ndimage.distance_transform_edt(miss, return_indices=True); dtm = dtm[ni, nj]
    return np.clip(z - dtm[i, j], 0, None)


class _Cloud:
    def __init__(self, L, ground_mode):
        self.las = L
        self.x, self.y, self.z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
        self.n = len(self.x)
        cls = np.asarray(L.classification)
        if ground_mode == "file" or (ground_mode == "auto" and (cls == 2).mean() >= 0.05):
            g, self.ground_source = cls == 2, "file class 2"
        else:
            g, self.ground_source = ts.csf_ground(self.x, self.y, self.z), "CSF (computed)"
        self.h = fast_hag(self.x, self.y, self.z, g)


def topview(cloud, L, raw, new, path, res=0.2):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    x, y, h = cloud.x, cloud.y, cloud.h
    ix = ((x - x.min()) / res).astype(np.int64); iy = ((y - y.min()) / res).astype(np.int64)
    W, H = ix.max() + 1, iy.max() + 1
    o = np.argsort(h); top = np.full(W * H, -1, np.int64); top[ix[o] * H + iy[o]] = o
    top = top.reshape(W, H); ok = top >= 0; t = top[ok]
    def lab(ids):
        cm = ts.random_colours(int(ids.max()) + 1, seed=11); im = np.ones((W, H, 3)); l = ids[t]
        im[ok] = cm[l]; g = np.zeros((W, H), np.int64); g[ok] = l
        e = np.zeros((W, H), bool); e[1:] |= g[1:] != g[:-1]; e[:, 1:] |= g[:, 1:] != g[:, :-1]
        im[e & (g > 0)] = 0; return im
    panels = []
    names = set(L.point_format.dimension_names)
    if {"red", "green", "blue"} <= names and np.asarray(L.red).max() > 0:
        rgb = np.ones((W, H, 3)); s = float(np.asarray(L.red).max())
        rgb[ok] = np.c_[np.asarray(L.red)[t], np.asarray(L.green)[t], np.asarray(L.blue)[t]] / s
        panels.append((rgb, "RGB"))
    panels += [(lab(raw), f"SegmentAnyTree raw ({len(np.unique(raw[raw > 0]))} trees)"),
               (lab(new), f"after merge_sat ({len(np.unique(new[new > 0]))} trees)")]
    asp = H / W
    fig, ax = plt.subplots(1, len(panels), figsize=(9 * len(panels), 9 * asp + 0.8))
    for a, (im, tt) in zip(np.atleast_1d(ax), panels):
        a.imshow(np.transpose(im, (1, 0, 2)), origin="lower", extent=[0, W * res, 0, H * res])
        a.set_title(tt, fontsize=14); a.set_xlabel("m"); a.set_ylabel("m")
    plt.tight_layout(); plt.savefig(path, dpi=110); plt.close(fig)


def process(path, a):
    t0 = time.time()
    path = Path(path)
    cloud = _Cloud(laspy.read(path), a.ground)
    L = cloud.las
    names = list(L.point_format.extra_dimension_names) + list(L.point_format.dimension_names)
    field = a.field or next((f for f in FIELDS if f in names), None)
    if field is None:
        raise SystemExit(f"{path.name}: no tree ID field (looked for {FIELDS}). Fields: {names}")
    raw = np.asarray(L[field]).astype(np.int64); raw[raw < 0] = 0
    print(f"{path.name}: {cloud.n:,} points, ground from {cloud.ground_source}, field {field}, "
          f"{len(np.unique(raw[raw > 0]))} trees")
    new, log = merge_oversegments(cloud.x, cloud.y, cloud.h, raw, min_area=a.min_area,
                                  stemless=None if a.stemless <= 0 else a.stemless, dip_frac=a.dip_frac,
                                  dip_min=a.dip_min, top_dist=a.top_dist, crown_k=a.crown_k, crown_c=a.crown_c)
    rules = {r: sum(1 for g in log if g["rule"] == r) for r in ("fragment", "stemless", "no valley")}
    print(f"  merged into {len(np.unique(new[new > 0]))} trees  ({rules})")

    stem = path.stem
    out = path.with_name(stem + "_merged.laz")
    if "PredInstance_SAT_raw" not in names:
        L.add_extra_dim(laspy.ExtraBytesParams(name="PredInstance_SAT_raw", type=np.int32))
    L["PredInstance_SAT_raw"] = raw.astype(np.int32)
    if field in L.point_format.extra_dimension_names or field in L.point_format.dimension_names:
        L[field] = new.astype(np.asarray(L[field]).dtype)
    L.write(out)
    import mosaic_labelled
    mosaic_labelled.colour(argparse.Namespace(files=[str(out)], field=field))
    with open(path.with_name(stem + "_merged_log.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["piece", "into", "rule", "piece_top_m", "piece_area_m2", "x", "y"])
        w.writeheader(); w.writerows(log)
    if not a.no_figure:
        topview(cloud, L, raw, new, path.with_name(stem + "_merged.png"))
    print(f"  wrote {out.name}, {stem}_merged_colour.laz, .png and _log.csv in {time.time() - t0:.0f} s")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--field", help="tree ID field (default: PredInstance_SAT, treeID, ...)")
    ap.add_argument("--ground", default="auto", choices=["auto", "file", "csf"])
    ap.add_argument("--crown-k", type=float, default=0.45, help="max crown radius = k x height + c (default 0.45)")
    ap.add_argument("--crown-c", type=float, default=2.0, help="(default 2.0 m)")
    ap.add_argument("--min-area", type=float, default=4.0, help="pieces smaller than this (m2, top view) are merged")
    ap.add_argument("--stemless", type=float, default=3.0, help="pieces whose base is higher than this (m) are "
                                                                 "merged; 0 turns the rule off")
    ap.add_argument("--dip-frac", type=float, default=0.2)
    ap.add_argument("--dip-min", type=float, default=1.0)
    ap.add_argument("--top-dist", type=float, default=0.5)
    ap.add_argument("--no-figure", action="store_true")
    a = ap.parse_args()
    import glob
    files = []
    for f in a.files:
        p = Path(f)
        if p.is_dir(): files += sorted(p.glob("*.la[sz]")) + sorted(p.glob("*.zip"))
        else: files += [Path(h) for h in sorted(glob.glob(f))] or [p]
    for f in files:
        if f.stem.endswith(("_merged", "_colour", "_merged_colour")): continue
        process(f, a)


if __name__ == "__main__":
    main()
