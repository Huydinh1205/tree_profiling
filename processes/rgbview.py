"""Interactive true-colour viewer for the evaluation report (used by aggregate_benchmarks.py).

For a labelled plot whose .las has real RGB (e.g. CedarCypress3D Kokonoe, DJI Zenmuse L2), it makes a top view
in true colour and one transparent layer per segmentation: the reference trees (ground truth) and each model.
In report.html, buttons put one layer on top of the photo-like view; a slider sets how strong the colours are.
Each tree is filled in its own colour with a white outline, so crowns that were merged or split stand out.
On the model layers a yellow line marks the labelled area (only trees inside it are scored).

Everything is embedded in the HTML (JPEG for the view, PNG with transparency for the layers).
"""
import base64, html as H, io
from pathlib import Path
import numpy as np
import laspy

RES = 0.1          # m per pixel
CSS = (".rgbv{margin:8px 0 18px}.rgbv .btns{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}"
       ".rgbv button{border:1px solid #1F3864;background:#fff;color:#1F3864;border-radius:4px;padding:5px 10px;"
       "cursor:pointer;font-size:13px}.rgbv button.on{background:#1F3864;color:#fff}"
       ".rgbv .stage{position:relative;display:inline-block;max-width:100%}.rgbv .stage img{display:block;width:640px;"
       "max-width:100%;height:auto}.rgbv .stage img.lay{position:absolute;left:0;top:0;opacity:.6}"
       ".rgbv .hint{font-size:12px;color:#555}.rgbv input[type=range]{vertical-align:middle;width:160px}")
JS = """<script>
function rgbvShow(id, k){var v=document.getElementById(id);
 v.querySelectorAll('img.lay').forEach(function(e){e.style.display=(e.dataset.k===k)?'block':'none';});
 v.querySelectorAll('button').forEach(function(b){b.classList.toggle('on', b.dataset.k===k);});}
function rgbvAlpha(id, a){document.getElementById(id).querySelectorAll('img.lay').forEach(function(e){e.style.opacity=a;});}
</script>"""


def has_rgb(las_path):
    with laspy.open(las_path) as r:
        return "red" in r.header.point_format.dimension_names


def _b64(im, fmt, **kw):
    buf = io.BytesIO(); im.save(buf, fmt, **kw); return base64.b64encode(buf.getvalue()).decode()


def _grid(x, y):
    x0, y1 = x.min(), y.max()
    nx = int(np.ceil((x.max() - x0) / RES)) + 1; ny = int(np.ceil((y1 - y.min()) / RES)) + 1
    ix = np.clip(((x - x0) / RES).astype(int), 0, nx - 1); iy = np.clip(((y1 - y) / RES).astype(int), 0, ny - 1)
    return nx, ny, iy * nx + ix


def _fill(arr2d, empty, maxd):
    """Copy values into empty cells from the nearest filled cell, at most maxd cells away."""
    from scipy import ndimage as ndi
    if not empty.any() or empty.all(): return arr2d, empty
    d, (ri, ci) = ndi.distance_transform_edt(empty, return_indices=True)
    f = empty & (d <= maxd); arr2d = arr2d.copy(); arr2d[f] = arr2d[ri[f], ci[f]]
    return arr2d, empty & ~f


def _true_colour(x, y, z, rgb):
    nx, ny, flat = _grid(x, y)
    o = np.argsort(z)                                        # the highest point wins its cell
    img = np.zeros((nx * ny, 3)); img[flat[o]] = rgb[o]
    empty = np.ones(nx * ny, bool); empty[flat] = False
    img, left = _fill(img.reshape(ny, nx, 3), empty.reshape(ny, nx), 4)
    v = img[~left] if (~left).any() else img.reshape(-1, 3)
    lo, hi = np.percentile(v, [1, 99], axis=0)               # simple contrast stretch
    img = np.clip((img - lo) / np.maximum(hi - lo, 1e-6), 0, 1); img[left] = 1.0
    from PIL import Image
    return Image.fromarray((img * 255).astype(np.uint8)), nx, ny


def _scored_area(x, y, gt, nx, ny):
    """Outline (bool image) of the labelled area: convex hull of the reference tree points, 2 px wide."""
    from scipy.spatial import ConvexHull
    from matplotlib.path import Path as MPath
    from scipy import ndimage as ndi
    _, _, flat = _grid(x, y)
    cells = np.unique(flat[gt > 0])
    if len(cells) < 3: return np.zeros((ny, nx), bool)
    pts = np.c_[cells % nx, cells // nx].astype(float)
    hull = pts[ConvexHull(pts).vertices]
    cc, rr = np.meshgrid(np.arange(nx), np.arange(ny))
    inside = MPath(hull).contains_points(np.c_[cc.ravel(), rr.ravel()]).reshape(ny, nx)
    return inside & ~ndi.binary_erosion(inside, iterations=2)


def _layer(x, y, z, ids, nx, ny, seed, outline=None):
    """RGBA image: each tree filled in its own colour, white outline, transparent elsewhere."""
    from PIL import Image
    _, _, flat = _grid(x, y)
    t = ids > 0
    lab = np.zeros(nx * ny, np.int64)
    o = np.argsort(z[t]); lab[flat[t][o]] = ids[t][o]        # highest tree point per cell
    lab = lab.reshape(ny, nx)
    lab, _ = _fill(lab, lab == 0, 1.5)                       # close pin holes between points of the same crown
    u, inv = np.unique(lab, return_inverse=True); inv = inv.reshape(lab.shape)
    pal = np.random.default_rng(seed).random((len(u), 3)) * 0.8 + 0.2
    rgba = np.zeros((ny, nx, 4), np.uint8)
    tree = lab > 0
    rgba[tree, :3] = (pal[inv[tree]] * 255).astype(np.uint8); rgba[tree, 3] = 170
    edge = np.zeros_like(tree)
    edge[:, 1:] |= lab[:, 1:] != lab[:, :-1]; edge[1:, :] |= lab[1:, :] != lab[:-1, :]
    edge &= tree
    rgba[edge] = [255, 255, 255, 255]
    if outline is not None: rgba[outline] = [255, 214, 0, 255]       # yellow: the labelled (scored) area
    return Image.fromarray(rgba, "RGBA"), int((u > 0).sum())


def viewer(las_path, pred_dir, labels, scores, vid):
    """HTML for one plot. labels: {prediction file stem: model name}; scores: {model name: F1 on this plot}."""
    L = laspy.read(las_path)
    x, y, z = np.asarray(L.x), np.asarray(L.y), np.asarray(L.z)
    rgb = np.c_[np.asarray(L.red), np.asarray(L.green), np.asarray(L.blue)].astype(float)
    base, nx, ny = _true_colour(x, y, z, rgb)
    layers = [("gt", "Ground truth", np.asarray(L.treeID).astype(np.int64), None)]
    for f in sorted(Path(pred_dir).glob("*.npy")):
        ids = np.load(f).astype(np.int64)
        if len(ids) != len(x): continue
        name = labels.get(f.stem, f.stem); layers.append((f.stem, name, ids, scores.get(name)))
    btn = [f'<button data-k="none" onclick="rgbvShow(\'{vid}\',\'none\')">RGB only</button>']
    imgs = []
    area = _scored_area(x, y, layers[0][2], nx, ny)
    for k, (key, name, ids, f1) in enumerate(layers):
        im, n = _layer(x, y, z, ids, nx, ny, seed=11 + k, outline=None if key == "gt" else area)
        cap = f"{name} ({n} trees" + (f", F1 {f1:.2f})" if f1 is not None else ")")
        btn.append(f'<button data-k="{key}" onclick="rgbvShow(\'{vid}\',\'{key}\')"{" class=on" if k == 0 else ""}>{H.escape(cap)}</button>')
        imgs.append(f'<img class="lay" data-k="{key}" style="display:{"block" if k == 0 else "none"}" '
                    f'src="data:image/png;base64,{_b64(im, "PNG", optimize=True)}" alt="{H.escape(name)}">')
    w, h = nx * RES, ny * RES
    return (f'<div class="rgbv" id="{vid}"><div class="btns">{"".join(btn)}</div>'
            f'<div class="hint">Colours on: <input type="range" min="0" max="1" step="0.05" value="0.6" '
            f'oninput="rgbvAlpha(\'{vid}\',this.value)"> &nbsp; Area {w:.0f} x {h:.0f} m, north up, {RES * 100:.0f} cm pixels.</div>'
            f'<div class="stage"><img src="data:image/jpeg;base64,{_b64(base, "JPEG", quality=85)}" alt="true colour">'
            f'{"".join(imgs)}</div></div>')
