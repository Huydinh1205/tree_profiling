"""The checks shown before a run: is this computer ready (HOME-3), and can this file be run
(NEW-2, NEW-3). Each check gives a level (ok, warning, error) and a sentence a person can act on.
An error blocks Start; a warning does not.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import lasheader
from .winproc import hidden

OK, WARNING, ERROR = "ok", "warning", "error"
GB = 1024 ** 3
SAT_IMAGE = "ghcr.io/3dtrees-earth/3dtrees_sat:1.1.0"


@dataclass(frozen=True)
class Check:
    key: str
    level: str
    text: str
    help: str = ""


def _run(cmd, timeout=20) -> int:
    """Exit code of a command, or -1 if it cannot be run."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, **hidden()).returncode
    except (OSError, subprocess.SubprocessError):
        return -1


def free_bytes(folder) -> Optional[int]:
    p = Path(folder)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def machine_checks(*, runs_root, shared_root: str = "", warn_free_gb: int = 100, image: str = SAT_IMAGE,
                   run: Callable = _run) -> list:
    """HOME-3: Docker, graphics card, tree-finding model, local disk, shared drive."""
    out = []
    docker = run(["docker", "info"])
    if docker == 0:
        out.append(Check("docker", OK, "Docker running"))
    else:
        out.append(Check("docker", ERROR, "Docker is not running",
                         "Open Docker Desktop, wait until it says running, then press Check again."))
    gpu = run(["nvidia-smi"])
    if gpu == 0:
        out.append(Check("gpu", OK, "Graphics card ready"))
    else:
        out.append(Check("gpu", ERROR, "Graphics card not found",
                         "The NVIDIA driver did not answer. Restart the computer; if it stays, send the log to Ryan."))
    if docker == 0:
        if run(["docker", "image", "inspect", image]) == 0:
            out.append(Check("model", OK, "Tree-finding model downloaded"))
        else:
            out.append(Check("model", WARNING, "Tree-finding model not downloaded yet",
                             "It is downloaded at the start of the first run (several GB, needs internet)."))
    free = free_bytes(runs_root)
    if free is None:
        out.append(Check("disk", ERROR, "Local disk cannot be read", f"Check the folder {runs_root} in Settings."))
    elif free < warn_free_gb * GB:
        out.append(Check("disk", WARNING, f"Local disk: {free / GB:.0f} GB free",
                         "Getting full. Delete local copies you no longer need in Settings, Storage."))
    else:
        out.append(Check("disk", OK, f"Local disk: {free / GB:.0f} GB free"))
    if shared_root:
        if os.path.isdir(shared_root):
            out.append(Check("shared", OK, "Shared drive connected"))
        else:
            out.append(Check("shared", WARNING, "Shared drive not reachable",
                             f"{shared_root} cannot be opened. Check the network connection."))
    return out


def space_needed(size_bytes: int, name: str) -> int:
    """Disk space one run needs. First rule of thumb (spec A2), to be corrected after real runs:
    6 x a .laz file, or 2 x a .las file, plus 10 GB."""
    factor = 2 if str(name).lower().endswith(".las") else 6
    return size_bytes * factor + 10 * GB


def is_cloud_only(path) -> bool:
    """A OneDrive file that is listed but not downloaded to this computer (Windows)."""
    try:
        attrs = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & 0x400000) or bool(attrs & 0x1000)       # RECALL_ON_DATA_ACCESS, OFFLINE


def read_crs(path):
    """(name, is_geographic) of the file's coordinate system; (None, None) if it has none;
    ("?", None) if it cannot be checked on this computer (laspy / pyproj not installed)."""
    try:
        import laspy
    except ImportError:
        return "?", None
    try:
        with laspy.open(str(path)) as f:
            crs = f.header.parse_crs()
    except Exception:
        return "?", None
    if crs is None:
        return None, None
    return crs.name, bool(crs.is_geographic)


@dataclass
class FileReport:
    path: str
    name: str
    size_bytes: int = 0
    header: Optional[lasheader.LasHeader] = None
    crs_name: Optional[str] = None
    needs_bytes: int = 0
    checks: list = field(default_factory=list)

    @property
    def can_start(self) -> bool:
        return not any(c.level == ERROR for c in self.checks)


def file_checks(path, *, runs_root, warn_free_gb: int = 100, machine: Optional[list] = None,
                already_finished: bool = False, crs_reader: Callable = read_crs,
                free_reader: Callable = free_bytes) -> FileReport:
    """NEW-2 and NEW-3 for one input file."""
    path = Path(path)
    rep = FileReport(str(path), path.name)
    if is_cloud_only(path):
        rep.checks.append(Check("onedrive", ERROR, "This file is not downloaded to this computer",
                                "In File Explorer, right-click the file and choose 'Always keep on this device', "
                                "wait for it to download, then choose it again."))
        return rep
    try:
        rep.size_bytes = path.stat().st_size
        rep.header = lasheader.read_header(path)
    except (OSError, lasheader.LasHeaderError):
        rep.checks.append(Check("read", ERROR, "This file cannot be read",
                                "It is not a LAS or LAZ point cloud, or it is damaged. Export it again."))
        return rep
    if rep.header.points == 0:
        rep.checks.append(Check("read", ERROR, "This file has no points", "Export it again from DJI Terra."))
        return rep
    rep.checks.append(Check("read", OK, "The file can be read"))

    name, geographic = crs_reader(path)
    rep.crs_name = name
    if name is None:
        rep.checks.append(Check("crs", WARNING, "The file has no coordinate system",
                                "Results will have no coordinate system. They can still be made."))
    elif geographic:
        rep.checks.append(Check("crs", ERROR, "Coordinates are in degrees, not metres",
                                "Export the file again from DJI Terra in the project's map grid, then choose "
                                "the new file."))
    elif name == "?":
        rep.checks.append(Check("crs", WARNING, "The coordinate system could not be checked",
                                "Make sure the file is in a map grid in metres."))
    else:
        rep.checks.append(Check("crs", OK, "Coordinates are in metres"))

    if "onedrive" in str(path).lower():
        rep.checks.append(Check("onedrive", WARNING, "The file is in a OneDrive folder",
                                "It will be copied to this computer first."))

    rep.needs_bytes = space_needed(rep.size_bytes, path.name)
    free = free_reader(runs_root)
    if free is None or free < rep.needs_bytes:
        rep.checks.append(Check("disk", ERROR, "Not enough space on this computer",
                                f"About {rep.needs_bytes / GB:.0f} GB needed, "
                                f"{(free or 0) / GB:.0f} GB free. Delete local copies in Settings, Storage."))
    elif free - rep.needs_bytes < warn_free_gb * GB:
        rep.checks.append(Check("disk", WARNING, f"Enough space, but the disk will be nearly full afterwards",
                                f"About {rep.needs_bytes / GB:.0f} GB needed, {free / GB:.0f} GB free."))
    else:
        rep.checks.append(Check("disk", OK, f"Enough space on this computer: about {rep.needs_bytes / GB:.0f} GB "
                                            f"needed, {free / GB:.0f} GB free"))
    for c in machine or []:
        if c.key in ("docker", "gpu") and c.level == ERROR:
            rep.checks.append(c)
    if machine is not None and not any(c.key in ("docker", "gpu") and c.level == ERROR for c in machine):
        rep.checks.append(Check("machine", OK, "Docker and the graphics card are ready"))
    if already_finished:
        rep.checks.append(Check("duplicate", WARNING, "This file already has finished results",
                                "You can open the existing results from the list of runs."))
    return rep
