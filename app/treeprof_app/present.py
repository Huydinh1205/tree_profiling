"""What the screens show, worked out without any screen code: wording, number formats, table rows,
the geometry and colours of the crown map. The Qt screens (treeprof_app/gui) only put this on screen,
so all of it can be tested without a window.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

from . import checks, crowns, naming, steps, store as st, summary

ACCENT = "#1F6B4A"
RAMP = ["#CFE8D8", "#9CCFB0", "#5FAE85", "#2F8560", "#0F4A32"]         # light to dark, also for colour-blind eyes
STATUS = {                       # status -> (words, sign, background, text colour)   SYS-8: never colour alone
    st.QUEUED: ("Waiting in queue", "…", "#ECEFEC", "#3A453F"),
    st.RUNNING: ("Running", "▶", "#E6EEFA", "#1D4F91"),
    st.FINISHED: ("Finished", "✓", "#E2F1E8", "#17553A"),
    st.FAILED: ("Failed", "✕", "#FBE5E2", "#8E2118"),
    st.CANCELLED: ("Cancelled", "■", "#ECEFEC", "#3A453F"),
    st.INTERRUPTED: ("Interrupted", "‖", "#FFF1C9", "#6B4700"),
}
LEVEL = {checks.OK: ("✓", "#1F6B4A"), checks.WARNING: ("!", "#6B4700"), checks.ERROR: ("✕", "#8E2118")}
QC_LABEL = {"ok": "OK", "few_points": "Check: few points", "edge": "Check: at the edge",
            "few_points,edge": "Check: few points, at the edge"}
COLUMNS = [                      # (csv column, label, shown at first)                RES-4
    ("tree_uid", "Tree ID", True), ("height_m", "Height (m)", True), ("crown_diam_m", "Crown diameter (m)", True),
    ("crown_area_m2", "Crown area (m²)", True), ("crown_volume_m3", "Crown volume (m³)", True),
    ("qc_label", "Quality check", True), ("crown_width_max_m", "Crown width, widest (m)", False),
    ("foliage_volume_m3", "Foliage volume (m³)", False), ("crown_base_m", "Crown starts at (m)", False),
    ("crown_depth_m", "Crown depth (m)", False), ("canopy_density_pct", "Canopy density (%)", False),
    ("nn_spacing_m", "Nearest tree (m)", False), ("n_points", "Points", False),
    ("h_p25_m", "Height, 25% of points below (m)", False), ("h_p50_m", "Height, 50% of points below (m)", False),
    ("h_p75_m", "Height, 75% of points below (m)", False), ("height_sd_m", "Height spread (m)", False),
    ("point_density_m2", "Points per m²", False), ("top_x", "X of the top", False), ("top_y", "Y of the top", False),
    ("top_z", "Z of the top", False),
]
TEXT_COLUMNS = ("tree_uid", "qc_label")
COLOUR_BY = {"height_m": "Height (m)", "crown_area_m2": "Crown area (m²)", "crown_volume_m3": "Crown volume (m³)",
             "qc_flag": "Quality check"}
QC_COLOURS = {                   # told apart by colour-blind eyes too, and by how light or dark they are
    "ok": (0xCF, 0xE8, 0xD8), "edge": (0xE6, 0x9F, 0x00), "few_points": (0x00, 0x72, 0xB2),
    "few_points,edge": (0x3B, 0x0F, 0x4A),
}
QC_SHORT = {"ok": "OK", "edge": "At the edge", "few_points": "Few points", "few_points,edge": "Few points, at the edge"}
MAP_DETAIL = ((3000, 32), (12000, 16))      # up to this many trees: at most this many points per crown outline


# ---------------------------------------------------------------------------------- words and numbers
def fmt_value(value) -> str:
    if value is None or value == "":
        return "–"
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def fmt_capture(iso: Optional[str]) -> str:
    try:
        return naming.format_capture_date(dt.date.fromisoformat(iso)) if iso else ""
    except ValueError:
        return iso or ""


def fmt_when(iso: Optional[str], today: Optional[dt.date] = None) -> str:
    try:
        d = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if d.date() == (today or dt.date.today()):
        return f"Today {d:%H:%M}"
    return f"{d.day} {d:%b %y}, {d:%H:%M}"


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d} min"


def fmt_bytes(n: Optional[float]) -> str:
    if n is None:
        return ""
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{int(n)} bytes"


def title_of(site: str, capture_iso: Optional[str]) -> str:
    date = fmt_capture(capture_iso)
    return f"{site} – {date}" if date else site


def run_title(run: dict) -> str:
    return title_of(run["site"], run.get("capture_date"))


def ground_text(source) -> Optional[str]:
    """What tree_info.py printed about the ground, in plain words."""
    text = str(source or "").strip()
    if not text:
        return None
    if text.lower().startswith("csf"):
        return "Worked out from the points (no ground marked in the file)"
    if text.lower().startswith("file"):
        return "Ground points marked in the file"
    return text


# ---------------------------------------------------------------------------------- New run
def file_facts(report: checks.FileReport, estimate_minutes: Optional[float],
               max_points: int = steps.DEFAULT_MAX_POINTS) -> list:
    """NEW-2, NEW-6: (label, value) of a chosen point cloud; [] when its header cannot be read."""
    h = report.header
    if not h:
        return []
    return [("Size", fmt_bytes(report.size_bytes)), ("Points", f"{h.points:,}"),
            ("Area covered", f"{h.width:,.0f} m × {h.height:,.0f} m"),
            ("Density", f"{h.bbox_density:,.0f} points per m²"),
            ("Coordinate system", report.crs_name if report.crs_name not in (None, "?") else "–"),
            ("Time needed", f"About {fmt_duration(estimate_minutes * 60)}" if estimate_minutes else "No estimate yet"),
            ("Trees found in", tiles_text(h.points, max_points))]


def tiles_text(points: int, max_points: int) -> str:
    """NEW-9: whether sat_docker.py runs the file in one go or cuts it into tiles (it cuts when points > max)."""
    if points <= max_points:
        return "One go"
    return f"Tiles of up to {max_points:,} points"


def start_problem(files: list, run_by: str) -> Optional[str]:
    """NEW-4, NEW-5, NEW-8: why Start cannot go ahead, or None. files: dicts with report, site, date."""
    if not files:
        return "Choose a point cloud first."
    if not all(f["report"].can_start for f in files):
        return "One of the files cannot be run. Remove it or fix the problem shown in red."
    if not (run_by or "").strip():
        return "Enter your name in 'Run by'."
    for f in files:
        if not (f["site"] or "").strip():
            return "Every file needs a site name."
        if (f["date"] or "").strip() and naming.parse_user_date(f["date"]) is None:
            return f"'{f['date']}' is not a date. Write it like 13 Feb 25, or leave it empty."
    return None


# ---------------------------------------------------------------------------------- Running
def step_rows(view: dict, now: Optional[float] = None) -> list:
    """RUN-1 to RUN-4: one dict per step: key, name, state (waiting / running / done), right (text at the
    right), label, eta, fraction (None = cannot tell how far)."""
    live, done = view.get("live"), view.get("steps_done") or {}
    now = now if now is not None else dt.datetime.now().timestamp()
    rows = []
    for step in steps.STEP_ORDER:
        row = {"key": step, "name": steps.STEP_LABELS[step], "state": "waiting", "right": "Waiting", "label": "",
               "eta": "", "fraction": 0.0}
        running = bool(live and live.steps.get(step) == "running" and view["status"] == st.RUNNING)
        if step in done or (live and live.steps.get(step) == "done"):
            seconds = done.get(step, {}).get("seconds", live.seconds.get(step) if live else None)
            row.update(state="done", right=f"Done · {fmt_duration(seconds)}" if seconds is not None else "Done")
        elif running:
            started = live.started.get(step)
            row.update(state="running", right=f"Running · {fmt_duration(now - started)} so far" if started else "Running",
                       fraction=live.fraction,
                       eta=f"About {fmt_duration(live.eta_minutes * 60)} left" if live.eta_minutes else "")
            if step == "copy" and live.copy:
                row["label"] = f"{fmt_bytes(live.copy[0])} of {fmt_bytes(live.copy[1])}"
            else:
                row["label"] = live.label or ""
        rows.append(row)
    return rows


def current_step_text(view: dict) -> str:
    """HOME-4: 'Step 2 of 5: Find trees · Tile 7 of 18'."""
    live = view.get("live")
    step = next((s for s in steps.STEP_ORDER if live and live.steps.get(s) == "running"), None)
    if not step:
        return ""
    text = f"Step {steps.STEP_ORDER.index(step) + 1} of {len(steps.STEP_ORDER)}: {steps.STEP_LABELS[step]}"
    if live.label and step == "find_trees":
        text += f" · {live.label}"
    return text


def failure_buttons(error_kind: Optional[str]) -> list:
    """RUN-8: which buttons a failed run offers, in order."""
    out = []
    if error_kind in ("out_of_ram", "gpu_memory"):
        out.append("Try again with smaller tiles")
    if error_kind not in ("unreadable_input", "input_missing", "docker_missing"):
        out.append("Try again")
    return out + ["Send log to Ryan", "Delete this run"]


# ---------------------------------------------------------------------------------- Results
def summary_cards(s: dict) -> list:
    """RES-1: (label, value, small print)."""
    qc = s.get("qc") or {}
    to_check = sum(v for k, v in qc.items() if k != "ok")
    return [("Trees", fmt_value(s.get("trees")), ""), ("Mean height", f"{fmt_value(s.get('height_mean_m'))} m", ""),
            ("Tallest", f"{fmt_value(s.get('height_max_m'))} m", ""),
            ("Area surveyed", f"{fmt_value(s.get('area_ha'))} ha" if s.get("area_ha") else "–", ""),
            ("Trees per hectare", fmt_value(s.get("trees_per_ha")) if s.get("trees_per_ha") else "–", ""),
            ("Trees to check", fmt_value(to_check),
             f"{qc.get('edge', 0) + qc.get('few_points,edge', 0):,} at the edge, "
             f"{qc.get('few_points', 0) + qc.get('few_points,edge', 0):,} with few points")]


def tree_facts(tree: dict) -> list:
    """RES-6: (label, value with unit) of the selected tree."""
    rows = [("Height", "height_m", "m"), ("Crown diameter", "crown_diam_m", "m"), ("Crown area", "crown_area_m2", "m²"),
            ("Crown volume", "crown_volume_m3", "m³"), ("Crown starts at", "crown_base_m", "m"),
            ("Nearest tree", "nn_spacing_m", "m"), ("Points", "n_points", "")]
    out = [(label, f"{fmt_value(tree.get(key))} {unit}".strip()) for label, key, unit in rows]
    return out + [("Quality check", QC_LABEL.get(tree.get("qc_flag") or "ok", str(tree.get("qc_flag"))))]


def qc_help(tree: dict) -> str:
    return summary.QC_HELP.get(tree.get("qc_flag") or "ok", "")


def add_labels(trees: list) -> None:
    for t in trees:
        t["qc_label"] = QC_LABEL.get(t.get("qc_flag") or "ok", t.get("qc_flag"))


def filter_trees(trees: list, find: str = "", hmin: Optional[float] = None, hmax: Optional[float] = None,
                 qc: str = "all") -> list:
    """RES-4: search by Tree ID, height range, quality check ('all', 'ok', 'check')."""
    out = trees
    text = (find or "").strip().lower()
    if text:
        out = [t for t in out if text in str(t.get("tree_uid") or "").lower()]
    if hmin is not None:
        out = [t for t in out if t.get("height_m") is not None and t["height_m"] >= hmin]
    if hmax is not None:
        out = [t for t in out if t.get("height_m") is not None and t["height_m"] <= hmax]
    if qc == "ok":
        out = [t for t in out if (t.get("qc_flag") or "ok") == "ok"]
    elif qc == "check":
        out = [t for t in out if (t.get("qc_flag") or "ok") != "ok"]
    return out


def sort_trees(rows: list, key: str, descending: bool = False) -> list:
    """Empty cells always come last, whichever way the column is sorted."""
    have = sorted((t for t in rows if t.get(key) is not None), key=lambda t: t[key], reverse=descending)
    return have + [t for t in rows if t.get(key) is None]


def tallest(trees: list):
    """The tree shown first: the tallest one."""
    have = [t for t in trees if t.get("height_m") is not None]
    if have:
        return max(have, key=lambda t: t["height_m"]).get("tree_id")
    return trees[0].get("tree_id") if trees else None


def run_info_rows(project: dict, saves: Optional[list] = None) -> list:
    """RES-8: (label, value). saves is None for results opened from a saved folder."""
    inp, pipe = project.get("input") or {}, project.get("pipeline") or {}
    tiles = pipe.get("tiles") or {}
    seconds = pipe.get("step_seconds") or {}
    rows = [("Point cloud", inp.get("path")), ("Size", fmt_bytes(inp.get("size_bytes"))),
            ("Points", fmt_value(inp.get("points"))), ("Coordinate system", inp.get("crs")),
            ("Ground used for heights", ground_text(inp.get("ground_source"))),
            ("Site", project.get("site_name")), ("Capture date", fmt_capture(project.get("capture_date"))),
            ("Run by", project.get("run_by")), ("Started", fmt_when(project.get("started_at"))),
            ("Finished", fmt_when(project.get("finished_at"))), ("Computer", project.get("machine")),
            ("Time per step", ", ".join(f"{steps.STEP_LABELS.get(k, k)} {fmt_duration(v)}" for k, v in seconds.items())),
            ("Tiles", f"{tiles.get('count')} ({tiles.get('thinned') or 0} run with fewer points)"
             if tiles.get("count") else "Not split into tiles"),
            ("Max points per tile", fmt_value(pipe.get("max_points"))),
            ("Trees before joining crowns", fmt_value(pipe.get("trees_before_merge"))),
            ("Trees after joining crowns", fmt_value(pipe.get("trees_after_merge"))),
            ("Tree-finding model", pipe.get("sat_image")), ("Scripts version", pipe.get("scripts_commit")),
            ("App version", project.get("app_version"))]
    if saves is not None:
        rows.append(("Saved to", "; ".join(s["folder"] for s in saves) if saves else "Not saved yet"))
    return [(label, str(value) if value not in (None, "") else "–") for label, value in rows]


# ---------------------------------------------------------------------------------- crown map
def map_shapes(trees: list, outlines: dict) -> tuple:
    """RES-3: (shapes, width, height). One shape per tree, in metres from the middle of the site with y
    pointing DOWN (screen direction), so the picture keeps the true shape and size:
        (tree_id, "ring", [(x, y), ...])     the crown outline (thinned when it has many points)
        (tree_id, "circle", (x, y, radius))  a tree without an outline, and every tree of a very big site
    A tree with neither an outline nor a place is left out."""
    n = next((pts for limit, pts in MAP_DETAIL if len(trees) <= limit), 0)
    raw = []
    for t in trees:
        ring = outlines.get(t.get("tree_id")) if n else None
        if ring:
            raw.append((t["tree_id"], "ring", ring if len(ring) <= n else crowns.simplify(ring, n)))
        elif t.get("top_x") is not None and t.get("top_y") is not None:
            raw.append((t["tree_id"], "circle", (t["top_x"], t["top_y"], max((t.get("crown_diam_m") or 2.0) / 2, 0.5))))
    xs, ys = [], []
    for _, kind, geo in raw:
        if kind == "ring":
            xs += [p[0] for p in geo]
            ys += [p[1] for p in geo]
        else:
            xs += [geo[0] - geo[2], geo[0] + geo[2]]
            ys += [geo[1] - geo[2], geo[1] + geo[2]]
    if not raw:
        return [], 10.0, 10.0
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    shapes = []
    for tree_id, kind, geo in raw:
        if kind == "ring":
            shapes.append((tree_id, kind, [(x - cx, cy - y) for x, y in geo]))
        else:
            shapes.append((tree_id, kind, (geo[0] - cx, cy - geo[1], geo[2])))
    return shapes, max(max(xs) - min(xs), 10.0), max(max(ys) - min(ys), 10.0)


def value_range(trees: list, key: str) -> tuple:
    values = [t[key] for t in trees if t.get(key) is not None]
    return (min(values), max(values)) if values else (0.0, 1.0)


def map_colours(trees: list, key: str) -> tuple:
    """RES-3: (colour of each tree, legend) for the chosen 'Colour by'.
    colours: tree_id -> (r, g, b). legend: ("scale", lowest, highest) for a number, or
    ("classes", [((r, g, b), words, number of trees), ...]) for the quality check (only classes that occur)."""
    if key == "qc_flag":
        flags = [(t.get("qc_flag") or "ok") for t in trees]
        colours = {t["tree_id"]: QC_COLOURS.get(f, QC_COLOURS["few_points,edge"]) for t, f in zip(trees, flags)}
        classes = [(QC_COLOURS[f], QC_SHORT[f], flags.count(f)) for f in QC_COLOURS if f in flags or f == "ok"]
        other = sum(1 for f in flags if f not in QC_COLOURS)
        if other:
            classes.append((QC_COLOURS["few_points,edge"], "Other", other))
        return colours, ("classes", classes)
    lo, hi = value_range(trees, key)
    return {t["tree_id"]: ramp_rgb(t.get(key), lo, hi) for t in trees}, ("scale", lo, hi)


def ramp_rgb(value, lo: float, hi: float) -> tuple:
    """Colour of a value on the light-to-dark green scale, as (r, g, b) 0..255. No value: the lightest."""
    stops = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in RAMP]
    if value is None or hi <= lo:
        return stops[0]
    f = min(max((value - lo) / (hi - lo), 0.0), 1.0) * (len(stops) - 1)
    i = min(int(f), len(stops) - 2)
    a, b, k = stops[i], stops[i + 1], f - i
    return tuple(int(round(a[j] + (b[j] - a[j]) * k)) for j in range(3))


# ---------------------------------------------------------------------------------- Save, Storage
def save_problem(parent: str, name: str) -> Optional[str]:
    """SAVE-1, SAVE-2, SAVE-4: why these cannot be used, or None."""
    parent = (parent or "").strip()
    clean = naming.safe_name(name or "", fallback="")
    if not parent or not Path(parent).is_dir():
        return "Choose the folder to save in."
    if not clean:
        return "Give the new folder a name."
    if naming.path_too_long(Path(parent) / clean / (clean + "_sat_merged.laz")):
        return "This folder name makes the path too long for Windows. Use a shorter name."
    return None


def recent_line(entry: dict) -> tuple:
    """HOME-5: (title, small print) of saved results that were opened lately."""
    trees = f"{entry['trees']:,} trees · " if entry.get("trees") is not None else ""
    return (title_of(entry.get("site") or "?", entry.get("capture_date")),
            f"{trees}opened {fmt_when(entry.get('opened_at'))} · {Path(entry['path']).parent}")


def storage_line(row: dict) -> tuple:
    """STO-3: (title, small print, needs attention) of one run in the storage list."""
    title = title_of(row["site"], row.get("capture_date"))
    if row["saved"]:
        state = "Saved"
    elif row["status"] == st.FINISHED:
        state = "Not saved"
    else:
        state = STATUS.get(row["status"], (row["status"].capitalize(),))[0]
    text = f"{fmt_when(row['created_at'])} · {fmt_bytes(row['size']) or '0 bytes'} · {state}"
    return title, text, (not row["saved"] and row["status"] == st.FINISHED)


def removal_note(due: Optional[dt.datetime], now: Optional[dt.datetime] = None, warn_days: int = 7) -> str:
    """STO-6: shown next to a run that was never saved, in its last week on this computer."""
    if due is None:
        return ""
    now = now or dt.datetime.now().astimezone()
    days = (due.date() - now.date()).days
    if days > warn_days:
        return ""
    if days <= 0:
        return "Removed today"
    return "Removed tomorrow" if days == 1 else f"Removed in {days} days"


def storage_note(keep_days: int) -> str:
    """STO-6: what happens to runs on this computer, above the storage list."""
    later = "A saved run can still be opened from the folder you saved it in."
    if not keep_days:
        return ("Runs are never removed by themselves. Delete the local copy of a run when you no longer need it "
                f"here. {later}")
    days = "1 day" if keep_days == 1 else f"{keep_days} days"
    return (f"Runs are removed from this computer {days} after they were last finished or saved, and their "
            f"intermediate tiles once they have finished. {later} A run that was never saved is gone for good, "
            "so save what you need.")
