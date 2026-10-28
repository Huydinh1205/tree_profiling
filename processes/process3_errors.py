"""PROCESS 3: where does the segmentation go wrong?

Takes the labelled file and the predictions saved by process 1
(<benchmark out>/predictions/<model>.npy, one tree ID per point) and sorts every
labelled tree and every predicted tree into an error type, with its location.

Labelled (reference) trees:
  ok        matched one-to-one with a predicted tree (IoU >= 0.5)
  merged    its points went into a predicted tree that also holds another reference tree
            (two or more trees found as one)
  split     its points were spread over several predicted trees (one tree found as several)
  missed    half or more of its points were not given to any tree
  boundary  one predicted tree holds most of it, but the crown outline is wrong (IoU < 0.5)

Predicted trees:
  ok           the match of a reference tree
  merge        holds the majority of two or more reference trees
  fragment     a piece of a split reference tree
  ground/noise half or more of its points are not tree points
  boundary     everything else

Writes, per model: errors_<model>.csv (one row per reference tree), predicted_<model>.csv,
                   fig_errors_<model>.png (map of the error types)
and once:          summary.csv, report.md

Usage: python process3_errors.py ../data/RMIT/train_test.laz --bench results/benchmark_rmit_train_test --out results/errors_rmit_train_test
"""
import argparse, csv, time
from pathlib import Path
import numpy as np
import treeseg as T
from process1_benchmark import load_labelled, drop_outside

COL = {"ok": "#2ca02c", "merged": "#d62728", "split": "#ff7f0e", "missed": "#7f7f7f", "boundary": "#9467bd"}


def overlap_table(gt, pred, keep):
    g, p = gt[keep], pred[keep]
    both = (g > 0) & (p > 0)
    pairs, inter = np.unique(np.c_[g[both], p[both]], axis=0, return_counts=True)
    gsz, psz = np.bincount(g), np.bincount(p)
    return pairs, inter, gsz, psz


def classify(gt, pred, keep, x, y, h):
    """Returns per-reference-tree rows and per-predicted-tree rows."""
    matches, _, _, _ = T.match_iou(gt, pred, keep)
    ok_g = {a: (b, iou) for a, b, iou in matches}; ok_p = {b for _, b, _ in matches}
    pairs, inter, gsz, psz = overlap_table(gt, pred, keep)
    g_ids = np.unique(gt[keep & (gt > 0)]); p_ids = np.unique(pred[keep & (pred > 0)])

    # majority predicted tree of each reference tree, and its share
    best_p, best_share, shares = {}, {}, {}
    for gid in g_ids:
        m = pairs[:, 0] == gid
        if not m.any(): best_p[gid], best_share[gid], shares[gid] = 0, 0.0, {}; continue
        s = inter[m] / gsz[gid]; o = np.argsort(-s)
        best_p[gid] = int(pairs[m][o[0], 1]); best_share[gid] = float(s[o[0]])
        shares[gid] = {int(q): float(v) for q, v in zip(pairs[m][:, 1], s)}
    # reference trees that each predicted tree "owns" (holds >= 50% of that tree's points)
    owners = {}
    for gid in g_ids:
        for q, v in shares[gid].items():
            if v >= 0.5: owners.setdefault(q, []).append(int(gid))

    gi, gx, gy, gh = T.tops_of(np.where(keep, gt, 0), x, y, h)
    top = {int(i): (float(a), float(b), float(c)) for i, a, b, c in zip(gi, gx, gy, gh)}
    from scipy.spatial import cKDTree
    kd = cKDTree(np.c_[x, y])

    iou_of = {(int(a), int(b)): float(i / (gsz[a] + psz[b] - i)) for (a, b), i in zip(pairs, inter)}
    rows = []
    for gid in g_ids:
        gid = int(gid); tx, ty, th = top[gid]
        unassigned = 1.0 - sum(shares[gid].values())
        big = [q for q, v in shares[gid].items() if v >= 0.2]
        if gid in ok_g: status = "ok"
        elif unassigned >= 0.5: status = "missed"
        elif best_p[gid] in owners and len(owners[best_p[gid]]) >= 2: status = "merged"
        elif best_share[gid] < 0.5 and len(big) >= 2: status = "split"
        else: status = "boundary"
        over = h[kd.query_ball_point([tx, ty], 0.75)].max() > th + 1.0
        rows.append(dict(ref_tree=gid, status=status, x=round(tx, 2), y=round(ty, 2), height_m=round(th, 1),
                         points=int(gsz[gid]), overtopped=int(over), pred_tree=best_p[gid],
                         share_in_pred=round(best_share[gid], 2), share_unassigned=round(unassigned, 2),
                         n_pred_pieces=len(big), iou=round(iou_of.get((gid, best_p[gid]), 0.0), 2),
                         merged_with=";".join(str(o) for o in owners.get(best_p[gid], []) if o != gid) if status == "merged" else ""))

    pi, px, py, ph = T.tops_of(np.where(keep, pred, 0), x, y, h)
    ptop = {int(i): (float(a), float(b), float(c)) for i, a, b, c in zip(pi, px, py, ph)}
    labelled_share = {}
    for pid in p_ids:
        m = keep & (pred == pid); labelled_share[int(pid)] = float((gt[m] > 0).mean())
    split_pieces = {q for r in rows if r["status"] == "split" for q in shares[r["ref_tree"]] if shares[r["ref_tree"]][q] >= 0.2}
    prows = []
    for pid in p_ids:
        pid = int(pid); tx, ty, th = ptop[pid]
        if pid in ok_p: st = "ok"
        elif len(owners.get(pid, [])) >= 2: st = "merge"
        elif labelled_share[pid] < 0.5: st = "ground/noise"
        elif pid in split_pieces: st = "fragment"
        else: st = "boundary"
        prows.append(dict(pred_tree=pid, status=st, x=round(tx, 2), y=round(ty, 2), height_m=round(th, 1),
                          points=int(psz[pid]), share_tree_points=round(labelled_share[pid], 2),
                          ref_trees_inside=";".join(str(o) for o in owners.get(pid, []))))
    return rows, prows


def fig_errors(x, y, h, gt, pred, keep, rows, label, path):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    sel = np.where(keep)[0]
    if len(sel) > 400_000: sel = np.random.default_rng(0).choice(sel, 400_000, replace=False)
    sel = sel[np.argsort(h[sel])]; x0, y0 = x[keep].min(), y[keep].min()
    fig, axs = plt.subplots(1, 3, figsize=(17, 5.8))
    for ax, (t, ids) in zip(axs[:2], [("Reference trees", T.relabel(gt[sel])), (label, T.relabel(pred[sel]))]):
        col = T.random_colours(int(ids.max()) if ids.max() > 0 else 1)[ids]
        ax.scatter(x[sel] - x0, y[sel] - y0, c=col, s=0.3, rasterized=True); ax.set_title(t, fontsize=10)
    ax = axs[2]
    ax.scatter(x[sel] - x0, y[sel] - y0, c=h[sel], s=0.3, cmap="Greys", rasterized=True)
    for r in rows:
        ax.scatter(r["x"] - x0, r["y"] - y0, s=28 if r["status"] == "ok" else 60, c=COL[r["status"]],
                   marker="o" if r["status"] == "ok" else "X", edgecolors="k", linewidths=0.4, zorder=5)
        if r["status"] != "ok":
            ax.annotate(str(r["ref_tree"]), (r["x"] - x0, r["y"] - y0), fontsize=6, xytext=(3, 3), textcoords="offset points")
    n = {k: sum(r["status"] == k for r in rows) for k in COL}
    ax.legend(handles=[Line2D([], [], marker="o" if k == "ok" else "X", ls="", color=COL[k], markeredgecolor="k",
                              label=f"{k} ({n[k]})") for k in COL], fontsize=8, loc="upper right")
    ax.set_title(f"Reference tree tops by error type: {label}", fontsize=10)
    for ax in axs: ax.set_aspect("equal"); ax.set_xlabel("m"); ax.tick_params(labelsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labelled")
    ap.add_argument("--bench", required=True, help="output folder of process 1 (has predictions/*.npy)")
    ap.add_argument("--out", default="results/errors")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    x, y, z, gt, gt_ground, protocols, _ = load_labelled(a.labelled)
    h = T.height_above_ground(x, y, z, gt_ground); keep = protocols["benchmark"]
    n_gt = len(np.unique(gt[keep & (gt > 0)]))
    preds = sorted(Path(a.bench, "predictions").glob("*.npy"))
    if not preds: raise SystemExit(f"No predictions in {a.bench}/predictions: run process 1 first")

    summary, md = [], [f"# Segmentation errors: {Path(a.labelled).name}\n",
                       f"{n_gt} reference trees. Generated {time.strftime('%Y-%m-%d %H:%M')}.\n"]
    for f in preds:
        m = f.stem; label = T.MODELS[m]["label"] if m in T.MODELS else m
        pred = drop_outside(np.load(f).astype(np.int64), keep)
        rows, prows = classify(gt, pred, keep, x, y, h)
        for name, rs in [(f"errors_{m}.csv", rows), (f"predicted_{m}.csv", prows)]:
            with open(out / name, "w", newline="") as fh:
                w = csv.DictWriter(fh, list(rs[0].keys())); w.writeheader(); w.writerows(rs)
        fig_errors(x, y, h, gt, pred, keep, rows, label, out / f"fig_errors_{m}.png")
        n = {k: sum(r["status"] == k for r in rows) for k in COL}
        npred = {k: sum(r["status"] == k for r in prows) for k in ["ok", "merge", "fragment", "ground/noise", "boundary"]}
        over = [r for r in rows if r["overtopped"]]
        summary.append(dict(model=label, ref_trees=n_gt, pred_trees=len(prows), **{f"ref_{k}": v for k, v in n.items()},
                            **{f"pred_{k.replace('/', '_')}": v for k, v in npred.items()},
                            ok_rate_open=round(np.mean([r["status"] == "ok" for r in rows if not r["overtopped"]]), 2),
                            ok_rate_overtopped=round(np.mean([r["status"] == "ok" for r in over]), 2) if over else float("nan")))
        print(f"{label}: {n_gt} reference vs {len(prows)} predicted; " + ", ".join(f"{k} {v}" for k, v in n.items()))
        md += [f"\n## {label}\n", f"Predicted {len(prows)} trees for {n_gt} reference trees.\n",
               "\nReference trees:\n\n", T.md_table([dict(k=k, v=v, p=f"{v / n_gt:.0%}") for k, v in n.items()],
                                                     [("Error type", "k"), ("Trees", "v"), ("Share", "p")]),
               "\nPredicted trees:\n\n", T.md_table([dict(k=k, v=v) for k, v in npred.items()], [("Type", "k"), ("Trees", "v")]),
               f"\n![errors](fig_errors_{m}.png)\n",
               "\nWorst cases (largest reference trees not found):\n\n",
               T.md_table(sorted([r for r in rows if r["status"] != "ok"], key=lambda r: -r["points"])[:10],
                          [("Ref tree", "ref_tree"), ("Error", "status"), ("x", "x"), ("y", "y"), ("Height (m)", "height_m"),
                           ("Points", "points"), ("Under other crown", "overtopped"), ("Share in best pred.", "share_in_pred"),
                           ("Merged with", "merged_with")])]
    with open(out / "summary.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, list(summary[0].keys())); w.writeheader(); w.writerows(summary)
    md.insert(2, "\n## Summary\n\n" + T.md_table(summary, [("Model", "model"), ("Reference trees", "ref_trees"), ("Predicted trees", "pred_trees"),
                                                           ("ok", "ref_ok"), ("merged", "ref_merged"), ("split", "ref_split"),
                                                           ("missed", "ref_missed"), ("boundary", "ref_boundary"),
                                                           ("False (ground/noise)", "pred_ground_noise"),
                                                           ("ok rate: open-topped", "ok_rate_open"), ("ok rate: under crown", "ok_rate_overtopped")]) +
              "\nHow to read: `merged` = several reference trees came out as one; `split` = one reference tree came out as several; "
              "`missed` = the tree's points were left unassigned; `boundary` = right tree, wrong outline (IoU < 0.5). "
              "Coordinates of every tree are in `errors_<model>.csv` (projected CRS of the input file).\n")
    (out / "report.md").write_text("\n".join(p.rstrip("\n") + "\n" for p in md), encoding="utf-8")
    print(f"Report: {out / 'report.md'}")


if __name__ == "__main__":
    main()
