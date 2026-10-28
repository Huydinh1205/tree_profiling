"""Run process 1 on every labelled .las in a folder, then pool the scores (aggregate_benchmarks.py).
Plots that already have results are skipped, so you can stop and re-run.

Usage (Windows, from the processes folder):
  py -3.10 benchmark_folder.py ..\\data\\FOR-instanceV2\\plots_100 --out results\\v2_100
  py -3.10 benchmark_folder.py ..\\data\\FOR-instanceV2\\plots_100 --out results\\v2_100 --models chm_watershed dalponte_py
Output: results\\v2_100\\<plot>\\ (one process 1 report per plot) and results\\v2_100\\summary.md + summary.csv
"""
import argparse, subprocess, sys, time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder with labelled .las files (from prep_labelled.py)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", nargs="+", default=["chm_watershed", "dalponte_py", "li2012_py"])
    ap.add_argument("--shifts", type=int, default=1)
    ap.add_argument("--redo", action="store_true", help="run again even if results exist")
    a = ap.parse_args()
    here = Path(__file__).resolve().parent
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    files = sorted(Path(a.folder).glob("*.las")) + sorted(Path(a.folder).glob("*.laz"))
    if not files: sys.exit(f"No .las/.laz in {a.folder}")
    t0 = time.time()
    for i, f in enumerate(files, 1):
        dst = out / f.stem
        if (dst / "scores_all_runs.csv").exists() and not a.redo:
            print(f"[{i}/{len(files)}] {f.stem}: already done, skipped"); continue
        print(f"[{i}/{len(files)}] {f.stem} ...", flush=True)
        cmd = [sys.executable, str(here / "process1_benchmark.py"), str(f), "--out", str(dst),
               "--models", *a.models, "--shifts", str(a.shifts), "--allow-slow"]
        with open(out / f"{f.stem}.log", "w") as log:
            r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
        print(f"    {'ok' if r.returncode == 0 else 'FAILED, see ' + str(out / (f.stem + '.log'))}  ({time.time() - t0:.0f}s so far)")
    subprocess.run([sys.executable, str(here / "aggregate_benchmarks.py"), *[str(p) for p in sorted(out.iterdir()) if p.is_dir()],
                    "--out", str(out / "summary.md")])


if __name__ == "__main__":
    main()
