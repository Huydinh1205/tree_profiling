"""Turn the lines the scripts print into progress for the Running screen (spec 6.4), and a failed
step's output into a plain message with the right buttons (spec 6.5).

The scripts are not changed, so this module depends on the wording of their messages. The tests keep
samples of that wording: if a script's messages change, the tests fail and this file is updated.
A parser never raises: a line it does not understand is ignored (the bar becomes indeterminate).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------- sat_docker.py ("Find trees")
_RE_START = re.compile(r"^SegmentAnyTree on .*?(?: \(([\d,]+) points\))?\. Docker output")
_RE_DOWNLOAD = re.compile(r"^Downloading the SegmentAnyTree image")
_RE_TILED = re.compile(r"^Too many points for one SegmentAnyTree run \(.*?\): (\d+) x (\d+) tiles .*?, "
                       r"(\d+) Docker runs")
_RE_TILE = re.compile(r"^--- tile (\d+)/(\d+) \(column (\d+), row (\d+)\)")
_RE_SKIPPED = re.compile(r": already done \(.*\), skipped\s*$")
_RE_THIN = re.compile(r"thinned to one point per (\d+) cm")
_RE_TILE_DONE = re.compile(r"^\s+done in ([\d.]+) min -> ")
_RE_ALL_DONE = re.compile(r"^All (\d+) tiles done")
_RE_SINGLE_DONE = re.compile(r"^SegmentAnyTree done in ([\d.]+) min\. Writing")
_RE_WRITING = re.compile(r"^Writing .* \.\.\.")
_RE_TREES = re.compile(r"^\s+([\d,]+) trees in ")
_RE_FINISHED = re.compile(r"^Done in ([\d.]+) min -> ")
_RE_FAIL = re.compile(r"^SegmentAnyTree: (.*)")
# ---------------------------------------------------------------- merge_sat.py ("Join split crowns")
_RE_MERGE_IN = re.compile(r", field \S+, ([\d,]+) trees\s*$")
_RE_MERGE_OUT = re.compile(r"^\s+merged into ([\d,]+) trees")
_RE_MERGE_LOW = re.compile(r"([\d,]+) trees lower than")
_RE_MERGE_WROTE = re.compile(r"^\s+wrote .* in ([\d.]+) s")
# ---------------------------------------------------------------- tree_info.py ("Measure trees")
_RE_INFO_POINTS = re.compile(r"^\s+([\d,]+) points, tree IDs from '([^']*)', ground: (\S+)")
_RE_INFO_TREES = re.compile(r"^\s+([\d,]+) trees measured")
_RE_INFO_OUT = re.compile(r"^\s+-> .*_info\.laz")
_RE_INFO_NO_CROWNS = re.compile(r"crown outlines skipped: (.*?)\)\s+\(\d")


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def _clean(line) -> str:
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    return str(line).rstrip("\r\n")


@dataclass
class SatProgress:
    """State of the Find trees step. phase: starting, downloading_model, running (one Docker run, no
    tiles), tiles, assembling, writing, finished, failed."""
    phase: str = "starting"
    input_points: Optional[int] = None
    tiles_total: Optional[int] = None
    tile_current: Optional[int] = None
    tiles_finished: set = field(default_factory=set)
    tiles_skipped: set = field(default_factory=set)
    tiles_thinned: set = field(default_factory=set)
    tile_minutes: list = field(default_factory=list)
    trees: Optional[int] = None
    minutes: Optional[float] = None
    fail_message: Optional[str] = None

    @property
    def tiles_done(self) -> int:
        return len(self.tiles_finished)

    @property
    def fraction(self) -> Optional[float]:
        """0..1 for the progress bar, or None when it cannot be known (indeterminate bar)."""
        if self.phase == "finished":
            return 1.0
        if self.tiles_total:
            return min(self.tiles_done / self.tiles_total, 1.0)
        return None

    @property
    def eta_minutes(self) -> Optional[float]:
        """RUN-4: from the average of the tiles run so far (skipped tiles do not count)."""
        if not self.tiles_total or not self.tile_minutes:
            return None
        left = self.tiles_total - self.tiles_done
        return sum(self.tile_minutes) / len(self.tile_minutes) * max(left, 0)

    @property
    def label(self) -> str:
        """Short text for the Running screen (RUN-3)."""
        if self.phase == "downloading_model":
            return "Downloading model (first time only)"
        if self.phase == "tiles" and self.tile_current and self.tiles_total:
            if self.tile_current in self.tiles_thinned and self.tile_current not in self.tiles_finished:
                return f"Retrying tile {self.tile_current} with fewer points"
            return f"Tile {self.tile_current} of {self.tiles_total}"
        if self.phase == "assembling":
            return "Putting the tiles together"
        if self.phase == "writing":
            return "Writing the result"
        if self.phase == "finished":
            return "Done"
        return "Finding trees"


class SatParser:
    step = "find_trees"

    def __init__(self):
        self.state = SatProgress()
        self._fail_lines = None

    def feed(self, line) -> bool:
        """Read one printed line. True if the state changed. Never raises."""
        try:
            return self._feed(_clean(line))
        except Exception:
            return False

    def _feed(self, line: str) -> bool:
        s = self.state
        if self._fail_lines is not None:                 # the rest of a multi-line failure message
            if line.strip():
                self._fail_lines.append(line.strip())
                s.fail_message = "\n".join(self._fail_lines)
            return bool(line.strip())
        m = _RE_FAIL.match(line)
        if m:
            s.phase = "failed"
            self._fail_lines = [m.group(1).strip()]
            s.fail_message = self._fail_lines[0]
            return True
        m = _RE_TILE.match(line)
        if m:
            tile, total = int(m.group(1)), int(m.group(2))
            s.phase, s.tile_current, s.tiles_total = "tiles", tile, total
            if _RE_SKIPPED.search(line):
                s.tiles_finished.add(tile)
                s.tiles_skipped.add(tile)
            elif _RE_THIN.search(line):
                s.tiles_thinned.add(tile)
            return True
        m = _RE_TILE_DONE.match(line)
        if m and s.phase == "tiles" and s.tile_current and s.tile_current not in s.tiles_finished:
            s.tiles_finished.add(s.tile_current)
            s.tile_minutes.append(float(m.group(1)))
            return True
        m = _RE_TILED.match(line)
        if m:
            s.phase, s.tiles_total = "tiles", int(m.group(3))
            return True
        m = _RE_START.match(line)
        if m:
            s.phase = "running"
            s.input_points = _int(m.group(1)) if m.group(1) else None
            return True
        if _RE_DOWNLOAD.match(line):
            s.phase = "downloading_model"
            return True
        if _RE_ALL_DONE.match(line):
            s.phase = "assembling"
            return True
        if _RE_SINGLE_DONE.match(line) or (_RE_WRITING.match(line) and s.phase in ("assembling", "running")):
            s.phase = "writing"
            return True
        m = _RE_TREES.match(line)
        if m:
            s.trees = _int(m.group(1))
            return True
        m = _RE_FINISHED.match(line)
        if m:
            s.phase, s.minutes = "finished", float(m.group(1))
            return True
        return False


class MergeParser:
    step = "join"

    def __init__(self):
        self.info = {"trees_before": None, "trees_after": None, "dropped_low": None, "finished": False}

    def feed(self, line) -> bool:
        try:
            line = _clean(line)
            m = _RE_MERGE_IN.search(line)
            if m and self.info["trees_before"] is None:
                self.info["trees_before"] = _int(m.group(1))
                return True
            m = _RE_MERGE_OUT.match(line)
            if m:
                self.info["trees_after"] = _int(m.group(1))
                low = _RE_MERGE_LOW.search(line)
                if low:
                    self.info["dropped_low"] = _int(low.group(1))
                return True
            if _RE_MERGE_WROTE.match(line):
                self.info["finished"] = True
                return True
        except Exception:
            pass
        return False


class MeasureParser:
    step = "measure"

    def __init__(self):
        self.info = {"points": None, "ground_source": None, "trees": None, "crowns_skipped": None,
                     "finished": False}

    def feed(self, line) -> bool:
        try:
            line = _clean(line)
            m = _RE_INFO_POINTS.match(line)
            if m:
                self.info["points"], self.info["ground_source"] = _int(m.group(1)), m.group(3)
                return True
            m = _RE_INFO_TREES.match(line)
            if m:
                self.info["trees"] = _int(m.group(1))
                return True
            if _RE_INFO_OUT.match(line):
                self.info["finished"] = True
                m = _RE_INFO_NO_CROWNS.search(line)
                if m:
                    self.info["crowns_skipped"] = m.group(1)
                return True
        except Exception:
            pass
        return False


def parser_for(step: str):
    return {"find_trees": SatParser, "join": MergeParser, "measure": MeasureParser}[step]()


# ---------------------------------------------------------------------------------- failures
RETRY, SMALLER_TILES, SEND_LOG, DETAILS = "retry", "retry_smaller_tiles", "send_log", "show_details"


@dataclass(frozen=True)
class Failure:
    kind: str                 # out_of_ram, gpu_memory, crash, docker_gpu, docker_not_running, docker_missing,
    message: str              # image_download, input_missing, no_trees, unknown
    actions: tuple
    detail: str = ""          # the script's own words, for "Show details"

    @property
    def is_empty_result(self) -> bool:
        """RES-10: not an error for the user, the file simply has no trees."""
        return self.kind == "no_trees"


_MESSAGES = {
    "out_of_ram": ("This file is too large to process in one piece on this computer.", (SMALLER_TILES, DETAILS)),
    "gpu_memory": ("The graphics card ran out of memory.", (SMALLER_TILES, DETAILS)),
    "crash": ("The tree-finding step crashed on part of this file.", (RETRY, SEND_LOG, DETAILS)),
    "docker_gpu": ("Docker cannot use the graphics card. Restart Docker Desktop, then try again.",
                   (RETRY, DETAILS)),
    "docker_not_running": ("Docker Desktop is not running. Open Docker Desktop, wait until it says running, "
                           "then try again.", (RETRY,)),
    "docker_missing": ("Docker Desktop is not installed on this computer. Ask Ryan to set it up.",
                       (SEND_LOG,)),
    "image_download": ("Could not download the tree-finding model. Check the internet connection.",
                       (RETRY, DETAILS)),
    "input_missing": ("The point cloud file could not be found. It may have been moved or renamed.",
                      (DETAILS,)),
    "no_trees": ("No trees were found in this file.", ()),
}
# markers in the scripts' own messages, most specific first
_DIRECT = (
    ("no_trees", ("No trees in this file", "No tree ID field", "no tree ID field")),
    ("docker_missing", ("Docker not found",)),
    ("docker_not_running", ("Docker Desktop is not running",)),
    ("docker_gpu", ("Docker cannot use the GPU",)),
    ("image_download", ("could not download the image",)),
    ("input_missing", ("input not found",)),
)
_CAUSES = (
    ("out_of_ram", ("OUT OF RAM", "SIGKILL", "TerminatedWorkerError")),
    ("gpu_memory", ("CUDA out of memory",)),
    ("crash", ("Segmentation fault",)),
)


def _cause(text: str) -> Optional[str]:
    for kind, marks in _CAUSES:
        if any(m in text for m in marks):
            return kind
    return None


def classify_failure(step: str, lines: Iterable) -> Failure:
    """What went wrong in a step that ended with an error, from everything it printed.
    When SegmentAnyTree failed several times (a tile, then the same tile thinned), the last failure
    decides: sat_docker.py prints 'SegmentAnyTree produced no result ...' followed by its reason."""
    lines = [_clean(x) for x in lines]
    text = "\n".join(lines)
    detail = "\n".join(lines[-40:])
    for kind, marks in _DIRECT:
        if any(m in text for m in marks):
            msg, actions = _MESSAGES[kind]
            return Failure(kind, msg, actions, detail)
    kind = None
    for i, line in enumerate(lines):
        if "SegmentAnyTree produced no result" in line:
            reason = next((x for x in lines[i + 1:i + 4] if x.strip()), "")
            kind = _cause(reason) or kind
    kind = kind or _cause(text)
    if kind:
        msg, actions = _MESSAGES[kind]
        return Failure(kind, msg, actions, detail)
    label = {"copy": "Copy file", "find_trees": "Find trees", "join": "Join split crowns",
             "measure": "Measure trees", "prepare_view": "Prepare view"}.get(step, step)
    return Failure("unknown", f"Something went wrong in step {label}.", (RETRY, DETAILS, SEND_LOG), detail)
