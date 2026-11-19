"""The report (RES-11): the same report.html that run_segmentanytree.bat makes (process2_segment.py:
CHM watershed, SegmentAnyTree before and after joining crowns, figures), with one more section, "Trees
to check", that lists only the trees worth a second look instead of every tree.

The page is made with the same markdown-to-HTML function as process2_segment.py (copied below so the
app does not import the processing scripts), so it looks exactly like the old reports. Tree IDs in
"Trees to check" come from the app's tree table, so they match _trees.csv and _info.laz.
"""
from __future__ import annotations

import base64
import re
import statistics
from pathlib import Path

from . import steps, summary

MAX_ROWS = 300                    # trees listed; the rest are counted
LARGE_CROWN = 3.0                 # crown area at least this many times the median crown area
LONG_CROWN = 2.0                  # widest crown width at least this many times the crown diameter
MIN_LARGE_M2 = 20.0               # a crown this small is never called large
DROP_NOTES = ("- Outputs per model are in sub-folders", "- `run_metadata.json` records")   # files the app does not save
REASONS = {
    "large": "Very large crown: may be several trees found as one",
    "long": "Long, narrow crown: may be two trees side by side",
    "edge": "At the edge of the data: the crown may be cut",
    "few_points": "Fewer than 20 points: numbers are rough",
}


def html_from_md(md, folder):
    """Tiny markdown -> standalone HTML (tables, headings, images embedded). Same as process2_segment.py."""
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


def _num(t, key):
    v = t.get(key)
    return v if isinstance(v, (int, float)) else None


def trees_to_check(trees: list) -> list:
    """(tree, [reason keys]) for every tree worth a second look, the most likely mistakes first."""
    areas = [a for a in (_num(t, "crown_area_m2") for t in trees) if a]
    median = statistics.median(areas) if areas else None
    out = []
    for t in trees:
        why = []
        area, diam, width = _num(t, "crown_area_m2"), _num(t, "crown_diam_m"), _num(t, "crown_width_max_m")
        if median and area and area >= max(LARGE_CROWN * median, MIN_LARGE_M2):
            why.append("large")
        if diam and width and width >= LONG_CROWN * diam:
            why.append("long")
        flag = t.get("qc_flag") or "ok"
        why += [f for f in ("edge", "few_points") if f in flag.split(",")]
        if why:
            out.append((t, why))
    order = {"large": 0, "long": 1, "edge": 2, "few_points": 3}
    out.sort(key=lambda tw: (min(order[w] for w in tw[1]), -(_num(tw[0], "crown_area_m2") or 0)))
    return out


def _cell(v, fmt="{:.1f}"):
    return "" if v is None else (fmt.format(v) if isinstance(v, (int, float)) else str(v).replace("|", "/"))


def check_section(trees: list) -> str:
    """The "Trees to check" section, in the report's markdown."""
    found = trees_to_check(trees)
    md = ["## Trees to check\n",
          f"{len(found):,} of {len(trees):,} trees (SegmentAnyTree + merge_sat) are worth a look before their numbers "
          "are used. The other trees showed no sign of a problem. Tree IDs are the same as in the tree table "
          "(`_trees.csv`) and the coloured point cloud (`_info.laz`, field TreeID); X and Y are the tree top in the "
          "file's coordinates.\n"]
    if not found:
        return "\n".join(md)
    md.append("| Tree ID | Why to check | Height (m) | Crown diameter (m) | Crown width, widest (m) | Crown area (m2) | X | Y |")
    md.append("|---|---|---|---|---|---|---|---|")
    for t, why in found[:MAX_ROWS]:
        md.append("| " + " | ".join([_cell(t.get("tree_uid")), "; ".join(REASONS[w] for w in why),
                                    _cell(_num(t, "height_m")), _cell(_num(t, "crown_diam_m")),
                                    _cell(_num(t, "crown_width_max_m")), _cell(_num(t, "crown_area_m2")),
                                    _cell(_num(t, "top_x"), "{:.2f}"), _cell(_num(t, "top_y"), "{:.2f}")]) + " |")
    if len(found) > MAX_ROWS:
        md.append(f"\n{len(found) - MAX_ROWS:,} more trees to check are not listed here; filter the tree table in the app.")
    md.append(f"\nHow they were picked: crown area at least {LARGE_CROWN:g} times the median crown area "
              f"(and over {MIN_LARGE_M2:g} m2); widest crown width at least {LONG_CROWN:g} times the crown diameter; "
              "or the quality check of the tree table (fewer than 20 points, or the top within 2 m of the edge).")
    return "\n".join(md) + "\n"


def make_report_text(md: str, trees: list) -> str:
    """The report.md of process2_segment.py with "Trees to check" before the figures."""
    lines = [l for l in md.splitlines() if not l.startswith(DROP_NOTES)]
    text = "\n".join(lines) + "\n"
    section = check_section(trees)
    at = text.find("## Figures")
    return text[:at] + section + "\n" + text[at:] if at >= 0 else text + "\n" + section


def build(workdir) -> int:
    """Write <workdir>/report.html from <workdir>/report/report.md and the tree table. Returns the number
    of trees to check. Also removes the full-size point clouds process2_segment.py leaves (one per model)."""
    w = Path(workdir)
    folder = w / steps.REPORT_DIR
    trees = summary.load_trees(w / steps.TREES_CSV)
    text = make_report_text((folder / "report.md").read_text(encoding="utf-8"), trees)
    part = w / (steps.REPORT_HTML + ".part")
    part.write_text(html_from_md(text, folder), encoding="utf-8")
    part.replace(w / steps.REPORT_HTML)
    for big in folder.glob("*/points_treeid.laz"):
        try:
            big.unlink()
        except OSError:
            pass
    return len(trees_to_check(trees))
