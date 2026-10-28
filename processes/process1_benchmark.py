"""PROCESS 1: score tree segmentation models on a dataset with ground truth.

Input: a labelled point cloud
  * FOR-instance v1 .las  (fields treeID + classification 0-6), e.g. RMIT/test.las
  * FOR-instanceV2 .ply   (fields x y z semantic_seg treeID), e.g. RMIT_RMIT_test_test.ply

The labels are stripped before any model sees the data: models get x, y, z only
and find the ground themselves (CSF). The labels are used only for scoring.

Scores (per model):
  Segmentation  precision / recall / F1 / coverage. A predicted tree is correct
                when it shares >= 50% of its points (IoU >= 0.5) with a labelled
                tree. Same rule as FOR-instance, ForestFormer3D, SegmentAnyTree.
  Detection     tree top within 2 m (horizontal) and 2 m (height) of a labelled
                tree top. Also split into open-topped vs overtopped trees.
Protocols:
  benchmark     ground + labelled tree points only (= FOR-instanceV2 point set,
                comparable with published numbers)
  strict        everything except out-of-plot points (v1 .las only)

Usage examples:
  python process1_benchmark.py data/RMIT/test.las --out results/benchmark_rmit
  python process1_benchmark.py data/RMIT/test.las --models chm_watershed lidr_dalponte --shifts 5
  python process1_benchmark.py data/RMIT/test.las --external sat=sat_output/segmented_pc.laz
"""
import argparse, csv, json, sys, tempfile, time
from pathlib import Path
import numpy as np, laspy
import treeseg as T

CHM_MODELS = {"chm_watershed", "lidr_dalponte", "dalponte_py"}   # results depend on raster alignment
SHIFTS = [(0, 0), (0.25, 0), (0, 0.25), (0.25, 0.25), (0.1, 0.35)]


def load_labelled(path):
    p = Path(path)
    if p.suffix.lower() == ".ply":
        from plyfile import PlyData
        v = PlyData.read(str(p))["vertex"].data
        x, y, z = (np.asarray(v[k], float) for k in "xyz")
        gt = np.asarray(v["treeID"]).astype(np.int64)
        sem = np.asarray(v["semantic_seg"])
        ground = sem == 1
        protocols = {"benchmark": np.ones(len(x), bool)}
        return x, y, z, gt, ground, protocols, None
    L = laspy.read(p)
    if "treeID" not in L.point_format.extra_dimension_names:
        raise SystemExit(f"{p} has no treeID field: this is not a labelled dataset (use process 2)")
    x, y, z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
    gt = np.asarray(L.treeID).astype(np.int64); cls = np.asarray(L.classification)
    ground = cls == 2
    protocols = {"benchmark": ground | (gt > 0), "strict": cls != 3}
    return x, y, z, gt, ground, protocols, L.header.parse_crs()


def drop_outside(pred, keep):
    """Remove predicted trees that lie mostly outside the scored point set."""
    u, inv = np.unique(pred, return_inverse=True)
    frac = np.bincount(inv.ravel(), weights=keep) / np.bincount(inv.ravel())
    return np.where(np.isin(pred, u[(frac < 0.5) & (u > 0)]), 0, pred)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labelled", help="labelled .las (FOR-instance v1) or .ply (FOR-instanceV2)")
    ap.add_argument("--out", default="results/benchmark")
    ap.add_argument("--models", nargs="*", default=list(T.MODELS), choices=list(T.MODELS),
                    help="models to run; give none (just --models) to only score --external results")
    ap.add_argument("--append", action="store_true",
                    help="keep the results already in --out and add/replace only the models of this run")
    ap.add_argument("--external", nargs="*", default=[], metavar="NAME=FILE[:FIELD]",
                    help="score a result made elsewhere, e.g. sat=segmented_pc.laz")
    ap.add_argument("--shifts", type=int, default=5, help="raster alignments for CHM models (1-5, default 5)")
    ap.add_argument("--allow-slow", action="store_true", help="run li2012 even on large clouds")
    a = ap.parse_args()
    out = Path(a.out); (out / "predictions").mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    print(f"Reading labels: {a.labelled}")
    x, y, z, gt, gt_ground, protocols, crs = load_labelled(a.labelled)
    hag_eval = T.height_above_ground(x, y, z, gt_ground)          # for scoring heights only
    n_gt = len(np.unique(gt[gt > 0]))
    print(f"  {len(x):,} points, {n_gt} labelled trees, protocols: {', '.join(protocols)}")

    # --- unlabelled copy: the only thing the models get
    tmp = Path(tempfile.mkdtemp()); unl = tmp / "input_unlabelled.las"
    T.write_xyz_las(unl, x, y, z, crs=crs)
    cloud = T.load_cloud(unl, ground="csf")
    gm = cloud.ground; tp = (gm & gt_ground).sum()
    print(f"  ground found by CSF: precision {tp / max(gm.sum(), 1):.2f}, recall {tp / max(gt_ground.sum(), 1):.2f} vs labels")

    runs, status = [], {}
    def record(model, shift, ids, secs):
        for proto, keep in protocols.items():
            pred = drop_outside(ids, keep) if proto == "benchmark" else ids
            s = T.score(gt, pred, keep, x, y, hag_eval)
            runs.append(dict(model=model, protocol=proto, shift=f"{shift[0]},{shift[1]}", seconds=round(secs, 1), **s))

    for m in a.models:
        shifts = SHIFTS[:max(1, min(a.shifts, 5))] if m in CHM_MODELS else SHIFTS[:1]
        for k, (dx, dy) in enumerate(shifts):
            c = cloud if (dx, dy) == (0, 0) else T.Cloud(cloud.path, cloud.x + dx, cloud.y + dy, cloud.z,
                                                          cloud.hag, cloud.ground, cloud.ground_source)
            try:
                print(f"  running {m} (shift {dx},{dy}) ...", end="", flush=True)
                ids, secs = T.run_model(m, c, allow_slow=a.allow_slow)
                print(f" {len(np.unique(ids[ids > 0]))} trees, {secs:.0f}s")
            except T.ModelUnavailable as e:
                print(f" skipped: {e}"); status[m] = f"skipped: {e}"; break
            except Exception as e:
                print(f" FAILED: {e}"); status[m] = f"failed: {str(e)[:300]}"; break
            status[m] = "ran"; record(m, (dx, dy), ids, secs)
            if (dx, dy) == (0, 0):
                np.save(out / "predictions" / f"{m}.npy", ids.astype(np.int32))
    for spec in a.external:
        name, path, field = T.parse_external(spec)
        try:
            ids, fld, cov = T.read_external(cloud, path, field); ids = T.relabel(ids)
            print(f"  external {name}: field {fld}, {cov:.0%} of points matched, {len(np.unique(ids[ids > 0]))} trees")
            status[f"external:{name}"] = f"ran (file {path}, field {fld})"; record(f"external:{name}", (0, 0), ids, 0)
            np.save(out / "predictions" / f"external_{name}.npy", ids.astype(np.int32))
        except Exception as e:
            print(f"  external {name} FAILED: {e}"); status[f"external:{name}"] = f"failed: {e}"

    if a.append and (out / "scores_all_runs.csv").exists():
        redone = {r["model"] for r in runs}
        old = [r for r in csv.DictReader(open(out / "scores_all_runs.csv")) if r["model"] not in redone]
        for r in old:
            status.setdefault(r["model"], "ran (earlier run)")
        runs = old + runs
    if not runs: sys.exit("No model ran. See messages above.")
    keys = list(runs[0].keys())
    with open(out / "scores_all_runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(runs)

    # --- summary (mean over raster shifts)
    metrics = ["precision", "recall", "f1", "coverage", "det_precision", "det_recall", "det_f1",
               "det_rate_open", "det_rate_overtopped", "seconds"]
    summ = []
    for (m, p) in dict.fromkeys((r["model"], r["protocol"]) for r in runs):
        rs = [r for r in runs if r["model"] == m and r["protocol"] == p]
        row = dict(model=m, protocol=p, runs=len(rs))
        for k in metrics:
            v = np.array([r[k] for r in rs], float); row[k] = float(np.nanmean(v))
            if k == "f1": row["f1_min"], row["f1_max"] = float(v.min()), float(v.max())
        summ.append(row)
    with open(out / "scores_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(summ[0].keys())); w.writeheader(); w.writerows(summ)

    # --- figures (shift 0)
    preds = {m: np.load(out / "predictions" / f"{m.replace(':', '_')}.npy").astype(np.int64)
             for m in status if status[m].startswith("ran") and (out / "predictions" / f"{m.replace(':', '_')}.npy").exists()}
    keepv = protocols["benchmark"]
    lab = T.model_label
    T.fig_topviews([("Height above ground", x[keepv], y[keepv], hag_eval[keepv], None),
                    (f"Reference ({n_gt} labelled trees)", x[keepv], y[keepv], hag_eval[keepv], T.relabel(gt[keepv]))] +
                   [(lab(m), x[keepv], y[keepv], hag_eval[keepv], T.relabel(drop_outside(ids, keepv)[keepv])) for m, ids in preds.items()],
                   out / "fig_topview.png")
    T.fig_sideview([("Reference", T.relabel(gt[keepv]))] +
                   [(lab(m), T.relabel(drop_outside(ids, keepv)[keepv])) for m, ids in preds.items()],
                   x[keepv], y[keepv], hag_eval[keepv], out / "fig_sideview.png")

    # --- report
    bench = [r for r in summ if r["protocol"] == "benchmark"]
    first = runs[0]
    rep = [f"# Tree segmentation benchmark\n",
           f"Dataset: `{a.labelled}`, {len(x):,} points, {n_gt} labelled trees. "
           f"{float(first.get('overtopped_share', 0) or 0):.0%} of labelled trees are under another tree's crown.\n",
           f"Run on {time.strftime('%Y-%m-%d %H:%M')}, {time.time() - t_start:.0f} s.\n",
           "## Models used\n",
           T.md_table([dict(name=lab(m), kind=T.MODELS[m]["kind"] if m in T.MODELS else "external result",
                            how=T.MODELS[m]["detail"] if m in T.MODELS else status[m], st=status[m]) for m in status],
                      [("Model", "name"), ("Type", "kind"), ("How it works", "how"), ("Status", "st")]),
           "\n## Results (benchmark protocol: ground + labelled tree points)\n",
           "CHM-based models were run with the raster grid shifted 5 times; mean shown, F1 range in brackets.\n\n",
           T.md_table([dict(r, name=lab(r["model"]), f1s=f"{r['f1']:.2f} ({r['f1_min']:.2f}-{r['f1_max']:.2f})"
                            if r["runs"] > 1 else f"{r['f1']:.2f}") for r in bench],
                      [("Model", "name"), ("Seg. precision", "precision"), ("Seg. recall", "recall"), ("Seg. F1", "f1s"),
                       ("Coverage", "coverage"), ("Top detection F1", "det_f1"), ("Found: open-topped", "det_rate_open"),
                       ("Found: overtopped", "det_rate_overtopped"), ("Time (s)", "seconds")],
                      {k: "{:.2f}" for k in metrics} | {"seconds": "{:.1f}"}),
           "\nPublished on the FOR-instance RMIT test plot, same IoU >= 0.5 rule (context only):\n\n",
           T.md_table([dict(n=n, f=f, l=l) for n, f, l in T.PUBLISHED_RMIT],
                      [("Model", "n"), ("Seg. F1", "f"), ("Availability", "l")], {"f": "{:.3f}"}),
           "\n![top view](fig_topview.png)\n\n![side view](fig_sideview.png)\n",
           "\nFiles: `scores_summary.csv`, `scores_all_runs.csv` (every run and protocol), `predictions/*.npy` "
           "(tree ID per input point).\n"]
    (out / "report.md").write_text("\n".join(part.rstrip("\n") + "\n" for part in rep), encoding="utf-8")
    print(f"\nDone in {time.time() - t_start:.0f}s. Report: {out / 'report.md'}")
    for r in bench:
        print(f"  {lab(r['model']):28s} seg F1 {r['f1']:.2f}  detection F1 {r['det_f1']:.2f}")


if __name__ == "__main__":
    main()
