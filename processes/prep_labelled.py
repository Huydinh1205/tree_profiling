"""Prepare labelled plots for benchmarking: convert FOR-instanceV2 .ply (or FOR-instance .las)
to labelled .las, optionally thinned to a target point density (to mimic our DJI L2 data,
about 50 to 100 pts/m2) and with z shifted so the lowest ground point is 0.

Output .las has: classification (2 ground, 5 tree, 1 other) and treeID, so process 1 and
process 3 read it directly.

Usage:
  python prep_labelled.py test_data/*.ply --out plots_full
  python prep_labelled.py test_data/*.ply --out plots_100 --density 100
"""
import argparse
from pathlib import Path
import numpy as np, laspy


def load(path):
    p = Path(path)
    if p.suffix.lower() == ".ply":
        from plyfile import PlyData
        v = PlyData.read(str(p))["vertex"].data
        x, y, z = (np.asarray(v[k], float) for k in "xyz")
        t = np.asarray(v["treeID"]).astype(np.int64); sem = np.asarray(v["semantic_seg"])
        cls = np.where(sem == 1, 2, np.where(t > 0, 5, 1)).astype(np.uint8)
        return x, y, z, cls, t, None
    L = laspy.read(p)
    return (np.asarray(L.x), np.asarray(L.y), np.asarray(L.z), np.asarray(L.classification),
            np.asarray(L.treeID).astype(np.int64), L.header.parse_crs())


def write(path, x, y, z, cls, t, crs=None):
    hdr = laspy.LasHeader(point_format=6, version="1.4")
    hdr.offsets = [np.floor(x.min()), np.floor(y.min()), np.floor(z.min())]; hdr.scales = [0.001, 0.001, 0.001]
    hdr.add_extra_dim(laspy.ExtraBytesParams(name="treeID", type=np.int32))
    if crs is not None:
        try: hdr.add_crs(crs)
        except Exception: pass
    W = laspy.LasData(hdr); W.x, W.y, W.z = x, y, z
    W.classification = cls.astype(np.uint8); W.treeID = t.astype(np.int32); W.write(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--density", type=float, help="target points per m2 (random thinning); omit = keep all")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--keep-z", action="store_true", help="do not shift z to ground = 0")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    for f in a.files:
        x, y, z, cls, t, crs = load(f)
        area = (x.max() - x.min()) * (y.max() - y.min()); dens = len(x) / area
        keep = np.ones(len(x), bool)
        if a.density and dens > a.density:
            keep = rng.random(len(x)) < a.density / dens
        if (cls == 2).sum() < 50:
            # no terrain in the file (e.g. BlueCat = merged single trees): add a flat ground grid under the
            # lowest points of each 2 m cell so the pipeline can compute height above ground
            print(f"  {Path(f).name}: no ground points, adding a synthetic ground surface")
            cx, cy = np.floor(x / 2).astype(int), np.floor(y / 2).astype(int)
            k = (cx - cx.min()) * (cy.max() - cy.min() + 1) + (cy - cy.min())
            o = np.lexsort((z, k)); first = np.r_[True, k[o][1:] != k[o][:-1]]
            gx, gy, gz = x[o][first], y[o][first], z[o][first]
            gz = np.minimum(gz, np.percentile(gz, 5) + 1.0)        # clip cells whose lowest point is high in a crown
            xs, ys = np.meshgrid(np.arange(x.min(), x.max(), 0.5), np.arange(y.min(), y.max(), 0.5))
            from scipy.interpolate import griddata
            gzz = griddata((gx, gy), gz, (xs.ravel(), ys.ravel()), method="nearest")
            x = np.r_[x, xs.ravel()]; y = np.r_[y, ys.ravel()]; z = np.r_[z, gzz]
            cls = np.r_[cls, np.full(xs.size, 2, np.uint8)]; t = np.r_[t, np.zeros(xs.size, np.int64)]
            keep = np.r_[keep, np.ones(xs.size, bool)]
        if not a.keep_z:
            g = (cls == 2) & keep
            z = z - (np.percentile(z[g], 1) if g.any() else z[keep].min())
        name = Path(f).stem.replace("_annotated_test", "").replace("_test", "").replace("_panoptic", "")
        write(out / f"{name}.las", x[keep], y[keep], z[keep], cls[keep], t[keep], crs)
        nt = len(np.unique(t[keep][t[keep] > 0]))
        print(f"{name}: {len(x):,} -> {keep.sum():,} pts ({dens:.0f} -> {keep.sum() / area:.0f} pts/m2), {nt} trees")


if __name__ == "__main__":
    main()
