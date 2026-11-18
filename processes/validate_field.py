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

Writes <data>/validation.csv (one row per tree: the field values, then ours) and <data>/report.html (charts and
tables to look at in a browser), and prints the table.
Result of 19/11/2026 (1,169 trees; 900 with a field crown diameter, 745 with a field lowest living branch, 897
with a field lowest branch, 91 with a field height), error = ours - field, median error:
  height          1.2 m (8%). The hypsometer itself is about that far off on tall trees: heights from the drone,
                  aircraft and ground scans agree with each other to 0.3 to 0.4 m but not with the field heights
  crown diameter  1.0 m (13%), bias +0.1 m. 60 other ways of measuring it (other crown points, cell sizes,
                  hulls, widths in 36 directions, radii per sector, a scale fitted on the other plots) all land at
                  0.93 to 1.0 m: the field dripline estimate in 2 directions does not allow better
  crown base      1.8 m against the lowest living branch, +1.9 m against the lowest branch of 1 m or longer
                  (dead branches too), see treeseg.crown_base_height
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
    fi = src.get("FI", {})
    cd = num(fi.get("mean_crown_diameter_m"))
    return dict(id=P["id"], plot=geojson.parent.name, species=P["species"], points=len(L.x),
                field_height_m=num(fi.get("height_m")), field_crown_diam_m=cd,
                field_crown_area_m2=np.pi * cd * cd / 4,                  # circle with the field crown diameter
                field_crown_base_m=num(fi.get("crown_base_height_m")),
                field_crown_base_green_m=num(fi.get("crown_base_height_green_m")), field_dbh_cm=num(fi.get("DBH_cm")),
                **{k: (np.nan if r.get(k) is None else r[k]) for k in OURS})


def line(rows, title, ours, ref, unit="m"):
    a = np.array([r[ours] for r in rows], float); b = np.array([r[ref] for r in rows], float)
    ok = np.isfinite(a) & np.isfinite(b) & (b > 0)
    if ref.startswith("field_crown_base"): ok &= b < np.array([r["height_m"] for r in rows]) + 1   # typing slips
    if ok.sum() < 3: return
    a, b = a[ok], b[ok]; e = a - b
    print(f"  {title:58s} {ok.sum():5d}  {e.mean():+7.2f}  {np.median(np.abs(e)):7.2f}  {np.sqrt((e ** 2).mean()):6.2f}  "
          f"{100 * np.median(np.abs(e) / b):6.1f}%  {np.corrcoef(a, b)[0, 1]:5.2f}   ({unit})")



# ----------------------------------------------------------------------------------------------- report.html
# (label, ours, field column, unit, what the field value is)
COMPARE = [
    ("Height", "height_m", "field_height_m", "m", "hypsometer, about 1 tree in 10"),
    ("Crown diameter", "crown_diam_m", "field_crown_diam_m", "m", "dripline in 2 directions, mean"),
    ("Crown width, widest", "crown_width_max_m", "field_crown_diam_m", "m", "the field crown diameter"),
    ("Crown area", "crown_area_m2", "field_crown_area_m2", "m²", "circle with the field crown diameter"),
    ("Crown base", "crown_base_m", "field_crown_base_green_m", "m", "lowest living branch"),
    ("Crown base", "crown_base_m", "field_crown_base_m", "m", "lowest branch of 1 m or longer, dead ones too"),
]


def pairs(rows, ours, ref):
    """(row, ours, field) for every tree that has both; field crown bases above the tree are typing slips."""
    out = []
    for r in rows:
        a, b = r[ours], r[ref]
        if not (np.isfinite(a) and np.isfinite(b) and b > 0): continue
        if ref.startswith("field_crown_base") and b >= r["height_m"] + 1: continue
        out.append((r, float(a), float(b)))
    return out


def stats(p):
    a = np.array([q[1] for q in p]); b = np.array([q[2] for q in p]); e = a - b
    return dict(n=len(p), bias=e.mean(), med=np.median(np.abs(e)), rmse=np.sqrt((e ** 2).mean()),
                pct=100 * np.median(np.abs(e) / b), r=np.corrcoef(a, b)[0, 1] if len(p) > 2 else np.nan)


def nice_ticks(hi, n=5):
    step = 10 ** np.floor(np.log10(hi / n)); step *= next(m for m in (1, 2, 2.5, 5, 10) if hi / (step * m) <= n)
    return [round(step * i, 6) for i in range(int(hi // step) + 1)]


def scatter_svg(p, unit, W=360, H=330):
    """Ours (y) against the field value (x) on the same scale, with the 1:1 line. One dot per tree."""
    import html as Hm
    L, R, T, B = 48, 14, 10, 40                                   # margins
    hi = max(max(q[1] for q in p), max(q[2] for q in p)) * 1.05
    ticks = nice_ticks(hi); hi = max(hi, ticks[-1])
    X = lambda v: L + (W - L - R) * v / hi
    Y = lambda v: H - B - (H - T - B) * v / hi
    g = [f'<svg viewBox="0 0 {W} {H}" class="sc" role="img">']
    for t in ticks:
        g.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                 f'<text class="tick" x="{L - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{t:g}</text>'
                 f'<text class="tick" x="{X(t):.1f}" y="{H - B + 16}" text-anchor="middle">{t:g}</text>')
    g.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}"/>')
    g.append(f'<line class="one" x1="{X(0):.1f}" y1="{Y(0):.1f}" x2="{X(hi):.1f}" y2="{Y(hi):.1f}"/>')
    g.append(f'<text class="lab" x="{X(hi) - 4:.1f}" y="{Y(hi) + 14:.1f}" text-anchor="end">same as field</text>')
    g.append(f'<text class="axl" x="{(L + W - R) / 2:.0f}" y="{H - 6}" text-anchor="middle">field ({unit})</text>')
    g.append(f'<text class="axl" transform="translate(13,{(T + H - B) / 2:.0f}) rotate(-90)" text-anchor="middle">ours ({unit})</text>')
    for r, a, b in p:
        tip = Hm.escape(f"{r['id']} · {r['species']} · {r['plot']}|field {b:.2f} {unit}, ours {a:.2f} {unit}, "
                        f"error {a - b:+.2f} {unit}", quote=True)
        g.append(f'<circle cx="{X(b):.1f}" cy="{Y(a):.1f}" r="3.5" data-tip="{tip}"/>')
    g.append("</svg>")
    return "".join(g)


def fmt(v, d=2, sign=False):
    return "–" if v is None or not np.isfinite(v) else (f"{v:+.{d}f}" if sign else f"{v:.{d}f}")


def write_report(rows, path):
    import html as Hm, datetime
    cards, summary = [], []
    for label, ours, ref, unit, what in COMPARE:
        p = pairs(rows, ours, ref)
        if len(p) < 3: continue
        s = stats(p)
        summary.append(f"<tr><th>{label}</th><td class='t'>{Hm.escape(what)}</td><td>{s['n']:,}</td><td>{fmt(s['bias'], 2, True)}</td>"
                       f"<td><b>{fmt(s['med'])}</b></td><td>{fmt(s['pct'], 1)}%</td><td>{fmt(s['rmse'])}</td>"
                       f"<td>{fmt(s['r'])}</td><td>{unit}</td></tr>")
        cards.append(f'<div class="card"><h3>{label}</h3><p class="sub">vs {Hm.escape(what)} · {s["n"]:,} trees · '
                     f'typical error {fmt(s["med"])} {unit} ({fmt(s["pct"], 0)}%)</p>{scatter_svg(p, unit)}</div>')

    def by(key, keep):
        """median error per plot or species, for the main comparisons"""
        main = [c for c in COMPARE if c[1] in ("height_m", "crown_diam_m", "crown_base_m") and c[2] != "field_crown_base_m"]
        groups = sorted({r[key] for r in rows}); out = []
        for gname in groups:
            sub = [r for r in rows if r[key] == gname]
            cells = []
            for label, ours, ref, unit, what in main:
                p = pairs(sub, ours, ref)
                cells.append(f"<td>{fmt(stats(p)['med'])} <span class='n'>({len(p)})</span></td>" if len(p) >= 3 else "<td>–</td>")
            if keep(sub): out.append(f"<tr><th>{Hm.escape(gname)}</th><td>{len(sub)}</td>{''.join(cells)}</tr>")
        head = "".join(f"<th>{c[0]}<br><span class='n'>vs {Hm.escape(c[4])}</span></th>" for c in main)
        return f"<table><thead><tr><th></th><th>Trees</th>{head}</tr></thead><tbody>{''.join(out)}</tbody></table>"

    def worst(label, ours, ref, unit, k=15):
        p = sorted(pairs(rows, ours, ref), key=lambda q: -abs(q[1] - q[2]))[:k]
        body = "".join(f"<tr><td>{Hm.escape(r['id'])}</td><td class='t'>{Hm.escape(r['species'])}</td><td class='t'>{r['plot']}</td>"
                       f"<td>{fmt(r['height_m'], 1)}</td><td>{fmt(b)}</td><td>{fmt(a)}</td><td><b>{fmt(a - b, 2, True)}</b></td>"
                       f"<td>{r['points']:,}</td></tr>" for r, a, b in p)
        return (f"<h3>{label}: the {k} trees furthest from the field value</h3><table><thead><tr><th>Tree</th><th class='t'>Species</th>"
                f"<th class='t'>Plot</th><th>Height (m)</th><th>Field ({unit})</th><th>Ours ({unit})</th><th>Error ({unit})</th>"
                f"<th>Points</th></tr></thead><tbody>{body}</tbody></table>")

    d = np.array([r["canopy_density_pct"] for r in rows], float)
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Tree measurements vs field data</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
.viz-root{{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f2f1ee;--text-primary:#0b0b0b;--text-secondary:#52514e;
--text-muted:#7a7974;--grid:#e6e5e1;--axis:#b9b8b2;--series-1:#2a78d6;--one:#52514e}}
@media (prefers-color-scheme:dark){{.viz-root{{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242423;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--text-muted:#94938b;--grid:#2f2f2d;--axis:#55544f;--series-1:#3987e5;--one:#c3c2b7}}}}
body{{margin:0}}
.viz-root{{background:var(--surface-1);color:var(--text-primary);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
min-height:100vh;padding:28px 32px 60px;box-sizing:border-box}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:18px;margin:36px 0 8px}} h3{{font-size:15px;margin:22px 0 4px}}
p{{margin:4px 0;max-width:960px}} .sub,.n,.note{{color:var(--text-secondary)}} .n{{font-size:12px}}
table{{border-collapse:collapse;margin:8px 0;font-variant-numeric:tabular-nums}}
th,td{{padding:5px 12px;text-align:right;border-bottom:1px solid var(--grid)}} th:first-child,td:first-child,.t{{text-align:left}}
thead th{{color:var(--text-secondary);font-weight:600;vertical-align:bottom}}
.grid-cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:18px;margin-top:12px}}
.card{{background:var(--surface-2);border-radius:10px;padding:12px 14px}} .card h3{{margin:0}} .card .sub{{font-size:13px;margin:0 0 6px}}
svg.sc{{width:100%;height:auto;display:block}}
svg .grid{{stroke:var(--grid);stroke-width:1}} svg .axis{{stroke:var(--axis);stroke-width:1}}
svg .one{{stroke:var(--one);stroke-width:1.5;stroke-dasharray:5 4}}
svg .tick,svg .axl,svg .lab{{fill:var(--text-secondary);font-size:11px}} svg .axl{{font-size:12px}}
svg circle{{fill:var(--series-1);fill-opacity:.55;stroke:var(--surface-2);stroke-width:1}}
svg circle:hover{{fill-opacity:1;stroke:var(--text-primary);stroke-width:1.5}}
#tip{{position:fixed;pointer-events:none;background:var(--surface-1);color:var(--text-primary);border:1px solid var(--axis);
border-radius:6px;padding:6px 9px;font-size:13px;box-shadow:0 2px 10px rgba(0,0,0,.15);display:none;max-width:340px}}
#tip b{{display:block}}
</style></head><body><div class="viz-root">
<h1>Tree measurements compared with the field</h1>
<p class="sub">{len(rows):,} trees from 6 German forest plots, each cut out of the drone point cloud by hand and measured on the
ground (<a href="https://doi.org/10.1594/PANGAEA.942856">Weiser et al. 2022</a>). Measured with the same code as the app
(treeseg.tree_table). Made {datetime.date.today():%d %b %Y}.</p>
<p class="note">The trees are already separated, so this checks only how each tree is measured, not how trees are found.
Error = ours minus field. "Typical error" is the median: half the trees are closer than this.</p>

<h2>Summary</h2>
<table><thead><tr><th>Measurement</th><th class='t'>Field value</th><th>Trees</th><th>Bias</th><th>Typical error</th><th>Typical error %</th>
<th>RMSE</th><th>r</th><th>Unit</th></tr></thead><tbody>{''.join(summary)}</tbody></table>
<p class="note">Bias: average error, + means ours is higher. RMSE weighs large errors more. r: 1 means the same ordering as the field.
Crown area has no field measurement: it is compared with a circle of the field crown diameter, so its % error is about twice the
diameter's. Canopy density: a value for {int(np.isfinite(d).sum()):,} of {len(d):,} trees, median {np.nanmedian(d):.0f}% (no field value).</p>

<h2>Ours against the field, one dot per tree</h2>
<p class="note">Dots on the dashed line are exactly right; above it ours is higher. Hover a dot for the tree.</p>
<div class="grid-cards">{''.join(cards)}</div>

<h2>Typical error by plot</h2>
{by("plot", lambda sub: True)}
<h2>Typical error by species</h2>
<p class="note">Species with at least 15 trees. The number of trees compared is in brackets.</p>
{by("species", lambda sub: len(sub) >= 15)}

<h2>Largest errors</h2>
<p class="note">Worth opening in CloudCompare: data/Weiser2022/&lt;plot&gt;/&lt;tree&gt;_*_ULS-on.laz. All trees are in validation.csv.</p>
{worst("Crown diameter", "crown_diam_m", "field_crown_diam_m", "m")}
{worst("Crown base (lowest living branch)", "crown_base_m", "field_crown_base_green_m", "m")}
<div id="tip"></div>
</div>
<script>
const tip = document.getElementById("tip");
document.addEventListener("mousemove", e => {{
  const t = e.target.closest && e.target.closest("circle[data-tip]");
  if (!t) {{ tip.style.display = "none"; return; }}
  const [head, body] = t.dataset.tip.split("|");
  tip.innerHTML = ""; const b = document.createElement("b"); b.textContent = head; tip.append(b, body);
  tip.style.display = "block";
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8), y = Math.min(e.clientY + 14, innerHeight - tip.offsetHeight - 8);
  tip.style.left = x + "px"; tip.style.top = y + "px";
}});
</script></body></html>"""
    path.write_text(html, encoding="utf-8")

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
    print(f"\n{len(rows)} trees with a leaf-on drone cloud, compared with what was measured in the field. "
          f"error = ours - field")
    print(f"  {'':58s} trees     bias  median   RMSE  median      r\n  {'':58s}               |error|         |error|%")
    line(rows, "height (field: hypsometer, about 1 tree in 10)", "height_m", "field_height_m")
    line(rows, "crown diameter (field: dripline, 2 directions, mean)", "crown_diam_m", "field_crown_diam_m")
    line(rows, "crown width, widest (vs the field crown diameter)", "crown_width_max_m", "field_crown_diam_m")
    line(rows, "crown area (vs a circle with the field diameter)", "crown_area_m2", "field_crown_area_m2", "m2")
    line(rows, "crown base (field: lowest living branch)", "crown_base_m", "field_crown_base_green_m")
    line(rows, "crown base (field: lowest branch of 1 m or longer)", "crown_base_m", "field_crown_base_m")
    d = np.array([r["canopy_density_pct"] for r in rows], float)
    print(f"  canopy density: empty for {int(np.isnan(d).sum())} of {len(d)} trees, median {np.nanmedian(d):.0f}%")
    write_report(rows, data / "report.html")
    print(f"\n-> {data / 'validation.csv'}\n-> {data / 'report.html'}")


if __name__ == "__main__":
    main()
