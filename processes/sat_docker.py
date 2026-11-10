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

Big mosaics (--index): SegmentAnyTree reads the whole file into RAM before it starts, and a mosaic of
~100 million points (CedarCypress3D or Synthetic Forest at original density) gets killed by Windows/WSL
("TerminatedWorkerError ... SIGKILL(-9)"). With --index <mosaic>/index.csv the plots are sent in groups of
whole plots, each group at most --max-points points (default 20 million), one Docker run per group. Each
group's result is kept in <out name>_parts so a run that stops half way resumes at the next group; the
groups are then put back together in the input's point order, with tree IDs made unique across groups.
Plots are 30 m apart in the mosaic, so no tree is cut between groups.
If SegmentAnyTree crashes on a group (e.g. "Segmentation fault" in eval.py on the dense Saiki plots of
CedarCypress3D), that group's plots are run one at a time; a plot that still crashes is tried again thinned to
one point per --thin voxel (5 cm, then 10 cm), and its tree IDs are mapped back onto ALL its points by nearest
point. A plot that fails every try is listed at the end (the run stops without writing --out; finished plots
are kept, so the next run only retries the failed ones).
  py -3.10 sat_docker.py ..\\data\\mosaic_ced_full\\mosaic_unlabelled.laz --out ..\\data\\mosaic_ced_full\\sat_result.laz
      --index ..\\data\\mosaic_ced_full\\index.csv

Big single files (no --index, e.g. a whole DJI Terra export dropped on run_segmentanytree.bat): a file with
more than --max-points points is cut into a grid of tiles, each tile run on its own with a --buffer (20 m) of
the neighbouring tiles around it, so a tree on a tile edge is seen whole by the tile that holds its top. From
each tile only the trees whose highest point lies inside the tile itself (not in the buffer) are kept, so no
tree is cut in two or counted twice. A point claimed by two kept trees goes to the tree of the tile it lies in.
The tile size is chosen so that tile + buffer holds at most --max-points points (or set it with --tile).
A tile with very few points (an edge of the flight) is not run alone: its trees are taken from the nearest
tile. Each tile's result is kept in <out name>_tiles_<size> so a run that stops half way resumes at the next
tile; a tile that crashes is tried again thinned (--thin), like the plots above. A tile that fails every try is
listed and the run stops without writing --out (running again only retries it).
  py -3.10 sat_docker.py "Galaxy16.laz" --out results\\Galaxy16_sat\\sat_result.laz
"""
import argparse, csv, json, shutil, subprocess, sys, tempfile, time
from pathlib import Path

IMAGE = "ghcr.io/3dtrees-earth/3dtrees_sat:1.1.0"   # same image as treeseg.run_segmentanytree
MAX_POINTS = 20_000_000
THIN = (0.05, 0.10)                                 # voxel sizes (m) tried on a single plot that keeps crashing
BUFFER = 20.0                                       # m of neighbouring tiles around each tile (> biggest crown radius)
MIN_TILE = 20.0                                     # m, smallest tile side
MIN_CORE = 100_000                                  # a tile with fewer points is handled by its nearest tile


class SatFailed(Exception):
    """SegmentAnyTree ran but gave no result (crash, out of memory...)."""


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


def write_points(dst, x, y, z, offsets, scales, fields=None):
    """LAZ with x, y, z (+ int32 extra fields). Written to <dst>.part first, so a cut write never looks finished."""
    import numpy as np, laspy
    h = laspy.LasHeader(point_format=6, version="1.4")
    h.offsets, h.scales = offsets, scales
    for f in (fields or {}):
        h.add_extra_dim(laspy.ExtraBytesParams(name=f, type=np.int32))
    W = laspy.LasData(h); W.x, W.y, W.z = x, y, z
    for f, v in (fields or {}).items():
        W[f] = v
    dst = Path(dst); part = dst.with_name(dst.name + ".part")
    with open(part, "wb") as fh:                      # a path ending in .part would be written uncompressed
        W.write(fh, do_compress=True)
    part.replace(dst)


def write_xyz(src, dst):
    """Copy of `src` with x, y, z only. SegmentAnyTree adds its own PredInstance_SAT / PredSemantic_SAT fields
    and crashes when the input already has them (e.g. a Galaxy result); every original attribute comes back
    anyway in remap_to_input, which writes the input's own points."""
    import laspy
    L = laspy.read(str(src))
    write_points(dst, L.x, L.y, L.z, L.header.offsets, L.header.scales)


def map_fields(x, y, z, result):
    """SegmentAnyTree's instance / semantic fields for the points x, y, z: each point takes the values of the
    nearest point of `result` (within 0.5 m, else 0 = not a tree). Returns ({field: int32 array}, summary)."""
    import numpy as np, laspy
    from scipy.spatial import cKDTree
    R = laspy.read(str(result))
    rdims = list(R.point_format.extra_dimension_names)
    fields = [f for f in rdims if "instance" in f.lower() or "semantic" in f.lower()]
    if not any("instance" in f.lower() for f in fields):
        fail(f"no instance field in the SegmentAnyTree output {result}: {rdims}")
    vals = {f: np.asarray(R[f]).astype(np.int64) for f in fields}
    d, i = cKDTree(np.c_[R.x, R.y, R.z]).query(np.c_[x, y, z], distance_upper_bound=0.5, workers=-1)
    ok = np.isfinite(d); i[~ok] = 0
    n_res = len(R.x); del R
    out = {f: np.where(ok, vals[f][i], 0).astype(np.int32) for f in fields}
    exact = float((d[ok] < 0.01).mean()) if ok.any() else 0.0
    return out, (f"{n_res:,} SegmentAnyTree points mapped onto the {len(x):,} input points: {ok.mean():.2%} matched, "
                 f"{exact:.2%} at the same position; fields {', '.join(fields)}")


def write_with_fields(src, fields, out):
    """Write `out` = the points of `src` (same order, all attributes kept) + the given int32 fields."""
    import numpy as np, laspy
    A = laspy.read(str(src))
    new = [f for f in fields if f not in A.point_format.extra_dimension_names]
    if new:
        A.add_extra_dims([laspy.ExtraBytesParams(name=f, type=np.int32) for f in new])
    for f, v in fields.items():
        A[f] = v
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")          # never leave a half-written result under the real name
    with open(part, "wb") as fh:
        A.write(fh, do_compress=out.suffix.lower() == ".laz")
    part.replace(out)


def remap_to_input(src, result, out):
    """Write `out` = the points of `src` (same order, all attributes kept) + SegmentAnyTree's fields."""
    import laspy
    L = laspy.read(str(src))
    fields, msg = map_fields(L.x, L.y, L.z, result)
    del L
    write_with_fields(src, fields, out)
    print("  " + msg, flush=True)


def run_docker(docker, image, inp, td, log, label=""):
    """SegmentAnyTree on the x,y,z file `inp` (in the temporary folder `td`). Returns the result file."""
    outd = td / "out"; outd.mkdir()
    # SegmentAnyTree works in /work INSIDE the container (Docker's own Linux disk) and only the final
    # LAS/LAZ is copied out to /result. Working directly on the Windows folder (through WSL) is slow and
    # can fail with "No such file or directory" when the pipeline deletes and re-creates its folders.
    inner = ("mkdir -p /work && python3.8 /src/run.py --dataset-path /in/input.laz --output-dir /work "
             "--log-file false && mkdir -p /result/final && "
             "cp /work/03_output_SAT/final_results/*.la[sz] /result/final/")
    # --shm-size: Docker's default 64 MB of shared memory makes PyTorch crash (segfault) on big clouds
    cmd = [docker, "run", "--rm", "--gpus", "all", "--shm-size", "16g",
           "-v", f"{inp}:/in/input.laz:ro", "-v", f"{outd}:/result", image, "bash", "-c", inner]
    log.write((f"\n===== {label}\n" if label else "") + "$ " + " ".join(cmd) + "\n"); log.flush()
    killed = oom_gpu = segv = False
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        print(line, end="", flush=True); log.write(line)
        killed |= "SIGKILL" in line or "TerminatedWorkerError" in line
        oom_gpu |= "CUDA out of memory" in line
        segv |= "Segmentation fault" in line
    rc = p.wait(); log.flush()
    res = sorted((outd / "final").glob("*.la[sz]"))
    if rc != 0 or not res:
        if segv:
            why = "SegmentAnyTree crashed (Segmentation fault)."
        elif killed:
            why = ("OUT OF RAM: Windows/WSL killed SegmentAnyTree (SIGKILL) because the file is too big for the memory "
                   "Docker may use. Run again with a smaller --max-points (e.g. 10000000), or give WSL more memory: "
                   "in %USERPROFILE%\\.wslconfig write the two lines  [wsl2]  and  memory=24GB  (about 3/4 of this "
                   "computer's RAM), then run 'wsl --shutdown' and start Docker Desktop again.")
        elif oom_gpu:
            why = "CUDA out of memory = too big for the GPU: run again with a smaller --max-points (e.g. 10000000)."
        else:
            why = "Look for 'Traceback' or 'Error' in the lines above."
        raise SatFailed(f"SegmentAnyTree produced no result{' for ' + label if label else ''} (Docker exit code {rc}).\n"
                        f"{why}\nFull output in {log.name}.")
    return res[0]


def voxel_keep(x, y, z, v):
    """Indices of one point per v-metre voxel (the first one), in the original order."""
    import numpy as np
    k = np.floor(np.c_[x, y, z] / v).astype(np.int64)
    _, first = np.unique(k, axis=0, return_index=True)
    return np.sort(first)


def plot_groups(index, x, y, max_points):
    """Group the mosaic's plots (index.csv rows, in order) so each group has at most max_points points
    (a plot bigger than that goes alone). Returns (rows, plot number of each point or -1, groups)."""
    import numpy as np
    with open(index, newline="") as fh:
        rows = list(csv.DictReader(fh))
    plot = np.full(len(x), -1, np.int32)
    for k, r in enumerate(rows):                      # same box as mosaic_labelled.py split; plots are 30 m apart
        m = ((x >= float(r["xmin"]) - 0.5) & (x <= float(r["xmax"]) + 0.5) &
             (y >= float(r["ymin"]) - 0.5) & (y <= float(r["ymax"]) + 0.5))
        plot[m] = k
    counts = np.bincount(plot[plot >= 0], minlength=len(rows))
    groups, cur, n = [], [], 0
    for k in range(len(rows)):
        if cur and n + counts[k] > max_points:
            groups.append(cur); cur, n = [], 0
        cur.append(k); n += int(counts[k])
    if cur:
        groups.append(cur)
    return rows, plot, groups


def run_grouped(docker, image, src, out, index, max_points, log_path, thin_steps=THIN):
    import numpy as np, laspy
    L = laspy.read(str(src))
    offsets, scales = L.header.offsets, L.header.scales
    x, y, z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
    del L
    rows, plot, groups = plot_groups(index, x, y, max_points)
    loose = int((plot < 0).sum())
    print(f"Too many points for one SegmentAnyTree run: {len(rows)} plots in {len(groups)} groups of at most "
          f"{max_points:,} points, one Docker run each. A group already done is skipped.", flush=True)
    if loose:
        print(f"  ({loose:,} points are outside every plot box in {index}; they get tree ID 0)", flush=True)
    parts = out.with_name(out.stem + "_parts"); parts.mkdir(parents=True, exist_ok=True)

    def part_of(ks):
        return parts / f"plots_{ks[0] + 1:02d}-{ks[-1] + 1:02d}.laz"

    def done(ks):
        p = part_of(ks)
        n_part, dims = header_info(p) if p.exists() else (None, [])
        return n_part == int(np.isin(plot, ks).sum()) and any("instance" in d.lower() for d in dims)

    def attempt(ks, label, log, thin=0.0):
        """One Docker run on the plots ks (thinned to one point per `thin` m voxel if thin > 0); writes the part."""
        sel = np.flatnonzero(np.isin(plot, ks))
        keep = voxel_keep(x[sel], y[sel], z[sel], thin) if thin else np.arange(len(sel))
        if thin:
            label += f", thinned to one point per {thin * 100:.0f} cm: {len(keep):,} points"
        print(f"\n--- {label}", flush=True)
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="sat_", ignore_cleanup_errors=True) as td:
            td = Path(td)
            s = sel[keep]
            write_points(td / "input.laz", x[s], y[s], z[s], offsets, scales)
            res = run_docker(docker, image, td / "input.laz", td, log, label)
            fields, msg = map_fields(x[sel], y[sel], z[sel], res)     # back onto ALL the points of these plots
        write_points(part_of(ks), x[sel], y[sel], z[sel], offsets, scales, fields)
        print(f"  {msg}\n  done in {(time.time() - t0) / 60:.1f} min -> {part_of(ks)}", flush=True)

    def plot_name(k):
        return f"plot {k + 1} ({rows[k]['plot']})"

    covered, failed = [], []                          # covered: plot lists whose part file holds a result
    with open(log_path, "w", encoding="utf-8") as log:
        for g, ks in enumerate(groups, 1):
            n = int(np.isin(plot, ks).sum())
            label = f"group {g}/{len(groups)}: plots {ks[0] + 1} to {ks[-1] + 1} of {len(rows)}, {n:,} points"
            if done(ks):
                print(f"\n--- {label}: already done ({part_of(ks).name}), skipped", flush=True)
                covered.append(ks); continue
            # a group that crashed before (some of its plots already have their own part): go plot by plot
            if len(ks) == 1 or not any(done([k]) for k in ks):
                try:
                    attempt(ks, label, log); covered.append(ks); continue
                except SatFailed as e:
                    print(f"\n{e}", flush=True)
                    if len(ks) > 1:
                        print(f"  -> running the {len(ks)} plots of group {g} one at a time", flush=True)
            for k in ks:
                one = [k]
                if done(one):
                    if len(ks) > 1:
                        print(f"\n--- {plot_name(k)}: already done ({part_of(one).name}), skipped", flush=True)
                    covered.append(one); continue
                n1 = int((plot == k).sum())
                tries = ([0.0] if len(ks) > 1 else []) + list(thin_steps)
                for thin in tries:
                    try:
                        attempt(one, f"{plot_name(k)}, {n1:,} points", log, thin); covered.append(one); break
                    except SatFailed as e:
                        print(f"\n{e}", flush=True)
                else:
                    failed.append(k)
    if failed:
        fail(f"SegmentAnyTree failed on {len(failed)} plot(s) even one at a time and thinned: "
             + ", ".join(plot_name(k) for k in failed) + f".\nThe other plots are done and kept in {parts}; "
             "running again only retries these. Send this window and the log to Ryan.")

    full, next_id = {}, 0
    for ks in covered:
        P = laspy.read(str(part_of(ks)))
        sel = np.flatnonzero(np.isin(plot, ks))
        for f in P.point_format.extra_dimension_names:
            v = np.asarray(P[f]).astype(np.int64)
            if "instance" in f.lower():                # tree IDs restart at 1 in every part: make them unique
                v = np.where(v > 0, v + next_id, 0)
                next_id = max(next_id, int(v.max()) if len(v) else 0)
            full.setdefault(f, np.zeros(len(x), np.int32))[sel] = v.astype(np.int32)
        del P
    del x, y, z
    print(f"\nAll {len(groups)} groups done. Writing {out} ...", flush=True)
    write_with_fields(src, full, out)
    inst = next(f for f in full if "instance" in f.lower())
    print(f"  {len(np.unique(full[inst][full[inst] > 0])):,} trees in {out.name}", flush=True)


def tile_grid(x, y, max_points, buffer, tile=None):
    """Grid of tiles over the file's extent. Returns (nx, ny, tx, ty, x0, y0): nx x ny tiles of tx x ty m from
    (x0, y0). Without `tile`, the side is the largest one for which every tile + buffer holds <= max_points."""
    import numpy as np
    x0, y0 = float(x.min()), float(y.min())
    W, H = max(float(x.max()) - x0, 1e-3), max(float(y.max()) - y0, 1e-3)

    def grid(t):
        nx, ny = max(1, int(np.ceil(W / t))), max(1, int(np.ceil(H / t)))
        return nx, ny, W / nx, H / ny                 # even tiles, so no thin strip is left at the far edge

    if tile:
        return (*grid(tile), x0, y0)
    res = max(1.0, max(W, H) / 4000)                  # point count histogram, at most 4000 x 4000 cells
    ci = np.minimum((x - x0) / res, W / res).astype(np.int64)
    cj = np.minimum((y - y0) / res, H / res).astype(np.int64)
    hist = np.zeros((int(ci.max()) + 1, int(cj.max()) + 1), np.int64)
    np.add.at(hist, (ci, cj), 1)
    S = np.zeros((hist.shape[0] + 1, hist.shape[1] + 1), np.int64); S[1:, 1:] = hist.cumsum(0).cumsum(1)

    def most(t):                                      # most points in one tile + buffer
        nx, ny, tx, ty = grid(t)
        a = np.clip(np.floor((np.arange(nx) * tx - buffer) / res), 0, S.shape[0] - 1).astype(int)
        b = np.clip(np.ceil(((np.arange(nx) + 1) * tx + buffer) / res), 0, S.shape[0] - 1).astype(int)
        c = np.clip(np.floor((np.arange(ny) * ty - buffer) / res), 0, S.shape[1] - 1).astype(int)
        d = np.clip(np.ceil(((np.arange(ny) + 1) * ty + buffer) / res), 0, S.shape[1] - 1).astype(int)
        n = S[b][:, d] - S[a][:, d] - S[b][:, c] + S[a][:, c]
        return int(n.max())

    dens = len(x) / max((hist > 0).sum() * res * res, 1.0)       # points per m2 where there are points
    t = max(MIN_TILE, min(max(W, H), np.sqrt(max_points / dens) - 2 * buffer))
    while t > MIN_TILE and most(t) > max_points:
        t = max(MIN_TILE, t * 0.85)
    return (*grid(t), x0, y0)


def run_tiled(docker, image, src, out, max_points, buffer, log_path, thin_steps=THIN, tile=None, run=None):
    """SegmentAnyTree on a big single file, tile by tile (see the docstring at the top). `run` replaces
    run_docker (tests)."""
    import numpy as np, laspy
    run = run or run_docker
    L = laspy.read(str(src))
    offsets, scales = L.header.offsets, L.header.scales
    x, y, z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
    del L
    nx, ny, tx, ty, x0, y0 = tile_grid(x, y, max_points, buffer, tile)
    ti = np.minimum(((x - x0) / tx).astype(np.int64), nx - 1)
    tj = np.minimum(((y - y0) / ty).astype(np.int64), ny - 1)
    tid = (ti * ny + tj).astype(np.int32)             # tile of each point (its core)
    del ti, tj
    core_n = np.bincount(tid, minlength=nx * ny)
    small = max(1, min(MIN_CORE, max_points // 50))
    runs = [k for k in range(nx * ny) if core_n[k] >= small]
    if not runs:
        runs = [int(np.argmax(core_n))]
    centre = lambda k: ((k // ny + 0.5) * tx, (k % ny + 0.5) * ty)
    owner = {}                                        # tile -> tile whose result holds its trees
    for k in range(nx * ny):
        if core_n[k] == 0: continue
        if k in runs:
            owner[k] = k
        else:
            cx, cy = centre(k)
            owner[k] = min(runs, key=lambda r: (centre(r)[0] - cx) ** 2 + (centre(r)[1] - cy) ** 2)

    def window(k):                                    # indices of the points of tile k + buffer
        i, j = divmod(k, ny)
        m = ((x >= x0 + i * tx - buffer) & (x <= x0 + (i + 1) * tx + buffer) &
             (y >= y0 + j * ty - buffer) & (y <= y0 + (j + 1) * ty + buffer))
        return np.flatnonzero(m)

    name = f"{tx:.0f}x{ty:.0f}m"
    parts = out.with_name(f"{out.stem}_tiles_{name}"); parts.mkdir(parents=True, exist_ok=True)
    info = dict(input=str(src), points=int(len(x)), tiles=[nx, ny], tile_m=[tx, ty], buffer_m=buffer, origin=[x0, y0])
    (parts / "tiles.json").write_text(json.dumps(info, indent=1))
    print(f"Too many points for one SegmentAnyTree run ({len(x):,} > {max_points:,}): {nx} x {ny} tiles of "
          f"{tx:.0f} x {ty:.0f} m (+ {buffer:g} m buffer), {len(runs)} Docker runs. A tile already done is skipped.",
          flush=True)
    if len(runs) < int((core_n > 0).sum()):
        print(f"  ({int((core_n > 0).sum()) - len(runs)} tiles with fewer than {small:,} points are taken from "
              "their nearest tile)", flush=True)

    def part_of(k):
        i, j = divmod(k, ny)
        return parts / f"tile_{i + 1:02d}_{j + 1:02d}.laz"

    def done(k, n):
        p = part_of(k)
        n_part, dims = header_info(p) if p.exists() else (None, [])
        return n_part == n and any("instance" in d.lower() for d in dims)

    failed = []
    with open(log_path, "w", encoding="utf-8") as log:
        for g, k in enumerate(runs, 1):
            i, j = divmod(k, ny)
            sel = window(k)
            label = f"tile {g}/{len(runs)} (column {i + 1}, row {j + 1}): {core_n[k]:,} points + buffer = {len(sel):,}"
            if done(k, len(sel)):
                print(f"\n--- {label}: already done ({part_of(k).name}), skipped", flush=True); continue
            for thin in [0.0] + list(thin_steps):
                keep = voxel_keep(x[sel], y[sel], z[sel], thin) if thin else np.arange(len(sel))
                lab = label + (f", thinned to one point per {thin * 100:.0f} cm: {len(keep):,} points" if thin else "")
                print(f"\n--- {lab}", flush=True)
                t0 = time.time()
                try:
                    with tempfile.TemporaryDirectory(prefix="sat_", ignore_cleanup_errors=True) as td:
                        td = Path(td); s = sel[keep]
                        write_points(td / "input.laz", x[s], y[s], z[s], offsets, scales)
                        res = run(docker, image, td / "input.laz", td, log, lab)
                        fields, msg = map_fields(x[sel], y[sel], z[sel], res)
                except SatFailed as e:
                    print(f"\n{e}", flush=True); continue
                write_points(part_of(k), x[sel], y[sel], z[sel], offsets, scales, fields)
                print(f"  {msg}\n  done in {(time.time() - t0) / 60:.1f} min -> {part_of(k)}", flush=True)
                break
            else:
                failed.append(k)
    if failed:
        fail(f"SegmentAnyTree failed on {len(failed)} tile(s) even thinned: "
             + ", ".join(part_of(k).stem for k in failed) + f".\nThe other tiles are done and kept in {parts}; "
             "running again only retries these. Try a smaller --max-points (e.g. 10000000), or send this window "
             "and the log to Ryan.")

    # --- put the tiles together: keep the trees whose top is in the tile's core (or in a small tile it owns)
    print(f"\nAll {len(runs)} tiles done. Putting them together ...", flush=True)
    full, prio, next_id, inst_f = {}, np.zeros(len(x), np.int8), 0, None
    owns = {r: [k for k, o in owner.items() if o == r] for r in runs}
    for k in runs:
        sel = window(k)
        P = laspy.read(str(part_of(k)))
        dims = list(P.point_format.extra_dimension_names)
        inst_f = inst_f or next(f for f in dims if "instance" in f.lower())
        v = np.asarray(P[inst_f]).astype(np.int64); v[v < 0] = 0
        mine = np.isin(tid[sel], owns[k])             # points in this tile's core (and the small tiles it owns)
        for f in dims:                                # other fields (semantic ...): from the tile the point lies in
            if f == inst_f: continue
            full.setdefault(f, np.zeros(len(x), np.int32))[sel[mine]] = np.asarray(P[f])[mine].astype(np.int32)
        del P
        t = v > 0
        if t.any():
            ids, inv = np.unique(v[t], return_inverse=True)
            zt = z[sel][t]; o = np.lexsort((zt, inv))  # per tree, its highest point last
            last = np.r_[np.flatnonzero(np.diff(inv[o])), len(o) - 1]
            top = np.flatnonzero(t)[o[last]]          # index (in sel) of each tree's highest point
            kept = np.isin(tid[sel][top], owns[k])
            new = np.zeros(len(ids), np.int64); new[kept] = next_id + 1 + np.arange(kept.sum())
            next_id += int(kept.sum())
            nv = np.zeros(len(v), np.int64); nv[np.flatnonzero(t)] = new[inv]
            p = np.where(mine, 2, 1).astype(np.int8)  # a point in this tile's core beats a buffer claim
            take = (nv > 0) & (p > prio[sel])
            full.setdefault(inst_f, np.zeros(len(x), np.int32))[sel[take]] = nv[take].astype(np.int32)
            prio[sel[take]] = p[take]
    full.setdefault(inst_f or "PredInstance_SAT", np.zeros(len(x), np.int32))
    inst_f = inst_f or "PredInstance_SAT"
    u, inv = np.unique(full[inst_f], return_inverse=True)       # 1..n, 0 stays 0 (a tree may have lost all points)
    full[inst_f] = (inv + (0 if u[0] == 0 else 1)).astype(np.int32)
    del x, y, z, tid, prio
    print(f"Writing {out} ...", flush=True)
    write_with_fields(src, full, out)
    print(f"  {len(np.unique(full[inst_f][full[inst_f] > 0])):,} trees in {out.name}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="point cloud (.laz or .las)")
    ap.add_argument("--out", required=True, help="where to put the segmented result (.laz)")
    ap.add_argument("--log", default=None, help="file for Docker's full output (default: <out>.log)")
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument("--index", default=None, help="index.csv of a mosaic (mosaic_labelled.py build): a big mosaic "
                    "then runs in groups of whole plots")
    ap.add_argument("--max-points", type=int, default=MAX_POINTS, help="most points per Docker run: a bigger file "
                    "runs in groups of plots (--index) or in tiles (default "
                    f"{MAX_POINTS:,}; lower it if SegmentAnyTree is still killed or the GPU runs out; 0 = never split)")
    ap.add_argument("--thin", type=float, nargs="*", default=list(THIN), help="voxel sizes (m) tried, one after the "
                    "other, on a plot or tile that keeps crashing (default 0.05 0.10; none = no thinning)")
    ap.add_argument("--tile", type=float, default=None, help="big single file: tile side in m (default: the largest "
                    "that keeps tile + buffer under --max-points)")
    ap.add_argument("--buffer", type=float, default=BUFFER, help=f"big single file: m of the neighbouring tiles run "
                    f"with each tile (default {BUFFER:g}; at least the biggest crown radius)")
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
    if a.index and n_in and a.max_points > 0 and n_in > a.max_points:
        run_grouped(docker, a.image, src, out, Path(a.index), a.max_points, log_path, a.thin)
    elif n_in and a.max_points > 0 and (n_in > a.max_points or a.tile):
        run_tiled(docker, a.image, src, out, a.max_points, a.buffer, log_path, a.thin, a.tile)
    else:
        with tempfile.TemporaryDirectory(prefix="sat_", ignore_cleanup_errors=True) as td, \
                open(log_path, "w", encoding="utf-8") as log:
            td = Path(td)
            write_xyz(src, td / "input.laz")          # x, y, z only (see write_xyz)
            try:
                res = run_docker(docker, a.image, td / "input.laz", td, log)
            except SatFailed as e:
                fail(str(e))
            print(f"\nSegmentAnyTree done in {(time.time() - t0) / 60:.1f} min. Writing {out} ...", flush=True)
            remap_to_input(src, res, out)
    print(f"Done in {(time.time() - t0) / 60:.1f} min -> {out}", flush=True)


if __name__ == "__main__":
    main()
