"""Check the per-tree measurements of treeseg.tree_table against trees measured in the field.

Dataset: Weiser et al. 2022, "Terrestrial, UAV-borne, and airborne laser scanning point clouds of central
European forest plots, Germany, with extracted individual trees and manual forest inventory measurements",
PANGAEA, https://doi.org/10.1594/PANGAEA.942856 (CC BY-SA 4.0). Paper: https://doi.org/10.5194/essd-14-2989-2022
Six 1 ha plots (BR01, BR03, BR05, KA09, KA10, KA11) have trees measured in the field: crown diameter (dripline in
two directions, averaged), crown base height (lowest branch of 1 m or longer, and lowest living branch), DBH, and
for about one tree in ten the height. Every tree comes as its own drone point cloud (RIEGL miniVUX-1UAV, leaf-on),
cut out by hand, so this checks the MEASURING only: finding the trees plays no part.

The plot zips are 7 to 16 GB each. This script does not download them: it reads the list of files at the end of
each zip and fetches only the single-tree drone clouds and their .geojson (about 1 GB for all six plots), into
data/Weiser2022/<plot>/. Run it again and it only fetches what is missing.

Height above ground of a tree's points = z minus the elevation of its stem base (given in the .geojson), which
is also how the authors measured their heights.

Usage:
  python validate_field.py                    all six plots
  python validate_field.py KA10               one small plot (14 trees with a drone cloud) to try it
  python validate_field.py --data D:/data/Weiser2022

Writes <data>/validation.csv (one row per tree: field values, the authors' values, ours) and prints the table.
Result of 18/11/2026 (1,169 trees; 900 with a field crown diameter, 897 with a field crown base, 91 with a field
height), error = ours - reference:
  height          the same as the authors' from the same cloud (RMSE 0.35 m); against the field hypsometer RMSE
                  2.6 m, which is the field method's own error (the authors' height does the same)
  crown diameter  bias +0.13 m, median error 0.98 m (13%), RMSE 1.58 m, r 0.88: as close to the field value as the
                  authors' own crown diameter (RMSE 1.60 m). crown_width_max_m is 0.65 m wider on average
  crown area      as the authors' convex-hull area (bias +0.15 m2, median 7%), 23% above their concave-hull area
  crown base      -0.5 m against the lowest living branch (median error 1.8 m), +1.9 m against the lowest branch
                  of 1 m or longer (median 1.7 m). Before 18/11/2026 ("10% of the points above 1 m are lower")
                  it was +4.5 m and +7.0 m (median 3.9 m and 6.4 m): see treeseg.crown_base_height
  canopy density  a value for every tree (median 53%)
"""
import argparse, csv, io, json, struct, sys, time, types, urllib.request, zipfile, zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np, laspy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import treeseg as T

URL = "https://download.pangaea.de/dataset/942856/files/{}.zip"
PLOTS = ["KA10", "KA11", "KA09", "BR03", "BR05", "BR01"]          # the plots with field measurements, small first
OURS = ["height_m", "crown_diam_m", "crown_width_max_m", "crown_area_m2", "crown_base_m", "crown_depth_m",
        "crown_volume_m3", "foliage_volume_m3", "canopy_density_pct", "point_density_m2"]


def http(url, a=None, b=None, tries=5):
    req = urllib.request.Request(url, headers={"Range": f"bytes={a}-{b - 1}"} if a is not None else {},
                                 method="GET" if a is not None else "HEAD")
    for k in range(tries):
        try:
            r = urllib.request.urlopen(req, timeout=180)
            return r.read() if a is not None else int(r.headers["Content-Length"])
        except Exception:
            if k == tries - 1: raise
            time.sleep(2)


class RemoteFile(io.RawIOBase):
    """A file on a web server, read in 1 MB blocks with range requests (enough for zipfile to list a zip)."""
    def __init__(self, url):
        self.url, self.pos, self.blocks, self.size = url, 0, {}, http(url)
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off); return self.pos
    def readinto(self, buf):
        n = min(len(buf), self.size - self.pos); out = b""
        while len(out) < n:
            k = self.pos // (1 << 20)
            if k not in self.blocks:
                self.blocks[k] = http(self.url, k << 20, min((k + 1) << 20, self.size))
            part = self.blocks[k][self.pos - (k << 20):][:n - len(out)]; out += part; self.pos += len(part)
        buf[:len(out)] = out; return len(out)


def fetch(plot, folder):
    """The .geojson of every tree of the plot and its leaf-on drone cloud, straight out of the remote zip."""
    url = URL.format(plot); folder.mkdir(parents=True, exist_ok=True)
    Z = zipfile.ZipFile(io.BufferedReader(RemoteFile(url), 1 << 20))
    todo = [i for i in Z.infolist() if "single_trees/" in i.filename
            and (i.filename.endswith("ULS-on.laz") or i.filename.endswith(".geojson"))
            and not (folder / Path(i.filename).name).exists()]
    if not todo: return
    print(f"{plot}: fetching {len(todo)} files, {sum(i.compress_size for i in todo) / 1e6:.0f} MB ...", flush=True)
    def get(i):
        a = i.header_offset; d = http(url, a, a + 30 + len(i.filename.encode()) + 400 + i.compress_size)
        n, m = struct.unpack("<HH", d[26:30]); raw = d[30 + n + m:30 + n + m + i.compress_size]
        if i.compress_type == 8: raw = zlib.decompress(raw, -15)
        if len(raw) != i.file_size: raise SystemExit(f"{i.filename}: incomplete download")
        p = folder / Path(i.filename).name; tmp = p.with_name(p.name + ".part"); tmp.write_bytes(raw); tmp.replace(p)
    with ThreadPoolExecutor(12) as ex: list(ex.map(get, todo))


def num(v):
    try: return float(v)
    except (TypeError, ValueError): return np.nan


def measure(geojson):
    P = json.loads(geojson.read_text())["properties"]; src, base = {}, None
    for m in P["measurements"]:
        if "position_xyz" in m: base = float(m["position_xyz"][2])
        if "source" in m and not (m["source"] == "ULS" and m.get("canopy_condition") != "leaf-on"): src[m["source"]] = m
    uls = [d for d in P["data"] if d["mode"] == "ULS" and d["canopy_condition"] == "leaf-on"]
    if base is None or not uls or not (geojson.parent / uls[0]["file"]).exists(): return None
    L = laspy.read(geojson.parent / uls[0]["file"])
    if len(L.x) < 30: return None
    cloud = types.SimpleNamespace(x=np.asarray(L.x), y=np.asarray(L.y), hag=np.clip(np.asarray(L.z) - base, 0, None))
    r = T.tree_table(cloud, np.ones(len(L.x), np.int64))[0]
    fi, au = src.get("FI", {}), src.get("ULS", {})
    return dict(id=P["id"], plot=geojson.parent.name, species=P["species"], points=len(L.x),
                field_height_m=num(fi.get("height_m")), field_crown_diam_m=num(fi.get("mean_crown_diameter_m")),
                field_crown_base_m=num(fi.get("crown_base_height_m")),
                field_crown_base_green_m=num(fi.get("crown_base_height_green_m")), field_dbh_cm=num(fi.get("DBH_cm")),
                authors_height_m=num(au.get("height_m")), authors_crown_diam_m=num(au.get("mean_crown_diameter_m")),
                authors_crown_base_m=num(au.get("crown_base_height_m")),
                authors_area_convex_m2=num(au.get("crown_projection_area_convex_hull_m2")),
                authors_area_concave_m2=num(au.get("crown_projection_area_concave_hull_m2")),
                **{k: (np.nan if r.get(k) is None else r[k]) for k in OURS})


def line(rows, title, ours, ref, unit="m"):
    a = np.array([r[ours] for r in rows], float); b = np.array([r[ref] for r in rows], float)
    ok = np.isfinite(a) & np.isfinite(b) & (b > 0)
    if ref.startswith("field_crown_base"): ok &= b < np.array([r["height_m"] for r in rows]) + 1   # typing slips
    if ok.sum() < 3: return
    a, b = a[ok], b[ok]; e = a - b
    print(f"  {title:58s} {ok.sum():5d}  {e.mean():+7.2f}  {np.median(np.abs(e)):7.2f}  {np.sqrt((e ** 2).mean()):6.2f}  "
          f"{100 * np.median(np.abs(e) / b):6.1f}%  {np.corrcoef(a, b)[0, 1]:5.2f}   ({unit})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plots", nargs="*", default=PLOTS, help=f"default: {' '.join(PLOTS)}")
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent.parent / "data" / "Weiser2022"))
    ap.add_argument("--no-download", action="store_true", help="only use what is already in --data")
    a = ap.parse_args()
    data = Path(a.data); rows = []
    for plot in a.plots:
        if not a.no_download: fetch(plot, data / plot)
        for g in sorted((data / plot).glob("*.geojson")):
            r = measure(g)
            if r: rows.append(r)
        print(f"{plot}: {sum(r['plot'] == plot for r in rows)} trees measured", flush=True)
    if not rows: raise SystemExit("No trees found.")
    with open(data / "validation.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"\n{len(rows)} trees with a leaf-on drone cloud. error = ours - reference")
    print(f"  {'':58s} trees     bias  median   RMSE  median      r\n  {'':58s}               |error|         |error|%")
    line(rows, "height: field (hypsometer)", "height_m", "field_height_m")
    line(rows, "height: the authors', same drone cloud", "height_m", "authors_height_m")
    line(rows, "crown diameter: field (dripline, 2 directions)", "crown_diam_m", "field_crown_diam_m")
    line(rows, "crown width, widest: field crown diameter", "crown_width_max_m", "field_crown_diam_m")
    line(rows, "  (the authors' crown diameter: field)", "authors_crown_diam_m", "field_crown_diam_m")
    line(rows, "crown area: the authors' convex hull area", "crown_area_m2", "authors_area_convex_m2", "m2")
    line(rows, "crown area: the authors' concave hull area", "crown_area_m2", "authors_area_concave_m2", "m2")
    line(rows, "crown base: field, lowest branch of 1 m or longer", "crown_base_m", "field_crown_base_m")
    line(rows, "crown base: field, lowest living branch", "crown_base_m", "field_crown_base_green_m")
    line(rows, "  (the authors' crown base: field, lowest branch)", "authors_crown_base_m", "field_crown_base_m")
    d = np.array([r["canopy_density_pct"] for r in rows], float)
    print(f"  canopy density: empty for {int(np.isnan(d).sum())} of {len(d)} trees, median {np.nanmedian(d):.0f}%")
    print(f"\n-> {data / 'validation.csv'}")


if __name__ == "__main__":
    main()
