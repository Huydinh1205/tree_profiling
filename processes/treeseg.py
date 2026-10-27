"""Shared code for tree segmentation: models, ground/height, scoring, export, figures.

Models (see MODELS below):
  chm_watershed   Python. Canopy height model + variable-window tree tops + watershed.
  lidr_dalponte   R/lidR. Tree tops (lmf) + dalponte2016 crown growing on the CHM.
  lidr_li2012     R/lidR. Point-cloud region growing (Li et al. 2012), lidR defaults.
  segmentanytree  SegmentAnyTree v1 (NIBIO 2024), pretrained deep learning.
                  Runs in Docker and needs an NVIDIA GPU.
  external:<name> Any result produced elsewhere (LAS/LAZ with a per-point tree ID
                  field), e.g. SegmentAnyTree run on another machine or on Galaxy.

Every model returns one integer tree ID per input point (0 = not a tree), in the
same order as the input file. Everything downstream works from that.
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, time
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import laspy

HERE = Path(__file__).resolve().parent
R_SCRIPT = HERE / "segment_lidr.R"

# --------------------------------------------------------------------------- models
MODELS = {
    "chm_watershed": dict(
        label="CHM + watershed (Python)",
        kind="Classical, raster (CHM) based",
        detail="Canopy height model at 0.5 m, tree tops by variable-window local maxima "
               "(window = 3 + 0.2 x height m), crowns by marker-controlled watershed. "
               "Parameters tuned on the FOR-instance RMIT dev split.",
        needs="Python only (numpy, scipy, scikit-image)",
        licence="Our code",
        params=dict(ws_a=3.0, ws_b=0.2, res=0.5)),
    "lidr_dalponte": dict(
        label="lidR dalponte2016",
        kind="Classical, raster (CHM) based",
        detail="lidR: tree tops with lmf (window = 2 + 0.05 x height m), crowns grown with "
               "dalponte2016 on a 0.5 m CHM. Parameters tuned on the FOR-instance RMIT dev split.",
        needs="R + lidR (Rscript on PATH)",
        licence="lidR GPL-3",
        params=dict(ws_a=2.0, ws_b=0.05, res=0.5)),
    "lidr_li2012": dict(
        label="lidR li2012",
        kind="Classical, point-cloud based",
        detail="lidR: Li et al. (2012) region growing directly on the points, lidR default "
               "parameters (dt1 1.5, dt2 2, R 2, Zu 15). Slow on large clouds.",
        needs="R + lidR (Rscript on PATH)",
        licence="lidR GPL-3",
        params=dict(dt1=1.5, dt2=2.0)),
    "dalponte_py": dict(
        label="dalponte2016 (Python port)",
        kind="Classical, raster (CHM) based",
        detail="Line-by-line Python port of lidR's lmf + dalponte2016 (no R needed). Same CHM "
               "(0.5 m, p2r subcircle 0.15, 3x3 median), tree tops window = 2 + 0.05 x height m, "
               "th_seed 0.45, th_cr 0.55, max_cr 20 px.",
        needs="Python only (numba)",
        licence="GPL-3 (port of lidR)",
        params=dict(ws_a=2.0, ws_b=0.05, res=0.5)),
    "li2012_py": dict(
        label="li2012 (Python port)",
        kind="Classical, point-cloud based",
        detail="Line-by-line Python port of lidR's li2012 (Li et al. 2012) with lidR defaults "
               "(dt1 1.5, dt2 2, R 2, Zu 15, speed_up 10). No R needed. Slow on large clouds.",
        needs="Python only (numba)",
        licence="GPL-3 (port of lidR)",
        params=dict(dt1=1.5, dt2=2.0)),
    "segmentanytree": dict(
        label="SegmentAnyTree v1",
        kind="Deep learning, pretrained (no training needed)",
        detail="NIBIO 2024, sensor-agnostic 3D model. Run through the 3DTrees Docker image "
               "ghcr.io/3dtrees-earth/3dtrees_sat:1.1.0. Published F1 0.754 on the RMIT test plot.",
        needs="Docker + NVIDIA GPU (Linux or Windows WSL2)",
        licence="MIT",
        params=dict(image="ghcr.io/3dtrees-earth/3dtrees_sat:1.1.0")),
}

# Published results on the same RMIT test plot (IoU >= 0.5), for context only.
PUBLISHED_RMIT = [
    ("SegmentAnyTree v1 (Wielgosz et al. 2024)", 0.754, "MIT, usable"),
    ("ForestFormer3D (Xiang et al. 2025)", 0.822, "CC BY-NC 4.0, not usable commercially"),
    ("SegmentAnyTreeV2 (Wielgosz et al. 2026)", 0.861, "weights not released"),
]


class ModelUnavailable(RuntimeError):
    """Raised when a model cannot run on this machine (missing R, Docker, GPU...)."""


@dataclass
class Cloud:
    """Point cloud with height above ground, kept in input order."""
    path: Path
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    hag: np.ndarray
    ground: np.ndarray
    ground_source: str
    crs: object = None
    las: object = None

    @property
    def n(self): return len(self.x)


# --------------------------------------------------------------------------- ground / HAG
def csf_ground(x, y, z, cloth_res=0.5):
    try:
        import CSF
    except ImportError as e:
        raise SystemExit("Ground classification needs the CSF package: pip install cloth-simulation-filter") from e
    c = CSF.CSF(); c.params.bSloopSmooth = False; c.params.cloth_resolution = cloth_res
    c.params.rigidness = 2; c.params.class_threshold = 0.3; c.params.time_step = 0.65
    c.setPointCloud(np.c_[x - x.min(), y - y.min(), z])
    g, ng = CSF.VecInt(), CSF.VecInt()
    with open(os.devnull, "w") as dn:  # CSF prints progress to stdout
        old = os.dup(1); os.dup2(dn.fileno(), 1)
        try: c.do_filtering(g, ng, False)
        finally: os.dup2(old, 1); os.close(old)
    m = np.zeros(len(x), bool); m[np.asarray(g, int)] = True
    return m


def height_above_ground(x, y, z, ground):
    from scipy.interpolate import griddata
    from scipy.spatial import cKDTree
    gx, gy, gz = x[ground], y[ground], z[ground]
    k = np.floor(gx / 0.25).astype(np.int64) * 10**9 + np.floor(gy / 0.25).astype(np.int64)
    o = np.lexsort((gz, k)); first = np.r_[True, k[o][1:] != k[o][:-1]]
    gx, gy, gz = gx[o][first], gy[o][first], gz[o][first]
    dtm = griddata((gx, gy), gz, (x, y), method="linear")
    nan = np.isnan(dtm)
    if nan.any():
        _, i = cKDTree(np.c_[gx, gy]).query(np.c_[x[nan], y[nan]]); dtm[nan] = gz[i]
    return np.clip(z - dtm, 0, None)


def load_cloud(path, ground="auto", epsg=None) -> Cloud:
    """ground: 'auto' (file class 2 if >= 5% of points, else CSF), 'file' or 'csf'."""
    L = laspy.read(path)
    x, y, z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
    cls = np.asarray(L.classification)
    if ground == "file" or (ground == "auto" and (cls == 2).mean() >= 0.05):
        gm, src = cls == 2, "file class 2"
    else:
        gm, src = csf_ground(x, y, z), "CSF (computed)"
    if gm.sum() < 50:
        raise SystemExit(f"{path}: too few ground points ({gm.sum()}) to build a ground model")
    crs = L.header.parse_crs()
    if crs is None and epsg:
        from pyproj import CRS; crs = CRS.from_epsg(epsg)
    return Cloud(Path(path), x, y, z, height_above_ground(x, y, z, gm), gm, src, crs, L)


def write_xyz_las(path, x, y, z, classification=None, crs=None):
    """Write a plain LAS 1.4 (point format 6) with coordinates only, no labels/colours."""
    hdr = laspy.LasHeader(point_format=6, version="1.4")
    hdr.offsets = [np.floor(x.min()), np.floor(y.min()), np.floor(z.min())]
    hdr.scales = [0.001, 0.001, 0.001]
    if crs is not None:
        try: hdr.add_crs(crs)
        except Exception: pass
    L = laspy.LasData(hdr); L.x, L.y, L.z = x, y, z
    if classification is not None: L.classification = classification
    L.write(path)


# --------------------------------------------------------------------------- CHM model
def chm_grid(x, y, res):
    """Grid aligned to multiples of res (so results do not depend on the data extent)."""
    x0 = np.floor(x.min() / res) * res; y1 = np.ceil(y.max() / res) * res
    nx = int(np.floor((x.max() - x0) / res)) + 1; ny = int(np.floor((y1 - y.min()) / res)) + 1
    ix = np.clip(((x - x0) / res).astype(np.int64), 0, nx - 1)
    iy = np.clip(((y1 - y) / res).astype(np.int64), 0, ny - 1)
    return x0, y1, nx, ny, ix, iy


def make_chm(cloud: Cloud, res=0.5):
    from scipy import ndimage as ndi
    x0, y1, nx, ny, ix, iy = chm_grid(cloud.x, cloud.y, res)
    flat = iy * nx + ix
    chm = np.full(nx * ny, np.nan); o = np.argsort(cloud.hag)
    chm[flat[o]] = cloud.hag[o]                     # last write = highest point
    chm = chm.reshape(ny, nx)
    empty = float(np.isnan(chm).mean())
    chm = ndi.median_filter(np.nan_to_num(chm, nan=0.0), size=3)
    return chm, (x0, y1, res), (ix, iy), empty


def run_chm_watershed(cloud: Cloud, ws_a=3.0, ws_b=0.2, res=0.5, hmin=2.0, crown_frac=0.3, **_):
    from scipy import ndimage as ndi
    from skimage.segmentation import watershed
    chm, _, (ix, iy), _ = make_chm(cloud, res)
    ny, nx = chm.shape
    cand = np.argwhere((chm == ndi.maximum_filter(chm, size=3)) & (chm >= hmin))
    hs = chm[cand[:, 0], cand[:, 1]]; o = np.argsort(-hs); cand, hs = cand[o], hs[o]
    rad = np.maximum(1.5, ws_a + ws_b * hs) / 2 / res
    taken = np.zeros(chm.shape, bool); tops = []
    for (r, c), h, rp in zip(cand, hs, rad):
        if taken[r, c]: continue
        tops.append((r, c, h)); R = int(np.ceil(rp))
        r0, r1, c0, c1 = max(0, r - R), min(ny, r + R + 1), max(0, c - R), min(nx, c + R + 1)
        yy, xx = np.ogrid[r0:r1, c0:c1]
        taken[r0:r1, c0:c1] |= (yy - r) ** 2 + (xx - c) ** 2 <= rp ** 2
    markers = np.zeros(chm.shape, np.int32)
    for i, (r, c, _) in enumerate(tops, 1): markers[r, c] = i
    lab = watershed(-chm, markers, mask=chm >= hmin)
    toph = np.r_[0.0, [t[2] for t in tops]]
    lab[(lab > 0) & (chm < crown_frac * toph[lab])] = 0
    size = np.bincount(lab.ravel(), minlength=len(tops) + 1); lab[size[lab] < 4] = 0
    ids = lab[iy, ix].astype(np.int64); ids[cloud.hag < 0.5] = 0
    return ids


# --------------------------------------------------------------------------- lidR models
def _rscript():
    """Find Rscript: RSCRIPT env var, PATH, or the usual Windows install folders
    (admin install in Program Files, or per-user install without admin rights)."""
    if os.environ.get("RSCRIPT") and os.path.exists(os.environ["RSCRIPT"]):
        return os.environ["RSCRIPT"]
    exe = shutil.which("Rscript")
    if exe:
        return exe
    roots = [r"C:\Program Files\R", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "R"),
             os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs", "R"),
             os.path.join(os.path.expanduser("~"), "Documents", "R")]
    for root in roots:
        if os.path.isdir(root):
            for v in sorted(os.listdir(root), reverse=True):
                c = os.path.join(root, v, "bin", "Rscript.exe")
                if os.path.exists(c):
                    return c
    raise ModelUnavailable("Rscript not found. Install R (per-user install works without admin) and the lidR "
                           "package, or set the RSCRIPT environment variable to Rscript.exe")


def run_lidr(cloud: Cloud, method, a, b, res=0.5):
    exe = _rscript()
    chk = subprocess.run([exe, "-e", "suppressMessages(library(lidR))"], capture_output=True, text=True)
    if chk.returncode != 0:
        raise ModelUnavailable("R found but lidR is not installed: in R run install.packages(c('lidR','RCSF','terra'))")
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "in.las"
        cls = np.where(cloud.ground, 2, 1).astype(np.uint8)   # hand over our ground so all models share it
        write_xyz_las(inp, cloud.x, cloud.y, cloud.z, cls)
        out = Path(td) / "out"
        r = subprocess.run([exe, str(R_SCRIPT), str(inp), str(out), method, str(a), str(b), str(res), "keep"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"lidR {method} failed:\n{r.stderr[-2000:]}")
        ids = np.loadtxt(str(out) + "_ids.txt", dtype=np.int64)
    if len(ids) != cloud.n:
        raise RuntimeError(f"lidR returned {len(ids)} IDs for {cloud.n} points")
    return ids


def run_lidr_dalponte(cloud, ws_a=2.0, ws_b=0.05, res=0.5, **_):
    return run_lidr(cloud, "dalponte", ws_a, ws_b, res)


def run_lidr_li2012(cloud, dt1=1.5, dt2=2.0, max_points=3_000_000, **_):
    if cloud.n > max_points:
        raise ModelUnavailable(f"li2012 skipped: {cloud.n:,} points > {max_points:,} (very slow). "
                               "Crop the cloud or pass --allow-slow")
    return run_lidr(cloud, "li2012", dt1, dt2)


# --------------------------------------------------------------------------- SegmentAnyTree
def map_ids_by_nearest(cloud: Cloud, px, py, pz, pid, max_dist=0.5):
    """Map tree IDs from another point set (e.g. a subsampled model output) to our points."""
    from scipy.spatial import cKDTree
    d, i = cKDTree(np.c_[px, py, pz]).query(np.c_[cloud.x, cloud.y, cloud.z], distance_upper_bound=max_dist)
    ok = np.isfinite(d)
    ids = np.zeros(cloud.n, np.int64); ids[ok] = pid[i[ok]]
    return ids, float(ok.mean())


def parse_external(spec):
    """'name=path' or 'name=path:FIELD' (Windows drive letters are handled)."""
    name, rest = spec.split("=", 1)
    path, field = rest, None
    head, sep, tail = rest.rpartition(":")
    if sep and head.lower().endswith((".las", ".laz")) and "/" not in tail and "\\" not in tail:
        path, field = head, tail
    return name, path, field


def read_external(cloud: Cloud, path, field=None):
    """Load per-point tree IDs from a LAS/LAZ produced by another tool."""
    L = laspy.read(path)
    names = list(L.point_format.extra_dimension_names) + list(L.point_format.dimension_names)
    if field is None:
        prefs = ["PredInstance_SAT", "PredInstance", "preds_instance_segmentation", "treeID", "TreeID", "instance"]
        field = next((f for f in prefs if f in names), None) or \
                next((f for f in names if "instance" in f.lower() or "treeid" in f.lower()), None)
    if field is None or field not in names:
        raise RuntimeError(f"{path}: no tree ID field found. Fields: {names}")
    pid = np.asarray(L[field]).astype(np.int64)
    px, py, pz = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
    if len(px) == cloud.n and np.allclose(px[:1000], cloud.x[:1000]) and np.allclose(pz[-1000:], cloud.z[-1000:]):
        ids, cov = pid, 1.0
    else:
        ids, cov = map_ids_by_nearest(cloud, px, py, pz, pid)
    ids[ids < 0] = 0
    return ids, field, cov


def run_segmentanytree(cloud: Cloud, image="ghcr.io/3dtrees-earth/3dtrees_sat:1.1.0", **_):
    docker = shutil.which("docker")
    if not docker:
        raise ModelUnavailable("Docker not found. SegmentAnyTree needs Docker + an NVIDIA GPU "
                               "(on Windows: WSL2 + Docker Desktop). Or run it elsewhere and pass "
                               "the result with --external sat=<result.laz>")
    gpu = subprocess.run([docker, "run", "--rm", "--gpus", "all", "hello-world"], capture_output=True, text=True)
    if gpu.returncode != 0:
        raise ModelUnavailable("Docker cannot use the GPU (--gpus all failed). Install the NVIDIA container toolkit.")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td); inp = td / "input.laz"; outd = td / "out"; outd.mkdir()
        write_xyz_las(inp, cloud.x, cloud.y, cloud.z, crs=cloud.crs)
        cmd = [docker, "run", "--rm", "--gpus", "all", "-v", f"{inp}:/in/input.laz:ro", "-v", f"{outd}:/out",
               image, "python3.8", "/src/run.py", "--dataset-path", "/in/input.laz", "--output-dir", "/out",
               "--log-file", "false"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"SegmentAnyTree failed:\n{r.stderr[-3000:]}")
        res = sorted((outd / "03_output_SAT" / "final_results").glob("*.la[sz]")) or sorted(outd.rglob("*.la[sz]"))
        if not res:
            raise RuntimeError(f"SegmentAnyTree produced no LAS/LAZ in {outd}")
        ids, _, cov = read_external(cloud, res[0])
    return ids


# --------------------------------------------------------------------------- Python ports of lidR
def lidr_style_chm(cloud: Cloud, res=0.5, subcircle=0.15):
    """CHM like lidR rasterize_canopy(p2r(subcircle)) + terra::focal(w=3, median, na.rm=TRUE)."""
    from numpy.lib.stride_tricks import sliding_window_view
    x0, y1, nx, ny, _, _ = chm_grid(cloud.x, cloud.y, res)
    chm = np.full(nx * ny, -np.inf)
    # lidR p2r(subcircle): each point is replaced by 8 points on a circle (the centre itself is not used)
    offs = [(subcircle * np.cos(a), subcircle * np.sin(a)) for a in np.arange(8) * np.pi / 4] if subcircle > 0 else [(0.0, 0.0)]
    for dx, dy in offs:
        ix = np.floor((cloud.x + dx - x0) / res).astype(np.int64); iy = np.floor((y1 - cloud.y - dy) / res).astype(np.int64)
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        np.maximum.at(chm, iy[ok] * nx + ix[ok], cloud.hag[ok])
    chm = chm.reshape(ny, nx); chm[np.isinf(chm)] = np.nan
    pad = np.pad(chm, 1, constant_values=np.nan)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(sliding_window_view(pad, (3, 3)).reshape(ny, nx, 9), axis=2)
    ix = np.clip(((cloud.x - x0) / res).astype(np.int64), 0, nx - 1)
    iy = np.clip(((y1 - cloud.y) / res).astype(np.int64), 0, ny - 1)
    return med, (x0, y1, res), (ix, iy)


def run_dalponte_py(cloud: Cloud, ws_a=2.0, ws_b=0.05, res=0.5, max_cr=20, **_):
    try:
        import lidr_python as LP
    except ImportError as e:
        raise ModelUnavailable(f"numba not installed ({e}): pip install numba")
    chm, (x0, y1, r), (ix, iy) = lidr_style_chm(cloud, res)
    ny, nx = chm.shape
    rr, cc = np.nonzero(~np.isnan(chm))                       # cell centres, row-major like lidR
    cx = x0 + (cc + 0.5) * r; cy = y1 - (rr + 0.5) * r; cz = chm[rr, cc]
    tops = LP.lmf(cx, cy, cz, ws_fun=lambda h: ws_a + ws_b * h, hmin=2.0)
    seeds = np.zeros(chm.shape, np.int64); seeds[rr[tops], cc[tops]] = np.arange(1, tops.sum() + 1)
    crowns = LP.dalponte2016(chm, seeds, th_tree=2.0, th_seed=0.45, th_cr=0.55, max_cr=max_cr)
    ids = crowns[iy, ix].astype(np.int64); ids[cloud.hag < 0.5] = 0
    return ids


def run_li2012_py(cloud: Cloud, dt1=1.5, dt2=2.0, max_points=3_000_000, **_):
    if cloud.n > max_points:
        raise ModelUnavailable(f"li2012 skipped: {cloud.n:,} points > {max_points:,} (very slow). "
                               "Crop the cloud or pass --allow-slow")
    try:
        import lidr_python as LP
    except ImportError as e:
        raise ModelUnavailable(f"numba not installed ({e}): pip install numba")
    ids = LP.li2012(cloud.x, cloud.y, cloud.hag, dt1=dt1, dt2=dt2).astype(np.int64)
    ids[cloud.hag < 0.5] = 0
    return ids


RUNNERS = {"chm_watershed": run_chm_watershed, "lidr_dalponte": run_lidr_dalponte,
           "lidr_li2012": run_lidr_li2012, "segmentanytree": run_segmentanytree,
           "dalponte_py": run_dalponte_py, "li2012_py": run_li2012_py}


def run_model(name, cloud: Cloud, allow_slow=False):
    """Returns (ids, seconds, note). Raises ModelUnavailable if it cannot run here."""
    p = dict(MODELS[name]["params"])
    if allow_slow: p["max_points"] = 10**12
    t0 = time.time(); ids = RUNNERS[name](cloud, **p)
    return relabel(ids), time.time() - t0


def relabel(ids):
    """Consecutive IDs 1..N, 0 stays 0."""
    u, inv = np.unique(np.asarray(ids).astype(np.int64), return_inverse=True)
    new = np.zeros(len(u), np.int64); pos = u > 0; new[pos] = np.arange(1, pos.sum() + 1)
    return new[inv.ravel()]


# --------------------------------------------------------------------------- per-tree table
def tree_table(cloud: Cloud, ids, res=0.5, voxel=0.5):
    """One row per tree with the LiDAR structural profile of the pilot document (section 4.2).

    Measured from the points assigned to each tree (heights = above ground):
      top_x, top_y, height_m     highest point of the tree
      crown_base_m               crown-base estimate: 10th percentile height of the tree's points
                                 above 1 m (proxy, not a field crown-base measurement)
      crown_depth_m              height_m - crown_base_m
      crown_area_m2              occupied 0.5 m cells of points above 1/3 of tree height
      crown_diam_m               diameter of a circle with that area
      crown_width_max_m          longest side of the minimum rotated rectangle around the crown points
      crown_elongation           long side / short side of that rectangle (1 = round)
      crown_volume_m3            geometric volume: 3D convex hull of points above crown base
      foliage_volume_m3          occupied 0.5 m voxels above crown base (foliage-occupied volume)
      canopy_density_pct         occupied 0.5 m voxels / voxels inside the crown hull x 100
      h_p25_m, h_p50_m, h_p75_m  vertical profile: height percentiles of the tree's points
      height_sd_m                within-crown height variability (points above crown base)
      nn_spacing_m               distance to the nearest other tree top
      point_density_m2           points per m2 of crown area
      n_points                   points assigned to the tree
    """
    from scipy.spatial import ConvexHull, cKDTree
    from scipy.spatial import QhullError
    t = ids > 0
    if not t.any():
        return []
    x0, y1, nx, ny, ix, iy = chm_grid(cloud.x, cloud.y, res)
    idx = np.nonzero(t)[0]
    o = idx[np.lexsort((cloud.hag[idx], ids[idx]))]
    I = ids[o]
    starts = np.r_[0, np.nonzero(I[1:] != I[:-1])[0] + 1]; ends = np.r_[starts[1:], len(I)]
    tops = np.array([[cloud.x[o[e - 1]], cloud.y[o[e - 1]]] for e in ends])
    nn = cKDTree(tops).query(tops, k=2)[0][:, 1] if len(tops) > 1 else np.array([np.nan])
    rows = []
    for k, (s0, e0) in enumerate(zip(starts, ends)):
        p = o[s0:e0]; tid = int(I[s0])
        X, Y, H = cloud.x[p], cloud.y[p], cloud.hag[p]
        top = p[-1]; h = float(cloud.hag[top])
        up = H >= h / 3
        area = np.unique(iy[p][up] * nx + ix[p][up]).size * res * res
        hb = H[H > 1.0]
        base = float(np.percentile(hb, 10)) if hb.size else 0.0
        cr = H >= base
        xyz = np.c_[X[cr], Y[cr], H[cr]]
        try:
            vol = float(ConvexHull(xyz).volume) if len(xyz) >= 5 else 0.0
        except QhullError:
            vol = 0.0
        occ = np.unique(np.floor(xyz / voxel).astype(np.int64), axis=0)
        vox = occ.shape[0] * voxel ** 3
        # density: occupied voxels / voxels inside the crown hull (or occupied), so it stays <= 100%
        dens = None
        if vol > 0:
            from scipy.spatial import Delaunay
            lo, hi = occ.min(0), occ.max(0)
            g = np.stack(np.meshgrid(*[np.arange(l, h_ + 1) for l, h_ in zip(lo, hi)], indexing="ij"), -1).reshape(-1, 3)
            inside = Delaunay(xyz).find_simplex((g + 0.5) * voxel) >= 0
            occ_set = np.zeros(len(g), bool)
            occ_set[np.ravel_multi_index((occ - lo).T, tuple(hi - lo + 1))] = True
            dens = 100.0 * occ_set.sum() / max((inside | occ_set).sum(), 1)
        xy = np.c_[X[up], Y[up]]
        wmax, elong = np.nan, np.nan
        if len(xy) >= 3:
            try:
                hull = xy[ConvexHull(xy).vertices]
                best = None
                for i in range(len(hull)):                     # minimum rotated rectangle
                    e = hull[(i + 1) % len(hull)] - hull[i]; n_ = np.hypot(*e)
                    if n_ == 0: continue
                    u = e / n_; v = np.array([-u[1], u[0]])
                    a_, b_ = hull @ u, hull @ v
                    w1, w2 = a_.max() - a_.min(), b_.max() - b_.min()
                    if best is None or w1 * w2 < best[0]: best = (w1 * w2, max(w1, w2), min(w1, w2))
                if best: wmax, elong = best[1], best[1] / best[2] if best[2] > 0 else np.nan
            except QhullError:
                pass
        r2 = lambda v: round(float(v), 2) if v == v else None
        rows.append(dict(tree_id=tid, top_x=r2(cloud.x[top]), top_y=r2(cloud.y[top]), height_m=r2(h),
                         crown_base_m=r2(base), crown_depth_m=r2(h - base),
                         crown_area_m2=r2(area), crown_diam_m=r2(2 * np.sqrt(area / np.pi)),
                         crown_width_max_m=r2(wmax), crown_elongation=r2(elong),
                         crown_volume_m3=r2(vol), foliage_volume_m3=r2(vox),
                         canopy_density_pct=r2(dens) if dens is not None else None,
                         h_p25_m=r2(np.percentile(H, 25)), h_p50_m=r2(np.percentile(H, 50)), h_p75_m=r2(np.percentile(H, 75)),
                         height_sd_m=r2(H[cr].std()), nn_spacing_m=r2(nn[k]) if len(tops) > 1 else None,
                         point_density_m2=r2(len(p) / area) if area > 0 else None, n_points=int(len(p))))
    return rows


# --------------------------------------------------------------------------- scoring
def match_iou(gt, pred, keep):
    g = gt[keep]; p = pred[keep]
    gsz = np.bincount(g); psz = np.bincount(p)
    both = (g > 0) & (p > 0)
    pairs, inter = np.unique(np.c_[g[both], p[both]], axis=0, return_counts=True)
    iou = inter / (gsz[pairs[:, 0]] + psz[pairs[:, 1]] - inter)
    o = np.argsort(-iou); used_g, used_p, m = set(), set(), []
    for k in o:
        if iou[k] < 0.5: break
        a, b = pairs[k]
        if a in used_g or b in used_p: continue
        used_g.add(a); used_p.add(b); m.append((a, b, iou[k]))
    n_gt = int((np.unique(g) > 0).sum()); n_pr = int((np.unique(p) > 0).sum())
    best = np.zeros(gsz.size)
    np.maximum.at(best, pairs[:, 0], iou)
    gt_ids = np.unique(g[g > 0])
    return m, n_gt, n_pr, float(best[gt_ids].mean()) if len(gt_ids) else 0.0


def tops_of(ids, x, y, h):
    o = np.lexsort((h, ids)); I = ids[o]; last = np.r_[I[1:] != I[:-1], True]
    s = o[last]; s = s[ids[s] > 0]
    return ids[s], x[s], y[s], h[s]


def score(gt, pred, keep, x, y, h, max_xy=2.0, max_dz=2.0):
    """Benchmark metrics: IoU>=0.5 instance matching + tree-top detection."""
    from scipy.optimize import linear_sum_assignment
    from scipy.spatial import cKDTree
    m, n_gt, n_pr, cov = match_iou(gt, pred, keep)
    tp = len(m); P = tp / n_pr if n_pr else 0.0; R = tp / n_gt if n_gt else 0.0
    out = dict(gt_trees=n_gt, pred_trees=n_pr, matched=tp, precision=P, recall=R,
               f1=2 * P * R / (P + R) if P + R else 0.0, coverage=cov)
    gi, gx, gy, gh = tops_of(np.where(keep, gt, 0), x, y, h)
    pi, px, py, ph = tops_of(np.where(keep, pred, 0), x, y, h)
    det = np.zeros(len(gi), bool); dh = np.array([])
    if len(gi) and len(pi):
        D = np.hypot(gx[:, None] - px[None], gy[:, None] - py[None]); Z = np.abs(gh[:, None] - ph[None])
        C = np.where((D <= max_xy) & (Z <= max_dz), D, 1e6); r, c = linear_sum_assignment(C); ok = C[r, c] < 1e6
        det[r[ok]] = True; dh = ph[c[ok]] - gh[r[ok]]
    dP = det.sum() / len(pi) if len(pi) else 0.0; dR = det.mean() if len(gi) else 0.0
    out.update(det_precision=dP, det_recall=dR, det_f1=2 * dP * dR / (dP + dR) if dP + dR else 0.0,
               det_height_mae_m=float(np.abs(dh).mean()) if len(dh) else np.nan)
    # trees under another crown (another point > 1 m above their top within 0.75 m)
    if len(gi):
        kd = cKDTree(np.c_[x, y]); over = np.array([h[kd.query_ball_point([a, b], 0.75)].max() > c + 1.0
                                                   for a, b, c in zip(gx, gy, gh)])
        out.update(overtopped_share=float(over.mean()),
                   det_rate_open=float(det[~over].mean()) if (~over).any() else np.nan,
                   det_rate_overtopped=float(det[over].mean()) if over.any() else np.nan)
    return out


# --------------------------------------------------------------------------- export
def export_model(cloud: Cloud, ids, outdir: Path, res=0.5, site="SITE", model=""):
    """Crowns (GPKG polygons), tree tops (GPKG points), LAZ with treeID + HAG.
    Each tree gets tree_uid = <site>_TREE00042 and the model name, as in the pilot data model."""
    import geopandas as gpd
    from shapely.geometry import Point, shape
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    outdir.mkdir(parents=True, exist_ok=True)
    rows = tree_table(cloud, ids, res)
    tag = "".join(ch if ch.isalnum() else "_" for ch in str(site)).strip("_").upper() or "SITE"
    rows = [{"tree_uid": f"{tag}_TREE{r['tree_id']:05d}", "model": model, **r} for r in rows]
    # simple quality flag: very few points, or tree top within 2 m of the data edge (crown may be cut)
    bx = (cloud.x.min(), cloud.y.min(), cloud.x.max(), cloud.y.max())
    for r in rows:
        f = []
        if r["n_points"] < 20: f.append("few_points")
        if min(r["top_x"] - bx[0], r["top_y"] - bx[1], bx[2] - r["top_x"], bx[3] - r["top_y"]) < 2: f.append("edge")
        r["qc_flag"] = ",".join(f) or "ok"
    x0, y1, nx, ny, ix, iy = chm_grid(cloud.x, cloud.y, res)
    # crown raster: each cell gets the tree that owns the highest point in it
    lab = np.zeros(nx * ny, np.int32); t = ids > 0; o = np.argsort(cloud.hag[t])
    lab[(iy[t] * nx + ix[t])[o]] = ids[t][o]; lab = lab.reshape(ny, nx)
    tr = from_origin(x0, y1, res, res)
    polys = {}
    for geom, v in shapes(lab, mask=lab > 0, transform=tr):
        v = int(v); g = shape(geom); polys[v] = polys[v].union(g) if v in polys else g
    crs = cloud.crs
    crown_rows = [{**r, "geometry": polys[r["tree_id"]]} for r in rows if r["tree_id"] in polys]
    gpd.GeoDataFrame(crown_rows, geometry="geometry", crs=crs).to_file(outdir / "trees_crowns.gpkg", driver="GPKG")
    gpd.GeoDataFrame([{**r, "geometry": Point(r["top_x"], r["top_y"])} for r in rows],
                     geometry="geometry", crs=crs).to_file(outdir / "trees_tops.gpkg", driver="GPKG")
    L = laspy.read(cloud.path)
    names = set(L.point_format.extra_dimension_names)
    if "treeID" not in names: L.add_extra_dim(laspy.ExtraBytesParams(name="treeID", type=np.int32))
    if "HAG" not in names: L.add_extra_dim(laspy.ExtraBytesParams(name="HAG", type=np.float32))
    L["treeID"] = ids.astype(np.int32); L["HAG"] = cloud.hag.astype(np.float32)
    L.write(outdir / "points_treeid.laz")
    return rows, lab


def write_rasters(cloud: Cloud, outdir: Path, res=0.5):
    import rasterio
    from rasterio.transform import from_origin
    chm, (x0, y1, r), _, empty = make_chm(cloud, res)
    prof = dict(driver="GTiff", height=chm.shape[0], width=chm.shape[1], count=1, dtype="float32",
                transform=from_origin(x0, y1, r, r), crs=cloud.crs.to_wkt() if cloud.crs else None,
                compress="deflate", nodata=-9999)
    with rasterio.open(outdir / "chm.tif", "w", **prof) as d: d.write(chm.astype("float32"), 1)
    # ground model from ground points (lowest per cell)
    _, _, nx, ny, ix, iy = chm_grid(cloud.x, cloud.y, res)
    g = cloud.ground; dtm = np.full(nx * ny, np.nan); o = np.argsort(-cloud.z[g])
    dtm[(iy[g] * nx + ix[g])[o]] = cloud.z[g][o]
    with rasterio.open(outdir / "dtm.tif", "w", **prof) as d:
        d.write(np.nan_to_num(dtm.reshape(ny, nx), nan=-9999).astype("float32"), 1)
    return chm, (x0, y1, r), empty


def density(cloud: Cloud):
    cells = np.unique(np.floor(cloud.x).astype(np.int64) * 10**9 + np.floor(cloud.y).astype(np.int64)).size
    return cloud.n / cells, cells


# --------------------------------------------------------------------------- figures
def random_colours(n, seed=7):
    rng = np.random.default_rng(seed); c = rng.random((n + 1, 3)) * 0.75 + 0.2; c[0] = [0.88, 0.88, 0.88]
    return c


def fig_topviews(panels, path, title=None, max_points=400_000):
    """panels: list of (title, x, y, h, ids or None). Top-down view, highest point drawn last."""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    k = len(panels); nc = min(k, 3); nr = int(np.ceil(k / nc))
    fig, axs = plt.subplots(nr, nc, figsize=(5.4 * nc, 5.4 * nr), squeeze=False)
    for ax in axs.ravel()[k:]: ax.axis("off")
    for ax, (t, x, y, h, ids) in zip(axs.ravel(), panels):
        sel = np.arange(len(x))
        if len(sel) > max_points: sel = np.random.default_rng(0).choice(sel, max_points, replace=False)
        sel = sel[np.argsort(h[sel])]
        x0, y0 = x.min(), y.min()
        if ids is None:
            ax.scatter(x[sel] - x0, y[sel] - y0, c=h[sel], s=0.3, cmap="viridis", rasterized=True)
        else:
            col = random_colours(int(ids.max()))[ids[sel]]
            ax.scatter(x[sel] - x0, y[sel] - y0, c=col, s=0.3, rasterized=True)
        ax.set_title(t, fontsize=10); ax.set_aspect("equal"); ax.set_xlabel("m"); ax.tick_params(labelsize=8)
    if title: fig.suptitle(title)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def fig_sideview(panels, x, y, h, path, width=4.0):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    yc = np.median(y); s = np.abs(y - yc) < width / 2
    k = len(panels); fig, axs = plt.subplots(k, 1, figsize=(13, 2.6 * k), sharex=True, squeeze=False)
    for ax, (t, ids) in zip(axs[:, 0], panels):
        col = random_colours(int(ids.max()) if ids.max() > 0 else 1)[ids[s]]
        ax.scatter(x[s] - x.min(), h[s], c=col, s=0.5, rasterized=True)
        ax.set_title(f"{t}  (side view, {width:.0f} m slice)", fontsize=10); ax.set_ylabel("height (m)")
    axs[-1, 0].set_xlabel("m"); fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def fig_crowns_on_chm(chm, geo, crown_rasters, path):
    """CHM map with crown outlines, one panel per model."""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from skimage.segmentation import find_boundaries
    x0, y1, r = geo; ny, nx = chm.shape; ext = [0, nx * r, 0, ny * r]
    k = len(crown_rasters); fig, axs = plt.subplots(1, k, figsize=(6 * k, 6 * ny / max(nx, 1) + 0.8), squeeze=False)
    vmax = np.percentile(chm[chm > 0], 99) if (chm > 0).any() else 1
    for ax, (t, lab) in zip(axs[0], crown_rasters):
        im = ax.imshow(chm, cmap="Greens", vmin=0, vmax=vmax, extent=ext, interpolation="nearest")
        b = find_boundaries(lab, mode="inner"); ov = np.zeros((*b.shape, 4)); ov[b] = [0.85, 0.1, 0.1, 1]
        ax.imshow(ov, extent=ext, interpolation="nearest")
        ax.set_title(t, fontsize=10); ax.set_xlabel("m"); ax.tick_params(labelsize=8)
    fig.colorbar(im, ax=axs[0].tolist(), shrink=0.7, label="canopy height (m)")
    fig.savefig(path, dpi=110, bbox_inches="tight"); plt.close(fig)


def fig_height_hist(tables, path):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4))
    for name, rows in tables.items():
        if rows: ax.hist([r["height_m"] for r in rows], bins=30, histtype="step", linewidth=1.8, label=name)
    ax.set_xlabel("tree height (m)"); ax.set_ylabel("number of trees"); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def md_table(rows, cols, fmt=None):
    fmt = fmt or {}
    s = "| " + " | ".join(c for c, _ in cols) + " |\n|" + "---|" * len(cols) + "\n"
    for r in rows:
        s += "| " + " | ".join((fmt.get(k, "{}").format(r[k]) if r.get(k) is not None and r.get(k) == r.get(k) else "n/a")
                               for _, k in cols) + " |\n"
    return s


def fig_rgb_trees(cloud: Cloud, preds, path, res=0.15, separate_dir=None):
    """Top view in true colour (from the point RGB) next to the same view with each
    tree filled in its own colour, one panel per model. Needs a colourised cloud."""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    L = cloud.las
    rgb = np.c_[np.asarray(L.red), np.asarray(L.green), np.asarray(L.blue)].astype(float)
    rgb /= 65535.0 if rgb.max() > 255 else 255.0
    x0, y1, nx, ny, ix, iy = chm_grid(cloud.x, cloud.y, res)
    flat = iy * nx + ix
    o = np.argsort(cloud.z)                                   # highest point wins the cell
    img = np.ones((nx * ny, 3)); img[flat[o]] = rgb[o]
    # fill small gaps (cells with no point) from the nearest filled cell, max 3 cells away
    from scipy import ndimage as ndi
    empty = np.ones(nx * ny, bool); empty[flat] = False; empty = empty.reshape(ny, nx)
    d, (ri, ci) = ndi.distance_transform_edt(empty, return_indices=True)
    im3 = img.reshape(ny, nx, 3); fill = empty & (d <= 3)
    im3[fill] = im3[ri[fill], ci[fill]]; img = im3.reshape(-1, 3)
    lo, hi = np.percentile(img[img < 1].reshape(-1, 3) if (img < 1).any() else img, [2, 98], axis=0)
    img = np.clip((img - lo) / np.maximum(hi - lo, 1e-6), 0, 1).reshape(ny, nx, 3)
    # one image per panel: original colour + one per model
    if separate_dir is not None:
        separate_dir = Path(separate_dir); separate_dir.mkdir(parents=True, exist_ok=True)
        ext1 = [0, nx * res, 0, ny * res]
        def one(im, title, fname):
            f, a = plt.subplots(figsize=(11, 11 * ny / nx + 0.7))
            a.imshow(im, extent=ext1, interpolation="nearest"); a.set_title(title, fontsize=14)
            a.set_xlabel("m"); a.tick_params(labelsize=9); f.tight_layout(); f.savefig(separate_dir / fname, dpi=150); plt.close(f)
        one(img, "Original", "1_original.png")
        for i, (name, ids) in enumerate(preds.items(), 2):
            lab = np.zeros(nx * ny, np.int64); lab[flat[o]] = ids[o]; lab = lab.reshape(ny, nx)
            lab[fill] = lab[ri[fill], ci[fill]]
            col = random_colours(int(lab.max()) if lab.max() > 0 else 1, seed=11)
            over = img.copy(); m = lab > 0; over[m] = 0.25 * img[m] + 0.75 * col[lab[m]]
            safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
            one(over, f"{name}: {len(np.unique(ids[ids > 0]))} trees", f"{i}_{safe}.png")
    k = 1 + len(preds); nc = min(k, 2); nr = int(np.ceil(k / nc))
    fig, axs = plt.subplots(nr, nc, figsize=(9 * nc, 9 * ny / nx * nr + 0.6), squeeze=False)
    for ax in axs.ravel()[k:]: ax.axis("off")
    ext = [0, nx * res, 0, ny * res]
    axs.ravel()[0].imshow(img, extent=ext, interpolation="nearest"); axs.ravel()[0].set_title("Point cloud colour (top view)")
    for ax, (name, ids) in zip(axs.ravel()[1:], preds.items()):
        lab = np.zeros(nx * ny, np.int64); lab[flat[o]] = ids[o]; lab = lab.reshape(ny, nx)
        lab[fill] = lab[ri[fill], ci[fill]]
        col = random_colours(int(lab.max()) if lab.max() > 0 else 1, seed=11)
        over = img.copy(); m = lab > 0
        over[m] = 0.25 * img[m] + 0.75 * col[lab[m]]
        ax.imshow(over, extent=ext, interpolation="nearest")
        ax.set_title(f"{name}: {len(np.unique(ids[ids > 0]))} trees")
    for ax in axs.ravel()[:k]: ax.set_xlabel("m"); ax.tick_params(labelsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)
