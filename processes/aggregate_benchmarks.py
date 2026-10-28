"""Pool process 1 results from many plots into one table: per model, per region and overall.

Pooled precision / recall / F1 are computed from the summed counts (matched, predicted,
reference trees) of the shift-0 run of each plot, benchmark protocol, so big plots weigh more,
like the published FOR-instance numbers. The mean of per-plot F1 is given too.

Usage: python aggregate_benchmarks.py results/v2_100/* --out results/v2_100_summary.md
Region = text before the first "_" of the plot folder name (RMIT, NIBIO2, SCION, ...).
"""
import argparse, csv
from collections import defaultdict
from pathlib import Path
import treeseg as T



def expand(paths):
    """Expand wildcards ourselves: Windows cmd/PowerShell pass *.ply to Python unexpanded."""
    import glob
    out = []
    for p in paths:
        hits = sorted(glob.glob(p))
        out += hits if hits else [p]
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", help="process 1 output folders, one per plot")
    ap.add_argument("--out", default="benchmark_summary.md")
    a = ap.parse_args()
    acc = defaultdict(lambda: dict(gt=0, pred=0, matched=0, f1s=[], plots=0, secs=0.0))
    plots = []
    for d in expand(a.folders):
        d = Path(d); f = d / "scores_all_runs.csv"
        if not f.exists(): continue
        region = d.name.split("_")[0]
        for r in csv.DictReader(open(f)):
            if r["protocol"] != "benchmark" or r["shift"] != "0,0": continue
            for key in [(r["model"], region), (r["model"], "ALL")]:
                s = acc[key]; s["gt"] += int(r["gt_trees"]); s["pred"] += int(r["pred_trees"]); s["matched"] += int(r["matched"])
                s["f1s"].append(float(r["f1"])); s["plots"] += 1; s["secs"] += float(r["seconds"])
            plots.append(dict(plot=d.name, region=region, model=r["model"], gt=int(r["gt_trees"]), pred=int(r["pred_trees"]),
                              matched=int(r["matched"]), f1=float(r["f1"]), det_f1=float(r["det_f1"]), seconds=float(r["seconds"])))
    rows = []
    for (m, reg), s in sorted(acc.items(), key=lambda kv: (kv[0][1] != "ALL", kv[0][1], kv[0][0])):
        P = s["matched"] / s["pred"] if s["pred"] else 0; R = s["matched"] / s["gt"] if s["gt"] else 0
        rows.append(dict(region=reg, model=T.MODELS[m]["label"] if m in T.MODELS else m, plots=s["plots"], gt=s["gt"], pred=s["pred"],
                         matched=s["matched"], precision=P, recall=R, f1=2 * P * R / (P + R) if P + R else 0,
                         f1_mean=sum(s["f1s"]) / len(s["f1s"]), seconds=s["secs"]))
    md = ["# Pooled benchmark\n", f"{len({p['plot'] for p in plots})} plots, protocol benchmark, IoU >= 0.5, shift 0 runs.\n",
          T.md_table(rows, [("Region", "region"), ("Model", "model"), ("Plots", "plots"), ("Ref. trees", "gt"), ("Pred. trees", "pred"),
                            ("Matched", "matched"), ("Precision", "precision"), ("Recall", "recall"), ("Pooled F1", "f1"),
                            ("Mean plot F1", "f1_mean"), ("Time (s)", "seconds")],
                     {"precision": "{:.2f}", "recall": "{:.2f}", "f1": "{:.2f}", "f1_mean": "{:.2f}", "seconds": "{:.0f}"}),
          "\n## Per plot\n",
          T.md_table(sorted(plots, key=lambda p: (p["plot"], p["model"])),
                     [("Plot", "plot"), ("Model", "model"), ("Ref.", "gt"), ("Pred.", "pred"), ("Matched", "matched"), ("F1", "f1"),
                      ("Top-detection F1", "det_f1"), ("s", "seconds")], {"f1": "{:.2f}", "det_f1": "{:.2f}", "seconds": "{:.0f}"})]
    Path(a.out).write_text("\n".join(md), encoding="utf-8")
    with open(Path(a.out).with_suffix(".csv"), "w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    for r in rows:
        if r["region"] == "ALL": print(f"{r['model']:28s} pooled F1 {r['f1']:.2f} (P {r['precision']:.2f} R {r['recall']:.2f}) on {r['gt']} trees")
    print("->", a.out)


if __name__ == "__main__":
    main()
