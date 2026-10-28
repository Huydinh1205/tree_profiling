"""Run process 1 + process 3 on every labelled .las in a folder, then pool everything into one
HTML evaluation report (aggregate_benchmarks.py). Plots already done are skipped, so you can
stop and re-run.

Usage (Windows, from the processes folder):
  py -3.10 benchmark_folder.py ..\\data\\FOR-instanceV2\\plots_100 --out results\\v2_100
  py -3.10 benchmark_folder.py ..\\data\\FOR-instanceV2\\plots_100 --out results\\v2_100 --models chm_watershed dalponte_py

Add a model that was run elsewhere (e.g. SegmentAnyTree on usegalaxy.eu), one file per plot with
the plot's file name, as made by `mosaic_labelled.py split`:
  py -3.10 benchmark_folder.py ..\\data\\FOR-instanceV2\\plots_100 --out results\\v2_100 --external sat=..\\data\\FOR-instanceV2\\mosaic_100\\sat_by_plot
It is scored on top of the results already there (the other models are not run again).

With Docker + NVIDIA GPU on this machine, SegmentAnyTree can run directly instead: --models segmentanytree

Output: results\\v2_100\\report.html (open this), summary.md, summary.csv, and one folder per plot
        (process 1 report + errors\\ from process 3).
"""
import argparse, subprocess, sys, time
from pathlib import Path


def run(cmd, log):
    with open(log, "a") as fh:
        fh.write("\n$ " + " ".join(cmd) + "\n"); fh.flush()
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder with labelled .las files (from prep_labelled.py)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", nargs="*", default=["chm_watershed", "dalponte_py", "li2012_py"])
    ap.add_argument("--external", nargs="*", default=[], metavar="NAME=FOLDER",
                    help="score results made elsewhere: FOLDER holds <plot>.laz for each plot")
    ap.add_argument("--shifts", type=int, default=1)
    ap.add_argument("--redo", action="store_true", help="run again even if results exist")
    ap.add_argument("--title", default=None)
    a = ap.parse_args()
    here = Path(__file__).resolve().parent; py = sys.executable
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    files = sorted(Path(a.folder).glob("*.las")) + sorted(Path(a.folder).glob("*.laz"))
    if not files: sys.exit(f"No .las/.laz in {a.folder}")
    ext = [e.split("=", 1) for e in a.external]
    t0 = time.time()
    for i, f in enumerate(files, 1):
        dst = out / f.stem; log = out / f"{f.stem}.log"; changed = False
        tag = f"[{i}/{len(files)}] {f.stem}"
        done = (dst / "scores_all_runs.csv").exists()
        if a.models and (a.redo or not done):
            print(f"{tag}: models {' '.join(a.models)} ...", flush=True)
            rc = run([py, str(here / "process1_benchmark.py"), str(f), "--out", str(dst), "--models", *a.models,
                      "--shifts", str(a.shifts), "--allow-slow"] + (["--append"] if done else []), log)
            print(f"    {'ok' if rc == 0 else 'FAILED, see ' + str(log)}  ({time.time() - t0:.0f}s so far)"); changed = rc == 0
        elif a.models:
            print(f"{tag}: models already done, skipped")
        for name, folder in ext:
            res = Path(folder) / f"{f.stem}.laz"
            if not res.exists():
                print(f"{tag}: no {name} result ({res}), skipped"); continue
            if (dst / "predictions" / f"external_{name}.npy").exists() and not a.redo:
                continue
            print(f"{tag}: scoring {name} ...", flush=True)
            rc = run([py, str(here / "process1_benchmark.py"), str(f), "--out", str(dst), "--models",
                      "--external", f"{name}={res}", "--append"], log)
            print(f"    {'ok' if rc == 0 else 'FAILED, see ' + str(log)}"); changed = changed or rc == 0
        if not (dst / "scores_all_runs.csv").exists():
            continue
        if changed or not (dst / "errors" / "summary.csv").exists():
            run([py, str(here / "process3_errors.py"), str(f), "--bench", str(dst), "--out", str(dst / "errors")], log)
    cmd = [py, str(here / "aggregate_benchmarks.py"), *[str(p) for p in sorted(out.iterdir()) if p.is_dir()],
           "--out", str(out / "summary.md"), "--labelled-dir", str(Path(a.folder))] + (["--title", a.title] if a.title else [])
    subprocess.run(cmd)
    print(f"\nOpen {out / 'report.html'}")


if __name__ == "__main__":
    main()
