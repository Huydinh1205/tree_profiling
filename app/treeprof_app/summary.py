"""Read the tree table written by tree_info.py and work out the overview numbers of the Results
screen (RES-1). The table is only read; a column that is missing or empty is tolerated.
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Optional

INT_COLUMNS = ("tree_id", "n_points")
FLOAT_COLUMNS = ("top_x", "top_y", "top_z", "height_m", "crown_diam_m", "crown_width_max_m", "crown_area_m2",
                 "crown_volume_m3", "foliage_volume_m3", "crown_base_m", "crown_depth_m", "canopy_density_pct",
                 "nn_spacing_m", "h_p25_m", "h_p50_m", "h_p75_m", "height_sd_m", "point_density_m2",
                 "species_prob", "confidence", "koala_value")
QC_FLAGS = ("ok", "few_points", "edge", "few_points,edge")
QC_HELP = {
    "ok": "No issue found.",
    "few_points": "Fewer than 20 points: the numbers of this tree are rough.",
    "edge": "The tree top is within 2 m of the edge of the data: the crown may be cut.",
    "few_points,edge": "Fewer than 20 points, and the tree top is within 2 m of the edge of the data.",
}


def _number(text, kind):
    text = (text or "").strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return int(float(text)) if kind is int else float(text)
    except ValueError:
        return None


def load_trees(path) -> list:
    """One dictionary per tree. Numbers are int / float, an empty cell is None."""
    trees = []
    with open(Path(path), newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            tree = {}
            for key, value in row.items():
                if key is None:
                    continue
                if key in INT_COLUMNS:
                    tree[key] = _number(value, int)
                elif key in FLOAT_COLUMNS:
                    tree[key] = _number(value, float)
                else:
                    tree[key] = (value or "").strip() or None
            trees.append(tree)
    return trees


def summarise(trees: list, area_ha: Optional[float] = None) -> dict:
    """The 'summary' section of the .treeprof file."""
    heights = [t["height_m"] for t in trees if t.get("height_m") is not None]
    qc = {flag: 0 for flag in QC_FLAGS}
    for t in trees:
        flag = t.get("qc_flag") or "ok"
        qc[flag] = qc.get(flag, 0) + 1
    out = {
        "trees": len(trees),
        "height_mean_m": round(statistics.fmean(heights), 2) if heights else None,
        "height_median_m": round(statistics.median(heights), 2) if heights else None,
        "height_max_m": round(max(heights), 2) if heights else None,
        "area_ha": round(area_ha, 2) if area_ha else None,
        "trees_per_ha": round(len(trees) / area_ha, 1) if area_ha else None,
        "qc": qc,
    }
    return out
