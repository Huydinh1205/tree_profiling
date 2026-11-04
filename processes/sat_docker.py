"""Run SegmentAnyTree v1 (NIBIO 2024, 3DTrees Docker image) on ONE point cloud on this computer.

run_benchmark_v2.bat (model choice S or A) runs it on each dataset's mosaic instead of usegalaxy.eu, and
run_segmentanytree.bat on our own point clouds:
  py -3.10 sat_docker.py ..\\data\\mosaic_v2_100\\mosaic_unlabelled.laz --out ..\\data\\mosaic_v2_100\\sat_result.laz

Needs Docker Desktop (WSL 2 engine) running and an NVIDIA GPU. The input is copied to a temporary folder
on the local disk (Docker cannot always read OneDrive files) and the temporary folder is deleted at the end.
Docker's full output goes to --log.

The result (--out) has exactly the input's points, in the same order and with all their attributes, plus
SegmentAnyTree's fields (PredInstance_SAT = tree ID, PredSemantic_SAT): SegmentAnyTree's own output can hold
a few more points (repeated where its tiles overlap), so its IDs are mapped onto the input by nearest point.
If --out already exists with another number of points (an older run), it is only re-mapped, Docker is not run.
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


def header_info(path):
    """(point count, extra dimension names) or (None, []) if the file cannot be read."""
    try:
        import laspy
        with laspy.open(path) as f:
            return f.header.point_count, list(f.header.point_format.extra_dimension_names)
    except Exception:
        return None, []


def remap_to_input(src, result, out):
    """Write `out` = the points of `src` (same order, all attributes kept) + SegmentAnyTree's fields, each
    point taking the values of the nearest point of `result` (within 0.5 m, else 0 = not a tree)."""
    import numpy as np, laspy
    from scipy.spatial import cKDTree
    A, R = laspy.read(str(src)), laspy.read(str(result))
    rdims = list(R.point_format.extra_dimension_names)
    fields = [f for f in rdims if "instance" in f.lower() or "semantic" in f.lower()]
    if not any("instance" in f.lower() for f in fields):
        fail(f"no instance field in the SegmentAnyTree output {result}: {rdims}")
    vals = {f: np.asarray(R[f]).astype(np.int64) for f in fields}
    d, i = cKDTree(np.c_[R.x, R.y, R.z]).query(np.c_[A.x, A.y, A.z], distance_upper_bound=0.5, workers=-1)
    ok = np.isfinite(d); i[~ok] = 0
    n_res = len(R.x); del R
    new = [f for f in fields if f not in A.point_format.extra_dimension_names]
    if new:
        A.add_extra_dims([laspy.ExtraBytesParams(name=f, type=np.int32) for f in new])
    for f in fields:
        A[f] = np.where(ok, vals[f][i], 0).astype(np.int32)
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")          # never leave a half-written result under the real name
    with open(part, "wb") as fh:                      # a path ending in .part would be written uncompressed
        A.write(fh, do_compress=out.suffix.lower() == ".laz")
    part.replace(out)
    exact = float((d[ok] < 0.01).mean()) if ok.any() else 0.0
    print(f"  {n_res:,} SegmentAnyTree points mapped onto the {len(A.x):,} input points: {ok.mean():.2%} matched, "
          f"{exact:.2%} at the same position; fields {', '.join(fields)}", flush=True)


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
    n_in, _ = header_info(src)

    # an older run whose result does not have the input's points: map it, no need to run Docker again
    if out.exists():
        n_out, dims = header_info(out)
        if n_out and n_in and n_out != n_in and any("instance" in x.lower() for x in dims):
            print(f"{out.name} exists ({n_out:,} points, input {n_in:,}): mapping it onto the input points, "
                  "SegmentAnyTree is not run again.", flush=True)
            tmp = out.with_name(out.stem + "_docker_output" + out.suffix)
            out.replace(tmp)
            remap_to_input(src, tmp, out)
            tmp.unlink()
            return

    docker = shutil.which("docker")
    check_docker(docker, a.image)
    print(f"SegmentAnyTree on {src.name}" + (f" ({n_in:,} points)" if n_in else "") + ". Docker output also goes to "
          f"{log_path}", flush=True)
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="sat_", ignore_cleanup_errors=True) as td:
        td = Path(td)
        ext = src.suffix.lower() if src.suffix.lower() in (".laz", ".las") else ".laz"
        inp = td / f"input{ext}"
        outd = td / "out"; outd.mkdir()
        shutil.copy(src, inp)
        # SegmentAnyTree works in /work INSIDE the container (Docker's own Linux disk) and only the final
        # LAS/LAZ is copied out to /result. Working directly on the Windows folder (through WSL) is slow and
        # can fail with "No such file or directory" when the pipeline deletes and re-creates its folders.
        inner = (f"mkdir -p /work && python3.8 /src/run.py --dataset-path /in/input{ext} --output-dir /work "
                 f"--log-file false && mkdir -p /result/final && "
                 f"cp /work/03_output_SAT/final_results/*.la[sz] /result/final/")
        cmd = [docker, "run", "--rm", "--gpus", "all",
               "-v", f"{inp}:/in/input{ext}:ro", "-v", f"{outd}:/result", a.image, "bash", "-c", inner]
        with open(log_path, "w", encoding="utf-8") as log:
            log.write("$ " + " ".join(cmd) + "\n")
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", bufsize=1)
            for line in p.stdout:
                print(line, end="", flush=True); log.write(line)
            rc = p.wait()
        res = sorted((outd / "final").glob("*.la[sz]"))
        if rc != 0 or not res:
            fail(f"SegmentAnyTree produced no result (Docker exit code {rc}). Look for 'Traceback' or 'Error' in the "
                 f"lines above; full output in {log_path}.\n"
                 "'CUDA out of memory' = the file is too big for the GPU: crop it, or for the benchmark run the "
                 "plots one by one with benchmark_folder.py --models segmentanytree.")
        print(f"\nSegmentAnyTree done in {(time.time() - t0) / 60:.1f} min. Writing {out} ...", flush=True)
        remap_to_input(src, res[0], out)
    print(f"Done -> {out}", flush=True)


if __name__ == "__main__":
    main()
