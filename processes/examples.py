"""Pictures for the evaluation report (used by aggregate_benchmarks.py):

  plot_views()   one plot, separate labelled images: points by height, reference trees, then one image
                 per model with its trees in their own colour (like process 2's figures_by_model).
  examples()     close-ups of single reference trees that a model got right or wrong: top view and
                 side view, reference on the left, the model on the right. The tree in question is red
                 in both; on the model side, red is the predicted tree that took most of its points.

Needs the labelled plot (.las from prep_labelled.py) and the predictions saved by process 1.
"""
import csv
from pathlib import Path
import numpy as np
import treeseg as T

RED = np.array([0.84, 0.15, 0.16]); GREY = np.array([0.85, 0.85, 0.85])
TYPES = ["ok", "merged", "split", "missed", "boundary"]
WANT = {"ok": 2, "merged": 2, "split": 1, "missed": 1, "boundary": 1}
TXT = {"ok": "correct", "merged": "merged with neighbours", "split": "split into several trees",
       "missed": "missed (points left unassigned)", "boundary": "right tree, wrong outline"}


def _plt():
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    return plt


def load_plot(las_path, pred_dir):
    """x, y, height above ground, reference IDs, benchmark mask, {model: predicted IDs}."""
    from process1_benchmark import load_labelled, drop_outside
    x, y, z, gt, ground, protocols, _ = load_labelled(las_path)
    h = T.height_above_ground(x, y, z, ground); keep = protocols["benchmark"]
    preds = {f.stem: drop_outside(np.load(f).astype(np.int64), keep) for f in sorted(Path(pred_dir).glob("*.npy"))}
    return x, y, h, gt, keep, preds


def _colours(ids, seed=7):
    c = T.random_colours(int(ids.max()) if ids.size and ids.max() > 0 else 1, seed)
    out = c[ids]; out[ids == 0] = GREY
    return out


def plot_views(las_path, pred_dir, out_dir, scores=None, max_points=300_000):
    """Separate images for one plot. scores: {model_key: dict(pred=, matched=, f1=)}. Returns [(png, caption)]."""
    plt = _plt(); out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    x, y, h, gt, keep, preds = load_plot(las_path, pred_dir)
    sel = np.flatnonzero(keep)
    if len(sel) > max_points: sel = np.random.default_rng(0).choice(sel, max_points, replace=False)
    sel = sel[np.argsort(h[sel])]; x0, y0 = x[sel].min(), y[sel].min()
    n_gt = len(np.unique(gt[keep & (gt > 0)]))
    panels = [("1_height", f"Points by height above ground", None),
              ("2_reference", f"Reference: {n_gt} labelled trees", T.relabel(gt[sel]))]
    for k, (m, ids) in enumerate(preds.items(), 3):
        s = (scores or {}).get(m, {}); lab = T.model_label(m)
        t = f"{lab}: {len(np.unique(ids[keep & (ids > 0)]))} trees" + (f", {s['matched']} correct, F1 {s['f1']:.2f}" if s else "")
        panels.append((f"{k}_{m}", t, T.relabel(ids[sel])))
    out = []
    for name, title, ids in panels:
        fig, ax = plt.subplots(figsize=(5.2, 5.2))
        if ids is None:
            sc = ax.scatter(x[sel] - x0, y[sel] - y0, c=h[sel], s=0.4, cmap="viridis", rasterized=True)
            fig.colorbar(sc, ax=ax, shrink=0.7, label="m")
        else:
            ax.scatter(x[sel] - x0, y[sel] - y0, c=_colours(ids), s=0.4, rasterized=True)
        ax.set_title(title, fontsize=9); ax.set_aspect("equal"); ax.set_xlabel("m"); ax.tick_params(labelsize=7)
        fig.tight_layout(); p = out_dir / f"{name}.png"; fig.savefig(p, dpi=100); plt.close(fig)
        out.append((p, title))
    return out


def pick(rows_by_plot, per_type=None, seed=0, skip_regions=()):
    """rows_by_plot: {plot: [errors_<model>.csv rows]}. Returns [(plot, row)] for each wanted type."""
    per_type = per_type or WANT; rng = np.random.default_rng(seed); chosen = []
    pool = [(p, r) for p, rs in rows_by_plot.items() if not any(p.startswith(s) for s in skip_regions) for r in rs]
    for t, n in per_type.items():
        cand = [(p, r) for p, r in pool if r["status"] == t and int(r["points"]) >= 150 and float(r["height_m"]) >= 4
                and (t != "ok" or float(r["iou"]) >= 0.6)]
        if len(cand) < n: cand = [(p, r) for p, r in pool if r["status"] == t]
        if cand:
            for i in rng.choice(len(cand), min(n, len(cand)), replace=False): chosen.append(cand[i])
    return chosen


def example(x, y, h, gt, keep, pred, row, model_label, path):
    """2 x 2 close-up of one reference tree: top and side view, reference vs model."""
    plt = _plt()
    g = int(row["ref_tree"]); p = int(row["pred_tree"])
    mg = keep & (gt == g); mp = keep & (pred == p) if p > 0 else np.zeros_like(mg)
    pts = mg | (mp if row["status"] == "merged" else mg)
    cx, cy = np.median(x[mg]), np.median(y[mg])
    r = max(5.0, np.hypot(x[pts] - cx, y[pts] - cy).max() + 2.5)
    win = keep & (np.abs(x - cx) < r) & (np.abs(y - cy) < r)
    idx = np.flatnonzero(win); idx = idx[np.argsort(h[idx])]
    other = T.random_colours(4096, 3)
    def colour(ids, focal):
        c = np.where((ids > 0)[:, None], other[ids % 4096] * 0.55 + 0.4, GREY)
        c[ids == focal] = RED
        return c
    cref = colour(gt[idx], g); cmod = colour(pred[idx], p) if p > 0 else colour(pred[idx], -1)
    # outline of the reference tree (2D hull), drawn on both top views
    hull = None
    try:
        from scipy.spatial import ConvexHull
        q = np.c_[x[mg] - cx, y[mg] - cy]; hv = ConvexHull(q).vertices; hull = np.r_[q[hv], q[hv[:1]]]
    except Exception:
        pass
    fig, axs = plt.subplots(2, 2, figsize=(8, 7.2), gridspec_kw=dict(height_ratios=[1, 0.75]))
    for col, (c, focal_mask, t) in enumerate([(cref, gt[idx] == g, f"Reference tree {g} ({float(row['height_m']):.1f} m)"),
                                              (cmod, (pred[idx] == p) if p > 0 else np.zeros(len(idx), bool),
                                               f"{model_label}: {TXT[row['status']]}")]):
        o = np.r_[np.flatnonzero(~focal_mask), np.flatnonzero(focal_mask)]      # the red tree is drawn last, on top
        ax = axs[0, col]; ax.scatter(x[idx][o] - cx, y[idx][o] - cy, c=c[o], s=2, rasterized=True)
        if hull is not None: ax.plot(hull[:, 0], hull[:, 1], "k--", lw=1.1)
        ax.set_aspect("equal"); ax.set_xlim(-r, r); ax.set_ylim(-r, r); ax.set_title(t, fontsize=9); ax.tick_params(labelsize=7)
        o2 = np.argsort(y[idx])[::-1]; o2 = np.r_[o2[~focal_mask[o2]], o2[focal_mask[o2]]]   # side view: far first, red last
        ax = axs[1, col]; ax.scatter(x[idx][o2] - cx, h[idx][o2], c=c[o2], s=2, rasterized=True)
        ax.set_xlim(-r, r); ax.set_ylim(0, max(h[idx].max() + 1, 5)); ax.set_xlabel("m"); ax.tick_params(labelsize=7)
    axs[1, 0].set_ylabel("height (m)")
    extra = []
    if row["status"] == "merged" and row.get("merged_with"): extra.append(f"also holds reference trees {row['merged_with'].replace(';', ', ')}")
    if row["status"] == "split": extra.append(f"spread over {row['n_pred_pieces']} predicted trees")
    if row["status"] == "missed": extra.append(f"{float(row['share_unassigned']):.0%} of its points unassigned")
    extra.append(f"IoU {float(row['iou']):.2f}" + ("  |  under another crown" if int(row["overtopped"]) else "")
                 + "  |  dashed line = outline of the reference tree")
    fig.suptitle("  |  ".join(extra), fontsize=8, y=0.995)
    fig.tight_layout(); fig.savefig(path, dpi=100); plt.close(fig)


def examples(plot_dirs, labelled_dir, out_dir, models=None, per_type=None, skip_regions=("BlueCat", "NIBIO_MLS")):
    """Returns {model_key: [(png, caption)]}."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    plot_dirs = [Path(d) for d in plot_dirs]
    keys = models or sorted({f.stem[len("errors_"):] for d in plot_dirs for f in (d / "errors").glob("errors_*.csv")})
    res, cache = {}, {}
    for m in keys:
        rows = {d.name: list(csv.DictReader(open(d / "errors" / f"errors_{m}.csv"))) for d in plot_dirs
                if (d / "errors" / f"errors_{m}.csv").exists()}
        res[m] = []
        for k, (plot, row) in enumerate(pick(rows, per_type, skip_regions=skip_regions)):
            dirs = labelled_dir if isinstance(labelled_dir, (list, tuple)) else [labelled_dir]
            las = next((q for d in dirs for q in [Path(d) / f"{plot}.las", Path(d) / f"{plot}.laz"] if q.exists()), None)
            if las is None: continue
            if plot not in cache:
                if len(cache) > 6: cache.pop(next(iter(cache)))
                cache[plot] = load_plot(las, next(d for d in plot_dirs if d.name == plot) / "predictions")
            x, y, h, gt, keep, preds = cache[plot]
            if m not in preds: continue
            png = out_dir / f"{m}_{k:02d}_{row['status']}.png"
            example(x, y, h, gt, keep, preds[m], row, T.model_label(m), png)
            res[m].append((png, f"{TXT[row['status']].capitalize()}: plot {plot}, reference tree {row['ref_tree']}"))
    return res
