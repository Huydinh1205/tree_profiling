"""Prepare labelled plots for benchmarking: convert FOR-instanceV2 .ply, Synthetic Forest .pcd, CedarCypress3D .las
(or FOR-instance .las) to labelled .las, optionally thinned to a target point density (to mimic our DJI L2 data,
about 100 pts/m2) and with z shifted so the lowest ground point is 0.

Output .las has: classification (2 ground, 5 tree, 1 other) and treeID, so process 1 and
process 3 read it directly. (FOR-instance v1 .las keeps its own classes 0-6.)

Usage:
  python prep_labelled.py test_data/*.ply --out plots_full
  python prep_labelled.py test_data/*.ply --out plots_100 --density 100
  python prep_labelled.py SyntheticForest/*.pcd --out syn_100 --density 100
  python prep_labelled.py "CedarCypress3D/*/plot_*/*_ULS_*.las" --out CedarCypress3D/plots_100 --density 100

CedarCypress3D (Zenodo 22168721): 34 circular plots (radius about 12 m) in Japanese cedar / cypress plantations,
each in a 36 x 36 m tile. Trees with their stem in the plot are labelled (treeID); trees outside are class 1 and
not scored. Kokonoe = DJI Zenmuse L2 (same sensor as ours), Saiki = RIEGL VUX-1LR. Only the drone (ULS) files are used.

Synthetic Forest .pcd (Pourdelan et al., Zenodo 17568131): classification 1 ground, 2 wood, 3 leaves,
4 deadwood, 5 rock, 6 stump, 7 bush; `instance` = one ID per tree (wood + leaves). Trees (2, 3) keep their
instance as treeID; ground becomes class 2; deadwood, rocks, stumps and bushes are kept as other points (treeID 0).
The files are ~40 million text lines each: they are read in chunks and, with --density, thinned while reading.
"""
import argparse
from pathlib import Path
import numpy as np, laspy



def expand(paths):
    """Expand wildcards ourselves: Windows cmd/PowerShell pass *.ply to Python unexpanded."""
    import glob
    out = []
    for p in paths:
        hits = sorted(glob.glob(p))
        out += hits if hits else [p]
    return out

def load_pcd(p, density=None, seed=0):
    """Synthetic Forest .pcd (ASCII or binary). Thinned while reading when density is given (scenes are 1 ha)."""
    import pandas as pd
    head, n_head = {}, 0
    with open(p, "rb") as fh:
        for raw in fh:
            n_head += 1; line = raw.decode("ascii", "replace").strip()
            if not line or line.startswith("#"): continue
            k, *v = line.split(); head[k.upper()] = v
            if k.upper() == "DATA": break
        offset = fh.tell() if head["DATA"][0] != "ascii" else None
    fields = head["FIELDS"]; npts = int(head["POINTS"][0])
    need = ["x", "y", "z", "classification", "instance"]
    miss = [f for f in need if f not in fields]
    if miss: raise SystemExit(f"{p}: missing fields {miss}; has {fields}")
    keep_p = 1.0
    if density:                                  # pre-thin to 3x the target assuming >= 1 ha; exact thinning happens later
        keep_p = min(1.0, 3 * density * 10_000 / npts)
    rng = np.random.default_rng(seed); parts = []
    if offset is None:
        reader = pd.read_csv(p, sep=" ", header=None, names=fields, usecols=need, skiprows=n_head, dtype=np.float64,
                             chunksize=4_000_000, engine="c")
        for k, ch in enumerate(reader):
            a = ch[need].to_numpy()
            if keep_p < 1: a = a[rng.random(len(a)) < keep_p]
            parts.append(a); print(f"  {Path(p).name}: read {min((k + 1) * 4_000_000, npts):,} / {npts:,} lines", end="\r", flush=True)
        print()
    else:
        tmap = {("F", "4"): "f4", ("F", "8"): "f8", ("I", "4"): "i4", ("U", "4"): "u4", ("I", "2"): "i2", ("U", "2"): "u2",
                ("U", "1"): "u1", ("I", "1"): "i1"}
        dt = np.dtype([(f, tmap[(t, s)]) for f, t, s in zip(fields, head["TYPE"], head["SIZE"])])
        a = np.fromfile(p, dtype=dt, count=npts, offset=offset)
        if keep_p < 1: a = a[rng.random(len(a)) < keep_p]
        parts.append(np.c_[[a[f].astype(float) for f in need]].T)
    a = np.concatenate(parts)
    x, y, z, c, inst = a.T
    c = c.astype(int); tree = (c == 2) | (c == 3)
    t = np.where(tree, inst.astype(np.int64) + 1, 0)            # +1 so an instance 0 tree is not lost
    cls = np.where(c == 1, 2, np.where(tree, 5, 1)).astype(np.uint8)
    return x, y, z, cls, t, None


def load(path, density=None):
    p = Path(path)
    if p.suffix.lower() == ".pcd":
        return load_pcd(p, density)
    if p.suffix.lower() == ".ply":
        from plyfile import PlyData
        v = PlyData.read(str(p))["vertex"].data
        x, y, z = (np.asarray(v[k], float) for k in "xyz")
        t = np.asarray(v["treeID"]).astype(np.int64); sem = np.asarray(v["semantic_seg"])
        cls = np.where(sem == 1, 2, np.where(t > 0, 5, 1)).astype(np.uint8)
        return x, y, z, cls, t, None
    L = laspy.read(p)
    x, y, z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
    cls = np.asarray(L.classification); t = np.asarray(L.treeID).astype(np.int64)
    if "treeComponent" in L.point_format.extra_dimension_names:
        # CedarCypress3D: 1 outside plot, 2 ground, 3 low vegetation, 5 labelled tree, 7 noise.
        # Noise is dropped; everything that is not ground or a labelled tree becomes "other" (1).
        k = cls != 7
        x, y, z, cls, t = x[k], y[k], z[k], cls[k], t[k]
        cls = np.where(cls == 2, 2, np.where(t > 0, 5, 1)).astype(np.uint8)
    return x, y, z, cls, t, L.header.parse_crs()


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
    for f in expand(a.files):
        x, y, z, cls, t, crs = load(f, a.density)
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
        name = Path(f).stem.replace("_annotated_test", "").replace("_test", "").replace("_panoptic", "").replace("_ULS_", "_")
        write(out / f"{name}.las", x[keep], y[keep], z[keep], cls[keep], t[keep], crs)
        nt = len(np.unique(t[keep][t[keep] > 0]))
        print(f"{name}: {len(x):,} -> {keep.sum():,} pts ({dens:.0f} -> {keep.sum() / area:.0f} pts/m2), {nt} trees")


if __name__ == "__main__":
    main()
