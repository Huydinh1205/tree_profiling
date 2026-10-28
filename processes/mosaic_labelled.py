"""Put many labelled plots side by side in ONE point cloud (a mosaic), so a model that is run
elsewhere (SegmentAnyTree on usegalaxy.eu, Docker on the company machine) needs one upload
and one run. Then split its result back into one file per plot for scoring.

  build:  python mosaic_labelled.py build plots_100/*.las --out mosaic_100
          -> mosaic_100/mosaic_unlabelled.laz   (upload this; x y z only)
             mosaic_100/mosaic_labelled.las     (same points + treeID, for reference)
             mosaic_100/index.csv               (where each plot sits in the mosaic)
  split:  python mosaic_labelled.py split mosaic_100/index.csv segmented_pc.laz --out mosaic_100/sat_by_plot
          -> one <plot>.laz per plot, in the plot's own coordinates, with the model's tree IDs,
             ready for:  process1_benchmark.py plots_100/<plot>.las --external sat=mosaic_100/sat_by_plot/<plot>.laz

Plots are placed on a grid with a 30 m gap and their z is kept as is (prep_labelled.py already
puts the ground near 0).
"""
import argparse, csv, math
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

def build(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    plots = []
    for f in expand(a.files):
        L = laspy.read(f); x, y = np.asarray(L.x), np.asarray(L.y)
        plots.append(dict(name=Path(f).stem, file=str(Path(f).resolve()), L=L, w=x.max() - x.min(), h=y.max() - y.min()))
    n = len(plots); cols = math.ceil(math.sqrt(n)); gap = a.gap
    colw = [0] * cols; rowh = [0] * math.ceil(n / cols)
    for i, p in enumerate(plots):
        colw[i % cols] = max(colw[i % cols], p["w"]); rowh[i // cols] = max(rowh[i // cols], p["h"])
    X, Y, Z, C, T, idx = [], [], [], [], [], []
    offset = 0; rows = []
    for i, p in enumerate(plots):
        L = p["L"]; x, y, z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
        ox = sum(colw[:i % cols]) + gap * (i % cols) - x.min()
        oy = sum(rowh[:i // cols]) + gap * (i // cols) - y.min()
        t = np.asarray(L.treeID).astype(np.int64); t = np.where(t > 0, t + offset, 0)
        rows.append(dict(plot=p["name"], source=p["file"], dx=round(ox, 3), dy=round(oy, 3),
                         xmin=round(x.min() + ox, 3), ymin=round(y.min() + oy, 3), xmax=round(x.max() + ox, 3), ymax=round(y.max() + oy, 3),
                         id_offset=offset, trees=len(np.unique(t[t > 0])), points=len(x)))
        offset = int(max(offset, t.max()))
        X.append(x + ox); Y.append(y + oy); Z.append(z); C.append(np.asarray(L.classification)); T.append(t); idx.append(np.full(len(x), i))
    x, y, z, c, t = (np.concatenate(k) for k in (X, Y, Z, C, T))
    with open(out / "index.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    for name, labelled in [("mosaic_labelled.las", True), ("mosaic_unlabelled.laz", False)]:
        hdr = laspy.LasHeader(point_format=6, version="1.4")
        hdr.offsets = [0, 0, np.floor(z.min())]; hdr.scales = [0.001, 0.001, 0.001]
        if labelled: hdr.add_extra_dim(laspy.ExtraBytesParams(name="treeID", type=np.int32))
        W = laspy.LasData(hdr); W.x, W.y, W.z = x, y, z
        if labelled: W.classification = c.astype(np.uint8); W.treeID = t.astype(np.int32)
        W.write(out / name)
    print(f"{n} plots, {len(x):,} points, {len(np.unique(t[t > 0]))} trees -> {out}/mosaic_unlabelled.laz (upload this)")


def split(a):
    rows = list(csv.DictReader(open(a.index)))
    L = laspy.read(a.result)
    names = list(L.point_format.extra_dimension_names) + list(L.point_format.dimension_names)
    field = a.field or next((f for f in ["PredInstance_SAT", "PredInstance", "treeID", "instance"] if f in names), None)
    if field is None: raise SystemExit(f"no tree ID field in {a.result}: {names}")
    x, y, z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z); ids = np.asarray(L[field]).astype(np.int64)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for r in rows:
        m = ((x >= float(r["xmin"]) - 0.5) & (x <= float(r["xmax"]) + 0.5) & (y >= float(r["ymin"]) - 0.5) & (y <= float(r["ymax"]) + 0.5))
        hdr = laspy.LasHeader(point_format=6, version="1.4")
        px, py = x[m] - float(r["dx"]), y[m] - float(r["dy"])
        hdr.offsets = [np.floor(px.min()), np.floor(py.min()), np.floor(z[m].min())]; hdr.scales = [0.001, 0.001, 0.001]
        hdr.add_extra_dim(laspy.ExtraBytesParams(name="treeID", type=np.int32))
        W = laspy.LasData(hdr); W.x, W.y, W.z = px, py, z[m]; W.treeID = ids[m].astype(np.int32)
        W.write(out / f"{r['plot']}.laz")
        print(f"{r['plot']}: {m.sum():,} points, {len(np.unique(ids[m][ids[m] > 0]))} trees")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("files", nargs="+"); b.add_argument("--out", required=True); b.add_argument("--gap", type=float, default=30)
    s = sub.add_parser("split"); s.add_argument("index"); s.add_argument("result"); s.add_argument("--out", required=True); s.add_argument("--field")
    a = ap.parse_args()
    build(a) if a.cmd == "build" else split(a)


if __name__ == "__main__":
    main()
