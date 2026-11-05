"""Tree info for CloudCompare: click a tree, see its profile.

Takes a segmentation result that already has one tree ID per point (SegmentAnyTree result, Galaxy
"Processed Files" zip, process 2 points_treeid.laz, a *_colour.laz ...), measures every tree, and writes

  <name>_info.laz     the same points, each tree in its own colour, and every point carries the numbers of
                      the tree it belongs to as extra fields (scalar fields in CloudCompare):
                        TreeID, Height_m, Crown_diameter_m, Crown_width_max_m, Crown_area_m2,
                        Crown_volume_m3, Foliage_volume_m3, Crown_base_m, Crown_depth_m,
                        Canopy_density_pct, Nearest_tree_m, N_points, QC_flag
                      and empty fields kept for later (NaN until that data exists):
                        Species_code, Species_prob, Foliage_condition, Thermal_anomaly, Confidence, Koala_value
                      Points that are not in a tree (ground, unassigned) have TreeID 0 and NaN.
  <name>_trees.csv    one row per tree: tree_uid, location (tree top), all numbers above, the empty columns
  <name>_crowns.shp   crown outlines (polygon at the tree's top height), opens in CloudCompare and QGIS.
                      Trees completely under another crown have no outline (the outline is the crown seen
                      from above) but their points still carry all the numbers.

In CloudCompare: open <name>_info.laz (keep all extra fields ticked), pick the "Point picking" tool
(Tools > Point picking, or the "i" icon), click a tree. The label shows the value of the scalar field
currently displayed; click the label (or open its properties) to see all fields. Colour the cloud by any
field (Properties > Colors > Scalar field, choose e.g. Height_m) to map heights, volumes ...

QC_flag: 0 = ok, 1 = few points (< 20), 2 = tree top within 2 m of the data edge (crown may be cut), 3 = both.

Usage:
  python tree_info.py sat_result.laz
  python tree_info.py "Galaxy14-[Processed Files].zip" --site COLLINS
  python tree_info.py points_treeid.laz --field treeID --out D:/results/site1
"""
import argparse, csv, time
from pathlib import Path
import numpy as np, laspy
import treeseg as T
from mosaic_labelled import expand

ID_FIELDS = ["PredInstance_SAT", "PredInstance", "preds_instance_segmentation", "treeID", "TreeID", "instance"]

# (scalar field name, tree_table key) in the order CloudCompare lists them
MEASURED = [("Height_m", "height_m"), ("Crown_diameter_m", "crown_diam_m"), ("Crown_width_max_m", "crown_width_max_m"),
            ("Crown_area_m2", "crown_area_m2"), ("Crown_volume_m3", "crown_volume_m3"),
            ("Foliage_volume_m3", "foliage_volume_m3"), ("Crown_base_m", "crown_base_m"),
            ("Crown_depth_m", "crown_depth_m"), ("Canopy_density_pct", "canopy_density_pct"),
            ("Nearest_tree_m", "nn_spacing_m"), ("N_points", "n_points"), ("QC_flag", "qc_code")]
# filled later (species model, multispectral, thermal, koala habitat rules); NaN for now
LATER = ["Species_code", "Species_prob", "Foliage_condition", "Thermal_anomaly", "Confidence", "Koala_value"]


def find_id_field(L, field=None):
    names = list(L.point_format.extra_dimension_names) + list(L.point_format.dimension_names)
    if field:
        if field not in names: raise SystemExit(f"Field {field} not in file. Fields: {names}")
        return field
    f = next((n for n in ID_FIELDS if n in names), None) or \
        next((n for n in names if "instance" in n.lower() or "treeid" in n.lower()), None)
    if f is None:
        raise SystemExit(f"No tree ID field in this file (looked for {', '.join(ID_FIELDS)}). Fields: {names}")
    return f


def qc_codes(rows, x, y):
    bx = (x.min(), y.min(), x.max(), y.max())
    for r in rows:
        c = 1 if r["n_points"] < 20 else 0
        if min(r["top_x"] - bx[0], r["top_y"] - bx[1], bx[2] - r["top_x"], bx[3] - r["top_y"]) < 2: c += 2
        r["qc_code"] = c
        r["qc_flag"] = {0: "ok", 1: "few_points", 2: "edge", 3: "few_points,edge"}[c]


def write_info_laz(L, ids, rows, path, crs=None):
    """Copy of the points: per-tree colour, classification, and the per-tree numbers on every point."""
    n = len(ids)
    hdr = laspy.LasHeader(point_format=7, version="1.4")
    hdr.offsets = L.header.offsets; hdr.scales = L.header.scales
    hdr.add_extra_dim(laspy.ExtraBytesParams(name="TreeID", type=np.int32, description="tree ID, 0 = not a tree"))
    for name, _ in MEASURED + [(f, None) for f in LATER]:
        hdr.add_extra_dim(laspy.ExtraBytesParams(name=name, type=np.float32))
    if crs is not None:
        try: hdr.add_crs(crs)
        except Exception: pass
    W = laspy.LasData(hdr)
    W.x, W.y, W.z = L.x, L.y, L.z
    names = set(L.point_format.dimension_names)
    if "classification" in names: W.classification = np.asarray(L.classification)
    if "intensity" in names: W.intensity = np.asarray(L.intensity)

    # lookup table: tree id -> row number (+1); 0 = not a tree
    tids = np.array([r["tree_id"] for r in rows], np.int64)
    look = np.zeros(int(ids.max()) + 1 if n else 1, np.int64)
    look[tids] = np.arange(1, len(rows) + 1)
    row = look[np.clip(ids, 0, None)]                    # 0 for non-tree points
    W.TreeID = np.where(row > 0, ids, 0).astype(np.int32)
    for name, key in MEASURED:
        v = np.r_[np.nan, [np.nan if r.get(key) is None else float(r[key]) for r in rows]].astype(np.float32)
        W[name] = v[row]
    for name in LATER:
        W[name] = np.full(n, np.nan, np.float32)

    # colour: one colour per tree (same palette as colour_trees.bat); others keep real colour, else grey
    pal = (T.random_colours(len(rows))[:, :3] * 65535).astype(np.uint16)
    pal[0] = int(0.6 * 65535)
    rgb = pal[row]
    if "red" in names and int(np.asarray(L.red).max()) > 0:
        bg = row == 0
        for k, ch in enumerate(("red", "green", "blue")):
            rgb[bg, k] = np.asarray(L[ch])[bg]
    W.red, W.green, W.blue = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    W.write(path)


def write_crowns(cloud, ids, rows, path, res=0.5):
    """Crown outline polygons (raster crowns at 0.5 m, as in process 2), z = tree height above ground + ground."""
    import geopandas as gpd
    import shapely
    from shapely.geometry import shape
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    x0, y1, nx, ny, ix, iy = T.chm_grid(cloud.x, cloud.y, res)
    lab = np.zeros(nx * ny, np.int32); t = ids > 0; o = np.argsort(cloud.hag[t])
    lab[(iy[t] * nx + ix[t])[o]] = ids[t][o]; lab = lab.reshape(ny, nx)
    polys = {}
    for geom, v in shapes(lab, mask=lab > 0, transform=from_origin(x0, y1, res, res)):
        v = int(v); g = shape(geom); polys[v] = polys[v].union(g) if v in polys else g
    out = []
    for r in rows:
        g = polys.get(r["tree_id"])
        if g is None: continue
        if g.geom_type == "MultiPolygon": g = max(g.geoms, key=lambda p: p.area)
        g = shapely.force_3d(g, r["top_z"])               # outline drawn at the tree top in 3D viewers
        # shapefile column names: 10 characters at most
        out.append(dict(tree_uid=r["tree_uid"], tree_id=r["tree_id"], height_m=r["height_m"], crown_d_m=r["crown_diam_m"],
                        area_m2=r["crown_area_m2"], volume_m3=r["crown_volume_m3"], qc_flag=r["qc_flag"], geometry=g))
    if out:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            gpd.GeoDataFrame(out, geometry="geometry", crs=cloud.crs).to_file(path)
    return len(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="segmentation result(s): .las/.laz (or Galaxy .zip) with a tree ID per point")
    ap.add_argument("--field", help="tree ID field (default: PredInstance_SAT, PredInstance, treeID ...)")
    ap.add_argument("--out", help="output folder (default: next to the input)")
    ap.add_argument("--site", help="site code used in tree_uid, e.g. COLLINS -> COLLINS_TREE00042 (default: file name)")
    ap.add_argument("--ground", choices=["auto", "file", "csf"], default="auto",
                    help="ground for heights: file class 2 when present, otherwise computed (CSF)")
    ap.add_argument("--no-crowns", action="store_true", help="skip the crown outline shapefile")
    a = ap.parse_args()
    for f in expand(a.files):
        t0 = time.time(); f = Path(f)
        out = Path(a.out) if a.out else f.parent; out.mkdir(parents=True, exist_ok=True)
        stem = f.stem
        print(f"\nReading {f.name} ...")
        cloud = T.load_cloud(f, a.ground)
        L = cloud.las
        fld = find_id_field(L, a.field)
        ids = np.asarray(L[fld]).astype(np.int64); ids[ids < 0] = 0
        print(f"  {cloud.n:,} points, tree IDs from '{fld}', ground: {cloud.ground_source}")
        rows = T.tree_table(cloud, ids)
        if not rows: raise SystemExit("No trees in this file (all tree IDs are 0).")
        qc_codes(rows, cloud.x, cloud.y)
        site = a.site or stem
        tag = "".join(ch if ch.isalnum() else "_" for ch in site).strip("_").upper() or "SITE"
        # absolute z of each tree top (for the crown outline)
        top_z = {}
        o = np.lexsort((cloud.hag, ids)); last = np.r_[ids[o][1:] != ids[o][:-1], True]
        for i in o[last]:
            if ids[i] > 0: top_z[int(ids[i])] = float(cloud.z[i])
        for r in rows:
            r["tree_uid"] = f"{tag}_TREE{r['tree_id']:05d}"; r["top_z"] = round(top_z[r["tree_id"]], 2)
        print(f"  {len(rows)} trees measured")

        laz = out / f"{stem}_info.laz"
        write_info_laz(L, ids, rows, laz, cloud.crs)
        cols = ["tree_uid", "tree_id", "top_x", "top_y", "top_z"] + [k for _, k in MEASURED if k != "qc_code"] + \
               ["h_p25_m", "h_p50_m", "h_p75_m", "height_sd_m", "point_density_m2", "qc_flag"] + \
               [c.lower() for c in LATER]
        with open(out / f"{stem}_trees.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, cols, extrasaction="ignore"); w.writeheader()
            for r in rows: w.writerow({**{c.lower(): "" for c in LATER}, **r})
        msg = ""
        if not a.no_crowns:
            try:
                k = write_crowns(cloud, ids, rows, out / f"{stem}_crowns.shp")
                msg = f", {k} crown outlines -> {stem}_crowns.shp"
            except Exception as e:
                msg = f" (crown outlines skipped: {e})"
        print(f"  -> {laz.name}, {stem}_trees.csv{msg}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
