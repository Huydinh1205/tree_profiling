"""Fix over-segmentation in a SegmentAnyTree result by merging crown pieces that belong to one tree.

SegmentAnyTree was trained mostly on European/temperate forest plots. On broad, clumpy, open-grown crowns
(eucalypts, paddock trees, windbreak rows) it often cuts one crown into several pieces. This script merges
pieces back together, always into a neighbour the piece actually touches (3D contact):

  1. no own top  right next to the piece's highest point (within --apex-r m) the neighbour is as high or
                 higher (down to --apex-dz m lower), and the neighbour does not cover that top. Pieces of one
                 crown look like that; two real trees each have their own top, and a lower tree under a
                 neighbour's crown is left alone. Only a piece at most --max-piece-frac (0.4) of the neighbour's
                 crown area (top view) is merged this way: a piece as big as a crown is a tree of its own.
  2. fragment    a piece without a stem smaller than --min-area m2 (top view).
  3. low trees   after merging, a tree whose top is lower than --min-top m (3) is dropped (set to 0): these are
                 shrubs and loose bits that never match a labelled tree. --min-top 0 keeps them.

Two pieces that both have a stem (points within 2 m of the ground) are never merged. DJI L2 over dense canopy
often shows no stems at all, so the rules do not rely on stems being visible. A merge is only allowed if the
merged crown still fits inside a circle of radius --crown-k x height + --crown-c m around the taller top.

Checked 4/11/2026 on labelled plots (RMIT eucalypt, CedarCypress3D Kokonoe = DJI L2, Saiki): on correct trees
it leaves F1 at 0.90 to 1.00 (the first version of this script, with a "stemless" and a "no valley" rule,
dropped them to 0.00 to 0.51 and FOR-instanceV2 from 0.78 to 0.15); crowns cut into 3 pieces on purpose
are put back together (F1 0.2 to 0.4 -> 0.79 to 0.96).

5/11/2026, --max-piece-frac 0.3 added after scoring every merge on the full-density benchmarks: most merges that
destroyed a correct tree were "no own top" merges of a big piece into a neighbour (dense plantations: Saiki,
Kokonoe). F1 (IoU >= 0.5) of SegmentAnyTree raw -> merge without the limit -> merge with 0.3:
  CedarCypress3D Saiki (16 plots)    0.859 -> 0.843 -> 0.881
  CedarCypress3D Kokonoe (18, DJI L2) 0.343 -> 0.323 -> 0.346
  FOR-instanceV2 (28 plots)          0.778 -> 0.810 -> 0.814
SegmentAnyTree's main error on Kokonoe is the opposite (several trees in one, 56% of the trees); merging cannot
fix that.

5/11/2026, defaults tuned on the same 62 plots (grid over max-piece-frac 0.2 to 0.8, apex-r 0.3 to 1.0, apex-dz,
min-area, crown-k, and a filter on small / low trees). Best: --max-piece-frac 0.4 --apex-r 0.6 --min-top 3 (the
others stay). F1: SegmentAnyTree raw -> old merge (no limit) -> these defaults:
  CedarCypress3D all (34 plots)      0.647 -> 0.625 -> 0.660
  CedarCypress3D Saiki               0.859 -> 0.843 -> 0.883
  CedarCypress3D Kokonoe             0.343 -> 0.323 -> 0.346
  FOR-instanceV2 (28 plots)          0.778 -> 0.810 -> 0.820
min-area 2 and crown-k 0.35 / 0.6 were worse or equal; max-piece-frac 0.5 or more and apex-r 1.0 were worse.

Usage:
  python merge_sat.py "Galaxy16-[Processed Files].laz"            (several files allowed)
  python merge_sat.py result.laz --apex-r 0.5                       (merges more)

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


def merge_oversegments(x, y, h, ids, res=0.25, voxel=0.5, min_contact=5, min_area=4.0, apex_r=0.6, apex_dz=0.3,
                       stem_h=2.0, stem_pts=3, crown_k=0.45, crown_c=2.0, max_piece_frac=0.4, min_top=3.0):
    """Return (merged ids, log rows, stats). ids: one int per point, 0 = not a tree. h = height above ground.

    A piece is only merged into a neighbour it touches in 3D, never so that the merged crown leaves the circle
    crown_k x height + crown_c around the taller top, and never when both pieces have a stem (at least stem_pts
    points lower than stem_h m above ground). Rules:
      no own top  right next to the piece's highest point (within apex_r m) the neighbour is as high or higher
                  (down to apex_dz m lower), and the neighbour's crown does not cover that top. A crown cut into
                  pieces gives pieces like that; two real trees each have their own top. A tree whose top is
                  under a neighbour's crown is left alone (it is a lower tree, not a piece). Only for a piece of
                  at most max_piece_frac x the neighbour's crown area (top view).
      fragment    a piece without a stem, smaller than min_area m2 (top view).
    After merging, a tree whose top is lower than min_top m is set to 0 (stats["dropped_low"] counts them)."""
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

    # --- per piece: top, stem, top-view raster (cell -> max height)
    order = np.argsort(ids, kind="stable"); sid = ids[order]
    u = np.unique(ids[tree])
    starts = np.searchsorted(sid, u); ends = np.searchsorted(sid, u, side="right")
    info = {}
    for t, s, e in zip(u.tolist(), starts, ends):
        p = order[s:e]; j = p[np.argmax(hp[p])]
        cc = ci[p] * H + cj[p]; oo = np.argsort(hp[p])
        r = dict(zip(cc[oo].tolist(), hp[p][oo].tolist()))
        info[t] = dict(top=float(hp[j]), tx=float(x[j]), ty=float(y[j]), n=len(p), r=r,
                       stem=bool((hp[p] < stem_h).sum() >= stem_pts))
    tall = [t for t in info if info[t]["top"] > 5]
    share = float(np.mean([info[t]["stem"] for t in tall])) if tall else 0.0

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

    kr = max(1, int(round(apex_r / res)))
    disk = [(di, dj) for di in range(-kr, kr + 1) for dj in range(-kr, kr + 1) if di * di + dj * dj <= kr * kr]
    def near_top(t, nb):
        """(highest canopy of nb within apex_r of t's top, canopy of nb right above t's top)"""
        A = info[t]; i0 = int((A["tx"] - x0) / res); j0 = int((A["ty"] - y0) / res); rb = info[nb]["r"]; v = -1.0
        for di, dj in disk:
            v = max(v, rb.get((i0 + di) * H + j0 + dj, -1.0))
        return v, rb.get(i0 * H + j0, -1.0)

    log = []
    def union(a, b, why):
        if info[b]["top"] > info[a]["top"]: a, b = b, a
        log.append(dict(piece=b, into=a, rule=why, piece_top_m=round(info[b]["top"], 2),
                        piece_area_m2=round(area(b), 1), x=round(info[b]["tx"], 2), y=round(info[b]["ty"], 2)))
        parent[b] = a; A, B = info[a], info[b]
        A["n"] += B["n"]; A["stem"] = A["stem"] or B["stem"]
        for c, v in B["r"].items():
            if v > A["r"].get(c, -1.0): A["r"][c] = v
        for nb, cc in contact.pop(b, {}).items():
            if nb == a: continue
            contact.setdefault(a, {}); contact[a][nb] = contact[a].get(nb, 0) + cc
            contact[nb].pop(b, None); contact[nb][a] = contact[nb].get(a, 0) + cc
        contact.get(a, {}).pop(b, None)
        return a

    def both_stems(a, b): return info[a]["stem"] and info[b]["stem"]

    changed = True
    while changed:
        changed = False
        # no own top: lowest pieces first, into the neighbour that is highest right next to their top
        for t in sorted(contact, key=lambda t: info[t]["top"]):
            if find(t) != t or not contact.get(t): continue
            best = None
            for nb in contact[t]:
                if both_stems(t, nb): continue
                v, above = near_top(t, nb)
                if above > info[t]["top"] + apex_dz: continue       # t's top is under nb's crown: a tree below
                if v >= info[t]["top"] - apex_dz and (best is None or v > best[0]): best = (v, nb)
            if best and fits(best[1], t) and area(t) <= max_piece_frac * area(best[1]):
                union(best[1], t, "no own top"); changed = True
        # small fragments without a stem, smallest first, into the touching neighbour with most contact
        for t in sorted(contact, key=lambda t: info[t]["n"]):
            if find(t) != t or not contact.get(t) or info[t]["stem"] or area(t) >= min_area: continue
            for nb, _ in sorted(contact[t].items(), key=lambda kv: -kv[1]):
                if fits(nb, t):
                    union(nb, t, "fragment"); changed = True; break

    lut = np.zeros(int(ids.max()) + 1, np.int64)
    low = 0
    for t in info:
        r = find(t); lut[t] = r
        if info[r]["top"] < min_top: lut[t] = 0; low += r == t
    return ts.relabel(lut[ids]), log, dict(stem_share=share, dropped_low=int(low))


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
    new, log, st = merge_oversegments(cloud.x, cloud.y, cloud.h, raw, min_area=a.min_area, apex_r=a.apex_r,
                                      apex_dz=a.apex_dz, crown_k=a.crown_k, crown_c=a.crown_c,
                                      max_piece_frac=a.max_piece_frac, min_top=a.min_top)
    rules = {r: sum(1 for g in log if g["rule"] == r) for r in ("no own top", "fragment")}
    print(f"  merged into {len(np.unique(new[new > 0]))} trees  ({rules}; "
          f"{st['stem_share']:.0%} of the pieces taller than 5 m show a stem; {st['dropped_low']} trees lower "
          f"than {a.min_top:g} m dropped)")

    stem = path.stem
    od = Path(a.out_dir) if getattr(a, "out_dir", None) else None
    out = od / f"{stem}.laz" if od else path.with_name(stem + "_merged.laz")
    if "PredInstance_SAT_raw" not in names:
        L.add_extra_dim(laspy.ExtraBytesParams(name="PredInstance_SAT_raw", type=np.int32))
    L["PredInstance_SAT_raw"] = raw.astype(np.int32)
    if field in L.point_format.extra_dimension_names or field in L.point_format.dimension_names:
        L[field] = new.astype(np.asarray(L[field]).dtype)
    L.write(out)
    if od is None:                                    # the coloured copy is for looking at, not for the benchmark
        import mosaic_labelled
        mosaic_labelled.colour(argparse.Namespace(files=[str(out)], field=field))
    with open(od / f"{stem}_merged_log.csv" if od else path.with_name(stem + "_merged_log.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["piece", "into", "rule", "piece_top_m", "piece_area_m2", "x", "y"])
        w.writeheader(); w.writerows(log)
    if not a.no_figure:
        topview(cloud, L, raw, new, od / f"{stem}_merged.png" if od else path.with_name(stem + "_merged.png"))
    print(f"  wrote {out.name}, {stem}_merged_colour.laz, .png and _log.csv in {time.time() - t0:.0f} s")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--field", help="tree ID field (default: PredInstance_SAT, treeID, ...)")
    ap.add_argument("--ground", default="auto", choices=["auto", "file", "csf"])
    ap.add_argument("--crown-k", type=float, default=0.45, help="max crown radius = k x height + c (default 0.45)")
    ap.add_argument("--crown-c", type=float, default=2.0, help="(default 2.0 m)")
    ap.add_argument("--min-area", type=float, default=4.0, help="stemless pieces smaller than this (m2, top view) "
                                                                 "are merged (default 4)")
    ap.add_argument("--apex-r", type=float, default=0.6, help="'no own top' looks this far around a piece's top "
                                                               "(m, default 0.6; larger merges more)")
    ap.add_argument("--apex-dz", type=float, default=0.3, help="... for a neighbour at most this much lower "
                                                                "(m, default 0.3; larger merges more)")
    ap.add_argument("--max-piece-frac", type=float, default=0.4, help="'no own top' only merges a piece of at most "
                    "this fraction of the neighbour's crown area (default 0.4; larger merges more, 1000 = no limit)")
    ap.add_argument("--min-top", type=float, default=3.0, help="drop trees whose top is lower than this after "
                    "merging (m, default 3; 0 keeps every tree)")
    ap.add_argument("--no-figure", action="store_true")
    ap.add_argument("--out-dir", help="write <out-dir>/<name>.laz (same name as the input) instead of <name>_merged.laz "
                    "next to the input; used by the benchmark to merge every plot on its own")
    ap.add_argument("--skip-done", action="store_true", help="with --out-dir: skip a file whose merged copy is newer "
                    "than it and was made by this exact merge_sat.py (MD5 kept in <out-dir>/merge_sat.version)")
    a = ap.parse_args()
    import glob
    files = []
    for f in a.files:
        p = Path(f)
        if p.is_dir(): files += sorted(p.glob("*.la[sz]")) + sorted(p.glob("*.zip"))
        else: files += [Path(h) for h in sorted(glob.glob(f))] or [p]
    import hashlib
    md5 = hashlib.md5(Path(__file__).read_bytes()).hexdigest()
    od = Path(a.out_dir) if a.out_dir else None
    stamp = od / "merge_sat.version" if od else None
    if od:
        od.mkdir(parents=True, exist_ok=True)
        same_code = stamp.exists() and stamp.read_text().strip() == md5
        if stamp.exists() and not same_code:
            stamp.unlink()                            # code changed: every plot is merged again
    for f in files:
        if f.stem.endswith(("_merged", "_colour", "_merged_colour")): continue
        if od and a.skip_done and same_code and (od / f"{f.stem}.laz").exists() \
                and (od / f"{f.stem}.laz").stat().st_mtime >= f.stat().st_mtime:
            continue
        process(f, a)
    if od:
        stamp.write_text(md5)
        print(f"merge_sat.py MD5 {md5}")


if __name__ == "__main__":
    main()
