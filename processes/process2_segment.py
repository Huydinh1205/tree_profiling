"""PROCESS 2: find individual trees in our own LiDAR (no ground truth) and build a
short report for presenting.

Input: a processed point cloud from DJI Terra (LAS/LAZ), e.g. cloud_merged.las.
Ground: DJI Terra ground class (2) is used when present, otherwise CSF.

For every model that can run on this machine it writes
  <out>/<model>/trees_crowns.gpkg   crown polygons (tree_id, height_m, crown_area_m2, crown_diam_m, top_x, top_y, n_points)
  <out>/<model>/trees_tops.gpkg     tree-top points, same attributes
  <out>/<model>/points_treeid.laz   input points + treeID + HAG
  <out>/<model>/trees.csv           the same table as CSV
and once:
  <out>/chm.tif, <out>/dtm.tif      canopy height and ground models
  <out>/summary.csv                 one row per model
  <out>/report.md + report.html     models used, data summary, results, figures (open the .html to present)

Usage examples:
  python process2_segment.py "D:/Survey/Site/terra_lidar/cloud_merged.las" --out results/site1
  python process2_segment.py cloud.laz --out results/site1 --crop-center 512300 6250100 --crop-size 200
  python process2_segment.py cloud.laz --out results/site1 --models chm_watershed --benchmark results/benchmark_rmit/scores_summary.csv
  python process2_segment.py cloud.laz --out results/site1 --external sat=sat_result.laz
"""
import argparse, base64, csv, re, tempfile, time
from pathlib import Path
import numpy as np, laspy
import treeseg as T


def crop_file(src, dst, center=None, size=None, bbox=None):
    L = laspy.read(src)
    x, y = np.asarray(L.x), np.asarray(L.y)
    if bbox is None:
        cx, cy = center if center else (np.median(x), np.median(y))
        h = size / 2; bbox = (cx - h, cy - h, cx + h, cy + h)
    m = (x >= bbox[0]) & (x <= bbox[2]) & (y >= bbox[1]) & (y <= bbox[3])
    if m.sum() < 1000: raise SystemExit(f"Crop {bbox} keeps only {m.sum()} points")
    W = laspy.LasData(L.header); W.points = L.points[m]; W.write(dst)
    return bbox, int(m.sum()), len(x)


def html_from_md(md, folder):
    """Tiny markdown -> standalone HTML (tables, headings, images embedded)."""
    out, rows = [], []
    def flush():
        if rows:
            out.append("<table>" + "".join(
                "<tr>" + "".join(f"<{'th' if i == 0 else 'td'}>{c.strip()}</{'th' if i == 0 else 'td'}>"
                                 for c in r.strip().strip("|").split("|")) + "</tr>"
                for i, r in enumerate(rows) if not re.match(r"^\|(-+\|)+$", r.strip())) + "</table>")
            rows.clear()
    for line in md.splitlines():
        if line.startswith("|"): rows.append(line); continue
        flush()
        if line.startswith("# "): out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "): out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("- "): out.append(f"<li>{line[2:]}</li>")
        elif (m := re.match(r"!\[(.*)\]\((.*)\)", line.strip())):
            b64 = base64.b64encode((folder / m.group(2)).read_bytes()).decode()
            out.append(f'<p><img alt="{m.group(1)}" src="data:image/png;base64,{b64}"></p>')
        elif line.strip(): out.append(f"<p>{line}</p>")
    flush()
    body = "\n".join(out)
    body = re.sub(r"`([^`]+)`", r"<code>\1</code>", body); body = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", body)
    css = ("body{font-family:Segoe UI,Calibri,Arial,sans-serif;max-width:1150px;margin:24px auto;padding:0 16px;color:#222}"
           "h1{color:#1F3864}h2{color:#1F3864;border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:28px}"
           "table{border-collapse:collapse;margin:10px 0;font-size:14px}th,td{border:1px solid #ccc;padding:5px 9px;vertical-align:top}"
           "th{background:#DCE6F0}img{max-width:100%;border:1px solid #eee}code{background:#f4f4f4;padding:1px 4px}")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Tree profiling report</title><style>{css}</style></head><body>{body}</body></html>"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("las", help="processed point cloud (.las/.laz) from DJI Terra")
    ap.add_argument("--out", default="results/site")
    ap.add_argument("--models", nargs="+", default=list(T.MODELS), choices=list(T.MODELS))
    ap.add_argument("--external", nargs="*", default=[], metavar="NAME=FILE[:FIELD]")
    ap.add_argument("--ground", choices=["auto", "file", "csf"], default="auto")
    ap.add_argument("--epsg", type=int, help="only if the file has no CRS")
    ap.add_argument("--crop-center", nargs=2, type=float, metavar=("X", "Y"))
    ap.add_argument("--crop-size", type=float, help="square crop size in metres (e.g. 200)")
    ap.add_argument("--crop-bbox", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    ap.add_argument("--benchmark", help="scores_summary.csv from process 1, quoted in the report")
    ap.add_argument("--site-name", default=None)
    ap.add_argument("--allow-slow", action="store_true")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); t0 = time.time()
    site = a.site_name or Path(a.las).stem
    src = Path(a.las); crop_note = "whole file"
    if a.crop_size or a.crop_bbox:
        tmp = Path(tempfile.mkdtemp()) / ("crop" + src.suffix.lower())
        bbox, kept, total = crop_file(src, tmp, a.crop_center, a.crop_size, a.crop_bbox)
        crop_note = f"crop {bbox[2] - bbox[0]:.0f} x {bbox[3] - bbox[1]:.0f} m ({kept:,} of {total:,} points)"
        print(f"Cropped: {crop_note}"); src = tmp

    print(f"Reading {src} ...")
    cloud = T.load_cloud(src, a.ground, a.epsg)
    dens, area = T.density(cloud)
    print(f"  {cloud.n:,} points, {area / 1e4:.2f} ha, {dens:.0f} pts/m2, ground: {cloud.ground_source}")
    chm, geo, empty = T.write_rasters(cloud, out)
    hasrgb = "red" in cloud.las.point_format.dimension_names and np.asarray(cloud.las.red).max() > 0
    warnings = []
    if cloud.crs is None: warnings.append("No CRS in the file: outputs have no CRS (use --epsg).")
    if dens < 50: warnings.append(f"Point density {dens:.0f} pts/m2 is low: published tests show tree detection drops below ~50 pts/m2.")
    if empty > 0.2: warnings.append(f"{empty:.0%} of 0.5 m CHM cells are empty.")

    results, tables, crowns = [], {}, []
    def finish(name, label, ids, secs, status):
        rows, lab = T.export_model(cloud, ids, out / name.replace(":", "_"), site=site, model=label)
        with open(out / name.replace(":", "_") / "trees.csv", "w", newline="") as f:
            if rows: w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); w.writerows(rows)
        H = np.array([r["height_m"] for r in rows] or [np.nan]); D = np.array([r["crown_diam_m"] for r in rows] or [np.nan])
        results.append(dict(model=label, status=status, trees=len(rows), trees_per_ha=len(rows) / (area / 1e4),
                            h_median=float(np.nanmedian(H)), h_p95=float(np.nanpercentile(H, 95)) if rows else np.nan,
                            crown_d_median=float(np.nanmedian(D)),
                            crown_vol_median=float(np.nanmedian([r["crown_volume_m3"] for r in rows] or [np.nan])),
                            seconds=secs))
        tables[label] = rows; crowns.append((label, lab))
        print(f"  {label}: {len(rows)} trees, {secs:.0f}s")
        return ids

    preds, skipped = {}, {}
    for m in a.models:
        label = T.MODELS[m]["label"]
        try:
            print(f"Running {label} ...")
            ids, secs = T.run_model(m, cloud, allow_slow=a.allow_slow)
            preds[label] = finish(m, label, ids, secs, "ran")
        except T.ModelUnavailable as e:
            print(f"  skipped: {e}"); skipped[label] = str(e)
        except Exception as e:
            print(f"  FAILED: {e}"); skipped[label] = f"failed: {str(e)[:300]}"
    for spec in a.external:
        name, path, field = T.parse_external(spec)
        try:
            ids, fld, cov = T.read_external(cloud, path, field)
            preds[name] = finish(f"external_{name}", name, T.relabel(ids), 0.0, f"external file, field {fld}, {cov:.0%} matched")
        except Exception as e:
            skipped[name] = f"failed: {e}"
    if not results: raise SystemExit("No model ran: " + "; ".join(f"{k}: {v}" for k, v in skipped.items()))

    import json, datetime
    hdr = cloud.las.header
    meta = dict(site=site, input_file=str(Path(a.las).resolve()), crop=crop_note,
                file_creation_date=str(getattr(hdr, "creation_date", "") or ""), points=cloud.n,
                point_density_pts_m2=round(dens, 1), area_ha=round(area / 1e4, 3), ground=cloud.ground_source,
                crs=cloud.crs.to_string() if cloud.crs else None,
                models={T.MODELS[m]["label"] if m in T.MODELS else m: (T.MODELS[m]["params"] if m in T.MODELS else {}) for m in a.models},
                skipped=skipped, run_at=datetime.datetime.now().isoformat(timespec="seconds"),
                scripts="tree_profiling/processes (treeseg.py, lidr_python.py)")
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(results[0].keys())); w.writeheader(); w.writerows(results)

    # ---- figures
    T.fig_crowns_on_chm(chm, geo, crowns, out / "fig_crowns_on_chm.png")
    zoom = None
    if max(chm.shape) * geo[2] > 120:                       # big area: add a 60 m close-up at the centre
        r0, c0 = chm.shape[0] // 2 - 60, chm.shape[1] // 2 - 60
        sl = (slice(max(r0, 0), r0 + 120), slice(max(c0, 0), c0 + 120))
        T.fig_crowns_on_chm(chm[sl], geo, [(t, l[sl]) for t, l in crowns], out / "fig_crowns_zoom.png")
        zoom = "fig_crowns_zoom.png"
    T.fig_height_hist(tables, out / "fig_tree_heights.png")
    if hasrgb and preds:
        T.fig_rgb_trees(cloud, preds, out / "fig_rgb_trees.png", separate_dir=out / "figures_by_model")
    T.fig_topviews([("Height above ground", cloud.x, cloud.y, cloud.hag, None)] +
                   [(lbl, cloud.x, cloud.y, cloud.hag, ids) for lbl, ids in preds.items()], out / "fig_topview_trees.png")
    T.fig_sideview(list(preds.items()), cloud.x, cloud.y, cloud.hag, out / "fig_sideview.png")

    # ---- report
    used = [m for m in a.models if T.MODELS[m]["label"] not in skipped]
    md = [f"# Tree profiling: {site}\n",
          f"Generated {time.strftime('%Y-%m-%d %H:%M')} from `{Path(a.las).name}` ({crop_note}).\n",
          "## Data\n",
          T.md_table([dict(k=k, v=v) for k, v in [
              ("Points", f"{cloud.n:,}"), ("Area covered", f"{area / 1e4:.2f} ha"), ("Point density", f"{dens:.0f} pts/m2"),
              ("Ground", cloud.ground_source), ("Real RGB colour", "yes" if hasrgb else "no"),
              ("CRS", cloud.crs.name if cloud.crs else "none"),
              ("Canopy height (95th percentile)", f"{np.percentile(chm[chm > 0], 95):.1f} m" if (chm > 0).any() else "n/a"),
              ("Canopy cover (> 2 m)", f"{(chm > 2).mean():.0%}")]], [("Item", "k"), ("Value", "v")]),
          "\n## Models used\n",
          T.md_table([dict(n=T.MODELS[m]["label"], t=T.MODELS[m]["kind"], d=T.MODELS[m]["detail"], r=T.MODELS[m]["needs"],
                           l=T.MODELS[m]["licence"], s=("ran" if T.MODELS[m]["label"] not in skipped else skipped[T.MODELS[m]["label"]]))
                      for m in a.models] +
                     [dict(n=n, t="external result", d="", r="", l="", s=next((r["status"] for r in results if r["model"] == n), skipped.get(n, "")))
                      for n in [T.parse_external(s)[0] for s in a.external]],
                     [("Model", "n"), ("Type", "t"), ("How it works", "d"), ("Needs", "r"), ("Licence", "l"), ("Status here", "s")]),
          "\n## Results on this site\n",
          T.md_table(results, [("Model", "model"), ("Trees found", "trees"), ("Trees / ha", "trees_per_ha"),
                               ("Median height (m)", "h_median"), ("95th pct height (m)", "h_p95"),
                               ("Median crown diameter (m)", "crown_d_median"), ("Median crown volume (m3)", "crown_vol_median"),
                               ("Run time (s)", "seconds")],
                     {"trees_per_ha": "{:.0f}", "h_median": "{:.1f}", "h_p95": "{:.1f}", "crown_d_median": "{:.1f}",
                      "crown_vol_median": "{:.0f}", "seconds": "{:.1f}"}),
          "\nThere is no ground truth for this site, so these are counts, not accuracy. "
          "Differences between models show how uncertain the tree count is.\n"]
    if a.benchmark and Path(a.benchmark).exists():
        rows = [r for r in csv.DictReader(open(a.benchmark)) if r["protocol"] == "benchmark"]
        md += ["\n## Accuracy measured on labelled data (process 1)\n",
               T.md_table([dict(m=T.MODELS.get(r["model"], {}).get("label", r["model"]), f=float(r["f1"]),
                                d=float(r["det_f1"]), o=float(r["det_rate_open"]), u=float(r["det_rate_overtopped"])) for r in rows],
                          [("Model", "m"), ("Segmentation F1", "f"), ("Tree-top detection F1", "d"),
                           ("Found: open-topped trees", "o"), ("Found: trees under other crowns", "u")],
                          {k: "{:.2f}" for k in "fdou"}),
               "\nPublished on the same labelled plot: " +
               ", ".join(f"{n} {f:.2f} ({l})" for n, f, l in T.PUBLISHED_RMIT) + ".\n"]
    md += ["\n## Figures\n",
           "Crown outlines over the canopy height model:\n", "![crowns](fig_crowns_on_chm.png)\n"] + \
          (["True colour top view and the same view with each tree filled in its own colour:\n",
            "![rgb trees](fig_rgb_trees.png)\n"] if hasrgb else []) + \
          (["Close-up, 60 x 60 m at the centre:\n", f"![zoom]({zoom})\n"] if zoom else []) + [
           "Trees coloured by ID (top view):\n", "![trees](fig_topview_trees.png)\n",
           "Side view of a 4 m slice through the middle of the area:\n", "![side](fig_sideview.png)\n",
           "Tree height distribution:\n", "![heights](fig_tree_heights.png)\n",
           "\n## Notes\n"]
    md += [f"- {w}\n" for w in warnings]
    md += ["- CHM-based methods only see trees whose top reaches the canopy surface; trees under other crowns are mostly missed.\n",
           "- Outputs per model are in sub-folders: `trees_crowns.gpkg` and `trees_tops.gpkg` open directly in QGIS. "
           "Each tree has the LiDAR structural profile: height, crown base and depth, crown area, diameter, max width, "
           "elongation, crown volume (convex hull), foliage-occupied volume (0.5 m voxels), canopy density, height percentiles, "
           "height variability, spacing to the nearest tree, point density.\n",
           "- Crown base, volumes and density are LiDAR proxies; they are not field measurements of foliage or biomass.\n",
           "- `run_metadata.json` records the input file, point density, ground source, models and parameters of this run.\n"]
    text = "\n".join(part.rstrip("\n") + "\n" for part in md)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "report.html").write_text(html_from_md(text, out), encoding="utf-8")
    print(f"\nDone in {time.time() - t0:.0f}s. Open {out / 'report.html'}")
    if skipped: print("Skipped: " + "; ".join(f"{k}: {v}" for k, v in skipped.items()))


if __name__ == "__main__":
    main()
