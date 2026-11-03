"""Run SegmentAnyTree v1 (NIBIO 2024, 3DTrees Docker image) on ONE point cloud on this computer.

run_benchmark_v2.bat (model choice S or A) runs it on each dataset's mosaic instead of usegalaxy.eu:
  py -3.10 sat_docker.py ..\\data\\mosaic_v2_100\\mosaic_unlabelled.laz --out ..\\data\\mosaic_v2_100\\sat_result.laz

Needs Docker Desktop (WSL 2 engine) running and an NVIDIA GPU. The input is copied to a temporary folder
on the local disk (Docker cannot always read OneDrive files); the result is copied to --out and the
temporary folder is deleted, so nothing is left outside the project. Docker's full output goes to --log.
"""
import argparse, shutil, subprocess, sys, tempfile, time
from pathlib import Path

IMAGE = "ghcr.io/3dtrees-earth/3dtrees_sat:1.1.0"   # same image as treeseg.run_segmentanytree


def fail(msg):
    print(f"\nSegmentAnyTree: {msg}", flush=True)
    sys.exit(1)


def quiet(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def check_docker(docker, image):
    if not docker:
        fail("Docker not found. Install Docker Desktop (WSL 2 engine), then open a NEW window and run again.")
    if quiet([docker, "info"]).returncode != 0:
        fail("Docker Desktop is not running. Open Docker Desktop, wait for 'Engine running', then run again.")
    if quiet([docker, "image", "inspect", image]).returncode != 0:
        print(f"Downloading the SegmentAnyTree image {image} (several GB, only the first time)...", flush=True)
        if subprocess.run([docker, "pull", image]).returncode != 0:
            latest = image.rsplit(":", 1)[0] + ":latest"
            print(f"{image} is not available, trying {latest} ...", flush=True)
            if subprocess.run([docker, "pull", latest]).returncode != 0:
                fail("could not download the image. Check the internet connection.")
            quiet([docker, "tag", latest, image])
    r = quiet([docker, "run", "--rm", "--gpus", "all", "hello-world"])
    if r.returncode != 0:
        fail("Docker cannot use the GPU (--gpus all failed). Check the NVIDIA driver and that Docker Desktop "
             "uses the WSL 2 engine (Settings > General).\n" + r.stderr[-1500:])


def point_count(path):
    try:
        import laspy
        with laspy.open(path) as f:
            return f.header.point_count
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="point cloud (.laz or .las)")
    ap.add_argument("--out", required=True, help="where to put the segmented result (.laz)")
    ap.add_argument("--log", default=None, help="file for Docker's full output (default: <out>.log)")
    ap.add_argument("--image", default=IMAGE)
    a = ap.parse_args()
    src, out = Path(a.input), Path(a.out)
    log_path = Path(a.log) if a.log else out.with_suffix(".log")
    if not src.exists():
        fail(f"input not found: {src}")
    docker = shutil.which("docker")
    check_docker(docker, a.image)

    n_in = point_count(src)
    print(f"SegmentAnyTree on {src.name}" + (f" ({n_in:,} points)" if n_in else "") + ". Docker output also goes to "
          f"{log_path}", flush=True)
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="sat_", ignore_cleanup_errors=True) as td:
        td = Path(td)
        ext = src.suffix.lower() if src.suffix.lower() in (".laz", ".las") else ".laz"
        inp = td / f"input{ext}"
        outd = td / "out"; outd.mkdir()
        shutil.copy(src, inp)
        cmd = [docker, "run", "--rm", "--gpus", "all",
               "-v", f"{inp}:/in/input{ext}:ro", "-v", f"{outd}:/out", a.image,
               "python3.8", "/src/run.py", "--dataset-path", f"/in/input{ext}", "--output-dir", "/out",
               "--log-file", "false"]
        with open(log_path, "w", encoding="utf-8") as log:
            log.write("$ " + " ".join(cmd) + "\n")
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", bufsize=1)
            for line in p.stdout:
                print(line, end="", flush=True); log.write(line)
            rc = p.wait()
        if rc != 0:
            fail(f"Docker run failed (exit code {rc}). Last lines are above; full output in {log_path}.\n"
                 "If it says 'CUDA out of memory', the file is too big for the GPU: run the plots one by one with "
                 "benchmark_folder.py --models segmentanytree instead.")
        res = sorted((outd / "03_output_SAT" / "final_results").glob("*.la[sz]")) or \
              sorted(f for f in outd.rglob("*.la[sz]") if f.name != inp.name)
        if not res:
            fail(f"Docker finished but produced no LAS/LAZ. Full output in {log_path}.")
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_name(out.name + ".part")          # never leave a half-copied result under the real name
        shutil.copy(res[0], part)
        part.replace(out)
    n_out = point_count(out)
    mins = (time.time() - t0) / 60
    print(f"\nSegmentAnyTree done in {mins:.1f} min -> {out}" + (f" ({n_out:,} points)" if n_out else ""), flush=True)
    if n_in and n_out and n_in != n_out:
        print(f"WARNING: the result has {n_out:,} points, the input {n_in:,}. The benchmark needs the same points; "
              "send this window to Ryan.")


if __name__ == "__main__":
    main()
