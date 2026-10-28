"""Pool process 1 (and process 3) results from many plots into one evaluation report:
per model, per region and overall, as summary.md, summary.csv and a standalone report.html.

Pooled precision / recall / F1 are computed from the summed counts (matched, predicted,
reference trees) of the shift-0 run of each plot, benchmark protocol, so big plots weigh more,
like the published FOR-instance numbers. The mean of per-plot F1 is given too.
If a plot folder has errors/summary.csv (process 3), error types are pooled as well.

Usage: python aggregate_benchmarks.py results/v2_100/* --out results/v2_100/summary.md
Region = text before the first "_" of the plot folder name (RMIT, NIBIO2, SCION, ...).
"""
import argparse, csv
from collections import defaultdict
from pathlib import Path
import numpy as np
import treeseg as T

REGION_INFO = {
    "RMIT": "Australia, dry eucalypt forest (Riegl miniVUX ULS)",
    "CULS": "Czechia, conifer plantation (ULS)",
    "NIBIO": "Norway, boreal conifer (ULS)",
    "NIBIO2": "Norway, boreal mixed (ULS)",
    "NIBIO_MLS": "Norway, boreal conifer (mobile laser scanning, ground-based)",
    "SCION": "New Zealand, radiata pine plantation (ULS)",
    "TUWIEN": "Austria, mixed broadleaf (ULS)",
    "Yuchen": "China, broadleaf (DLS)",
    "BlueCat": "Czechia, merged TLS single trees (no terrain; ground added)",
    "WoodLand": "Synthetic eucalypt woodland, 38% canopy cover (HELIOS++ ULS)",
    "OpenForest": "Synthetic eucalypt open forest, 63% canopy cover (HELIOS++ ULS)",
    "ClosedForest": "Synthetic eucalypt closed forest, 84% canopy cover (HELIOS++ ULS)",
    "Kokonoe": "Japan, cedar / cypress plantation (DJI Zenmuse L2, the same sensor as ours)",
    "Saiki": "Japan, cedar / cypress plantation (RIEGL VUX-1LR ULS)",
}
CEDAR = {"Kokonoe", "Saiki"}
ERR = ["ok", "merged", "split", "missed", "boundary"]
NON_DRONE = {"BlueCat", "NIBIO_MLS"}          # ground-based scans: shown, but left out of the "drone" totals
SYNTHETIC = {"WoodLand", "OpenForest", "ClosedForest"}
G_REAL, G_SYN, G_GROUND, G_ALL = "Real drone plots", "Synthetic eucalypt", "Ground-based scans", "ALL"
GROUPS = [G_REAL, G_SYN, G_GROUND, G_ALL]


def group_of(region):
    return G_SYN if region in SYNTHETIC else G_GROUND if region in NON_DRONE else G_REAL


def expand(paths):
    """Expand wildcards ourselves: Windows cmd/PowerShell pass *.ply to Python unexpanded."""
    import glob
    out = []
    for p in paths:
        hits = sorted(glob.glob(p))
        out += hits if hits else [p]
    return out


def region_of(name):
    return "NIBIO_MLS" if name.startswith("NIBIO_MLS") else name.split("_")[0]


def fig_regions(rows, models, path):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    regs = [r for r in dict.fromkeys(r["region"] for r in rows) if r not in GROUPS] + [g for g in GROUPS if any(r["region"] == g for r in rows)]
    w = 0.8 / max(len(models), 1); fig, ax = plt.subplots(figsize=(max(8, 1.2 * len(regs) * max(1, len(models) / 2)), 4.5))
    for k, m in enumerate(models):
        v = [next((r["f1"] for r in rows if r["region"] == g and r["model"] == m), np.nan) for g in regs]
        ax.bar(np.arange(len(regs)) + (k - (len(models) - 1) / 2) * w, v, w, label=m)
    ax.set_xticks(range(len(regs))); ax.set_xticklabels([f"{g}\n({next(r['gt'] for r in rows if r['region'] == g)} trees)" for g in regs], fontsize=8)
    ax.set_ylabel("pooled F1 (IoU >= 0.5)"); ax.set_ylim(0, 1); ax.grid(axis="y", alpha=0.3); ax.legend(fontsize=8)
    ax.axvline(len(regs) - 0.5 - sum(g in regs for g in GROUPS), color="grey", lw=0.8)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def fig_errors(err, path):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    col = {"ok": "#2ca02c", "merged": "#d62728", "split": "#ff7f0e", "missed": "#7f7f7f", "boundary": "#9467bd"}
    models = list(err); fig, ax = plt.subplots(figsize=(9, 0.6 * len(models) + 1.6))
    left = np.zeros(len(models))
    for e in ERR:
        tot = np.array([sum(err[m].values()) for m in models], float)
        v = np.array([err[m][e] for m in models]) / np.maximum(tot, 1)
        ax.barh(range(len(models)), v, left=left, color=col[e], label=e)
        for i, (l, x) in enumerate(zip(left, v)):
            if x > 0.06: ax.text(l + x / 2, i, f"{x:.0%}", ha="center", va="center", fontsize=8, color="white")
        left += v
    ax.set_yticks(range(len(models))); ax.set_yticklabels(models, fontsize=9); ax.invert_yaxis()
    ax.set_xlim(0, 1); ax.set_xlabel("share of reference trees"); ax.legend(ncol=5, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.25))
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)



GALLERY_CSS = (".gal{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 18px}.gal figure{margin:0;width:270px}"
               ".gal.big figure{width:420px}.gal img{width:100%;border:1px solid #ddd}"
               ".gal figcaption{font-size:12px;color:#444;margin-top:2px}details{margin:6px 0}"
               "summary{cursor:pointer;font-weight:600;color:#1F3864;padding:4px 0}")


def _img(path, cap, max_w=900):
    """Embed as JPEG (scatter plots are 5 to 10 times smaller than PNG), scaled to max_w pixels wide."""
    import base64, html as H, io
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > max_w: im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=78, optimize=True)
    b = base64.b64encode(buf.getvalue()).decode()
    return f'<figure><img alt="{H.escape(cap)}" src="data:image/jpeg;base64,{b}"><figcaption>{H.escape(cap)}</figcaption></figure>'


def pictures(a, folder, plots, models):
    """HTML for the two picture sections: plot views and right/wrong examples."""
    import html as H
    import examples as X
    lab_dirs = [Path(d) for d in a.labelled_dir]
    find_las = lambda name: next((q for d in lab_dirs for q in [d / f"{name}.las", d / f"{name}.laz"] if q.exists()), None)
    by_plot = defaultdict(dict)
    for p in plots: by_plot[p["plot"]][p["model"]] = p
    # one plot per drone region: the one with most reference trees
    best = {}
    for name, ms in by_plot.items():
        reg = region_of(name)
        if reg in NON_DRONE: continue
        n = next(iter(ms.values()))["gt"]
        if reg not in best or n > best[reg][1]: best[reg] = (name, n)
    order = sorted(best, key=lambda r: (r in SYNTHETIC, r != "RMIT", r))[:a.view_plots]
    out = ["<h2>Pictures</h2>",
           "<p>Top views of one plot per region: each image is one result, every tree in its own colour, grey = not assigned to any tree. "
           "Click a plot to open it.</p>"]
    for k, reg in enumerate(order):
        name = best[reg][0]; pdir = folder / name
        las = find_las(name)
        if las is None or not (pdir / "predictions").exists(): continue
        keymap = {}
        for f in (pdir / "predictions").glob("*.npy"):
            lab = T.model_label(f.stem); keymap[f.stem] = by_plot[name].get(lab, {})
        print(f"  pictures: {name}")
        imgs = X.plot_views(las, pdir / "predictions", folder / "views" / name, keymap)
        ef = sorted((pdir / "errors").glob("fig_errors_*.png"))
        out.append(f"<details{' open' if k == 0 else ''}><summary>{H.escape(reg)}: {H.escape(name)} "
                   f"({best[reg][1]} trees; {H.escape(REGION_INFO.get(reg, ''))})</summary>")
        out.append('<div class="gal">' + "".join(_img(p, c, 600) for p, c in imgs) + "</div>")
        if ef:
            out.append("<p>Error maps (reference tree tops coloured by error type):</p>")
            out.append('<div class="gal big">' + "".join(_img(f, T.model_label(f.stem[len("fig_errors_"):])) for f in ef) + "</div>")
        out.append("</details>")
    print("  pictures: examples")
    ex = X.examples([folder / p for p in by_plot], lab_dirs, folder / "examples")
    out += ["<h2>Examples: right and wrong</h2>",
            "<p>Randomly chosen reference trees from the drone and synthetic plots (taller than 4 m, at least 150 points), for each model: "
            "2 found correctly, then the error types. Top row seen from above, bottom row from the side. "
            "<b>Red</b> on the left is the reference tree; <b>red</b> on the right is the predicted tree that took most of its points "
            "(for a merged tree it also covers the neighbours). Grey = not assigned to any tree.</p>"]
    for k, (m, items) in enumerate(ex.items()):
        if not items: continue
        out.append(f"<details{' open' if k == 0 else ''}><summary>{H.escape(T.model_label(m))}</summary>"
                   '<div class="gal big">' + "".join(_img(p, c) for p, c in items) + "</div></details>")
    return "\n".join(out) + "\n"

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", help="process 1 output folders, one per plot")
    ap.add_argument("--out", default="summary.md")
    ap.add_argument("--title", default="Tree segmentation evaluation")
    ap.add_argument("--labelled-dir", nargs="+", help="folder(s) with the labelled plots (.las); enables the pictures")
    ap.add_argument("--view-plots", type=int, default=10, help="number of plots shown with one image per model (one per region)")
    ap.add_argument("--no-pictures", action="store_true")
    a = ap.parse_args()
    out_md = Path(a.out); folder = out_md.parent; folder.mkdir(parents=True, exist_ok=True)
    acc = defaultdict(lambda: dict(gt=0, pred=0, matched=0, f1s=[], plots=0, secs=0.0, det=[]))
    err = defaultdict(lambda: defaultdict(int)); err_over = defaultdict(lambda: [0, 0])
    plots = []
    for d in expand(a.folders):
        d = Path(d); f = d / "scores_all_runs.csv"
        if not f.exists(): continue
        region = region_of(d.name)
        for r in csv.DictReader(open(f)):
            if r["protocol"] != "benchmark" or r["shift"] != "0,0": continue
            m = T.model_label(r["model"])
            for key in [(m, region), (m, G_ALL), (m, group_of(region))]:
                s = acc[key]; s["gt"] += int(r["gt_trees"]); s["pred"] += int(r["pred_trees"]); s["matched"] += int(r["matched"])
                s["f1s"].append(float(r["f1"])); s["plots"] += 1; s["secs"] += float(r["seconds"]); s["det"].append(float(r["det_f1"]))
            plots.append(dict(plot=d.name, region=region, group=group_of(region), model=m, gt=int(r["gt_trees"]), pred=int(r["pred_trees"]),
                              matched=int(r["matched"]), f1=float(r["f1"]), det_f1=float(r["det_f1"]), seconds=float(r["seconds"])))
        e = d / "errors" / "summary.csv"
        if e.exists():
            for r in csv.DictReader(open(e)):
                m = T.model_label(r["model"]) if r["model"] in T.MODELS else r["model"]
                for k in ERR: err[m][k] += int(r[f"ref_{k}"])
            for ef in (d / "errors").glob("errors_*.csv"):
                m = T.model_label(ef.stem[len("errors_"):])
                for r in csv.DictReader(open(ef)):
                    if int(r["overtopped"]):
                        err_over[m][0] += r["status"] == "ok"; err_over[m][1] += 1
    if not plots: raise SystemExit("No results found")
    models = list(dict.fromkeys(p["model"] for p in plots))
    rows = []
    groups = [g for g in GROUPS if g == G_ALL or any(p["group"] == g for p in plots)]
    if len(groups) == 2: groups = [G_ALL]                  # only one kind of data: the group total = ALL
    regions = groups + sorted({p["region"] for p in plots})
    for reg in regions:
        for m in models:
            s = acc.get((m, reg))
            if not s: continue
            P = s["matched"] / s["pred"] if s["pred"] else 0; R = s["matched"] / s["gt"] if s["gt"] else 0
            rows.append(dict(region=reg, model=m, plots=s["plots"], gt=s["gt"], pred=s["pred"], matched=s["matched"],
                             precision=P, recall=R, f1=2 * P * R / (P + R) if P + R else 0,
                             f1_mean=float(np.mean(s["f1s"])), det_f1=float(np.mean(s["det"])), seconds=s["secs"]))
    fig_regions(rows, models, folder / "fig_f1_by_region.png")
    if err: fig_errors(err, folder / "fig_error_types.png")

    n_plots = len({p["plot"] for p in plots}); n_trees = next(r["gt"] for r in rows if r["region"] == G_ALL)
    head = groups[0]                                        # real drone plots if any, else synthetic, else ALL
    allrows = sorted([r for r in rows if r["region"] == head], key=lambda r: -r["f1"])
    best = allrows[0]
    fmt = {"precision": "{:.2f}", "recall": "{:.2f}", "f1": "{:.2f}", "f1_mean": "{:.2f}", "det_f1": "{:.2f}", "seconds": "{:.0f}"}
    # one line per model, one column per group: F1 (precision / recall)
    comp = []
    for r in allrows:
        row = dict(model=r["model"])
        for g in groups:
            q = next((x for x in rows if x["region"] == g and x["model"] == r["model"]), None)
            row[g] = f"**{q['f1']:.2f}** ({q['precision']:.2f} / {q['recall']:.2f})" if q else "n/a"
        comp.append(row)
    gsize = {g: next(x for x in rows if x["region"] == g) for g in groups}
    gcols = [(f"{g}: {gsize[g]['gt']} trees, {gsize[g]['plots']} plots", g) for g in groups]
    md = [f"# {a.title}\n",
          f"{n_plots} labelled plots, {n_trees} reference trees. A predicted tree counts as correct when it shares at least "
          "half of its points with one reference tree (IoU >= 0.5, one-to-one), the FOR-instance / SegmentAnyTree rule. "
          "Scores are pooled over all trees (bigger plots weigh more).\n",
          f"Best on {head.lower() if head != G_ALL else 'all plots'}: **{best['model']}**, F1 {best['f1']:.2f} "
          f"(precision {best['precision']:.2f}, recall {best['recall']:.2f}).\n",
          "## Overall\n",
          "F1 (precision / recall) per group of data:\n\n",
          T.md_table(comp, [("Model", "model")] + gcols),
          "\nPrecision = share of predicted trees that are correct. Recall = share of reference trees found. "
          "F1 combines both (1 = perfect). " + " ".join(t for g, t in [
              (G_REAL, "Real drone plots = real UAV laser scans with hand-labelled trees, the closest to our DJI L2 data."),
              (G_SYN, "Synthetic eucalypt = Synthetic Forest scenes (simulated scans, cleaner than real ones)."),
              (G_GROUND, "Ground-based scans = BlueCat and NIBIO_MLS, very different from drone data.")] if g in groups) + "\n",
          "\nDetails per group:\n\n",
          T.md_table([r for g in groups for r in sorted([x for x in rows if x["region"] == g], key=lambda x: -x["f1"])],
                     [("Group", "region"), ("Model", "model"), ("Plots", "plots"), ("Reference trees", "gt"), ("Predicted trees", "pred"),
                      ("Correct", "matched"), ("Precision", "precision"), ("Recall", "recall"), ("F1", "f1"),
                      ("Mean plot F1", "f1_mean"), ("Tree-top detection F1", "det_f1"), ("Run time (s)", "seconds")], fmt),
          "\n## By region\n", "![F1 by region](fig_f1_by_region.png)\n",
          T.md_table([dict(r, info=REGION_INFO.get(r["region"], "")) for r in rows if r["region"] not in GROUPS],
                     [("Region", "region"), ("Forest", "info"), ("Model", "model"), ("Plots", "plots"), ("Trees", "gt"),
                      ("Predicted", "pred"), ("Precision", "precision"), ("Recall", "recall"), ("F1", "f1")], fmt)]
    if err:
        er = [dict(model=m, **{k: err[m][k] for k in ERR}, total=sum(err[m].values()),
                   over=f"{err_over[m][0]}/{err_over[m][1]}" if err_over[m][1] else "n/a") for m in err]
        md += ["\n## What goes wrong (process 3, all plots)\n",
               "Every reference tree is put in one class: `ok` found correctly; `merged` came out together with neighbours as one tree; "
               "`split` came out as several trees; `missed` its points were left unassigned; `boundary` right tree, wrong outline.\n",
               "![error types](fig_error_types.png)\n",
               T.md_table(er, [("Model", "model"), ("ok", "ok"), ("merged", "merged"), ("split", "split"), ("missed", "missed"),
                               ("boundary", "boundary"), ("Reference trees", "total"), ("Found correctly when under another crown", "over")]),
               "\nPer plot: `[plot]/errors/report.md`, maps `fig_errors_[model].png`, coordinates of each tree `errors_[model].csv`.\n"]
    md += ["\n## Per plot\n",
           T.md_table(sorted(plots, key=lambda p: (p["plot"], p["model"])),
                      [("Plot", "plot"), ("Model", "model"), ("Reference", "gt"), ("Predicted", "pred"), ("Correct", "matched"),
                       ("F1", "f1"), ("Tree-top detection F1", "det_f1"), ("s", "seconds")], {"f1": "{:.2f}", "det_f1": "{:.2f}", "seconds": "{:.0f}"}),
           "\n## Notes\n",
           "- Point clouds were thinned to the density in the folder name (e.g. plots_100 = 100 pts/m2) to look like our DJI L2 data.\n",
           "- For reference, published on the full-resolution FOR-instanceV2 test set (same rule): SegmentAnyTree v1 about 0.75 (RMIT), "
           "ForestFormer3D 0.82 to 0.84, SegmentAnyTreeV2 0.85. Thinning makes every model worse, so compare models within this report.\n",
           "- Synthetic Forest scenes are simulated (HELIOS++), cleaner than real scans; use them to rank models, not to predict accuracy on our data.\n",
           "- No model here saw these plots during tuning, except that the classical models' parameters were chosen on RMIT train.\n"]
    if any(p["region"] in CEDAR for p in plots):
        md += ["- CedarCypress3D: circular plots of about 12 m radius inside 36 x 36 m tiles. Only trees whose stem is inside the circle "
               "are labelled; the trees around them are in the data (models see them) but are not scored. Kokonoe was scanned with a "
               "DJI Zenmuse L2, like our data; Saiki with a RIEGL VUX-1LR. Even-aged conifer plantations, so easier than native forest.\n"]
    gallery = ""
    if a.labelled_dir and not a.no_pictures:
        gallery = pictures(a, folder, plots, models)
        md.insert(md.index("\n## Per plot\n"), "\n## Pictures\nSee report.html (plot views in `views/`, examples in `examples/`).\n")
    text = "\n".join(p.rstrip("\n") + "\n" for p in md)
    out_md.write_text(text, encoding="utf-8")
    with open(out_md.with_suffix(".csv"), "w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    from process2_segment import html_from_md
    html = html_from_md(text, folder).replace("<title>Tree profiling report</title>", f"<title>{a.title}</title>")
    if gallery:
        cut = html.find("<h2>Pictures</h2>")
        end = html.find("<h2>", cut + 5)
        html = html[:cut] + gallery + html[end:]
        html = html.replace("</style>", GALLERY_CSS + "</style>")
    (folder / "report.html").write_text(html, encoding="utf-8")
    for r in allrows:
        print(f"{r['model']:32s} pooled F1 {r['f1']:.2f} (P {r['precision']:.2f} R {r['recall']:.2f}) on {r['gt']} trees")
    print("->", folder / "report.html")


if __name__ == "__main__":
    main()
