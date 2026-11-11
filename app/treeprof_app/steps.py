"""The steps of one run: the exact commands (the same ones run_segmentanytree.bat and tree_info.bat
use), the files each step must leave behind, and the rule that says a step is really finished.

Why the app keeps its own record of finished steps instead of looking at which files exist (spec 6.3):
  * sat_docker.py runs Docker again when its --out already exists with the right number of points
    (only the .bat skips it), so the app must not call it again once the step is done;
  * merge_sat.py and tree_info.py write straight to their final file names, so a run that was stopped
    half way can leave a file that exists but is incomplete.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

from . import lasheader

STEP_ORDER = ("copy", "find_trees", "join", "measure", "prepare_view")
STEP_LABELS = {"copy": "Copy file", "find_trees": "Find trees", "join": "Join split crowns",
               "measure": "Measure trees", "prepare_view": "Prepare view"}
SCRIPT_STEPS = ("find_trees", "join", "measure")       # steps done by a script of ../processes
DEFAULT_MAX_POINTS = 20_000_000                        # sat_docker.py MAX_POINTS

SAT_RAW = "sat_result.laz"
SAT_MERGED = "sat_result_merged.laz"
INFO_LAZ = "sat_result_merged_info.laz"
TREES_CSV = "sat_result_merged_trees.csv"
CROWNS_SHP = "sat_result_merged_crowns.shp"
SAT_LOG = os.path.join("log", "sat_docker.log")

_OUTPUTS = {
    "find_trees": (SAT_RAW,),
    "join": (SAT_MERGED,),
    "measure": (INFO_LAZ, TREES_CSV, CROWNS_SHP, "sat_result_merged_crowns.shx", "sat_result_merged_crowns.dbf"),
}
_SAME_POINTS = {"find_trees": (SAT_RAW,), "join": (SAT_MERGED,), "measure": (INFO_LAZ,)}


def command(step: str, *, python: Union[str, Sequence[str]], workdir, input_path=None, site: Optional[str] = None,
            max_points: int = DEFAULT_MAX_POINTS) -> list:
    """Command line of a script step. Run it with the processes folder as the working directory.
    python: the interpreter, e.g. r"C:\\...\\python.exe" or ["py", "-3.10"]."""
    py = [python] if isinstance(python, str) else list(python)
    w = Path(workdir)
    if step == "find_trees":
        cmd = py + ["sat_docker.py", str(input_path), "--out", str(w / SAT_RAW), "--log", str(w / SAT_LOG)]
        if max_points != DEFAULT_MAX_POINTS:
            cmd += ["--max-points", str(int(max_points))]
        return cmd
    if step == "join":
        return py + ["merge_sat.py", str(w / SAT_RAW)]
    if step == "measure":
        cmd = py + ["tree_info.py", str(w / SAT_MERGED)]
        if site:
            cmd += ["--site", site]
        return cmd
    raise ValueError(f"{step} is not a script step")


def child_env(base: Optional[dict] = None) -> dict:
    """Environment for the scripts (spec 6.2): merge_sat.py and tree_info.py do not flush what they
    print, and Windows pipes are not UTF-8 by default."""
    env = dict(os.environ if base is None else base)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def expected_outputs(step: str, workdir) -> list:
    return [Path(workdir) / name for name in _OUTPUTS.get(step, ())]


@dataclass(frozen=True)
class StepCheck:
    done: bool
    reason: str = ""


def check_step_done(step: str, workdir, exit_code: int, input_points: Optional[int]) -> StepCheck:
    """Spec 6.3: exit code 0, every expected file there, and the LAZ outputs hold the input's points."""
    if exit_code != 0:
        return StepCheck(False, f"the step ended with exit code {exit_code}")
    for p in expected_outputs(step, workdir):
        if not p.is_file() or p.stat().st_size == 0:
            return StepCheck(False, f"{p.name} is missing")
    for name in _SAME_POINTS.get(step, ()):
        p = Path(workdir) / name
        try:
            n = lasheader.point_count(p)
        except lasheader.LasHeaderError as e:
            return StepCheck(False, str(e))
        if input_points is not None and n != input_points:
            return StepCheck(False, f"{name} has {n:,} points, the input has {input_points:,}")
    return StepCheck(True)


def next_step(done_steps) -> Optional[str]:
    """First step not finished yet (resume point), or None when all are done."""
    done = set(done_steps)
    for s in STEP_ORDER:
        if s not in done:
            return s
    return None
