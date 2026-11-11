"""Where the app keeps its things, and the few settings a user can change (SET-1 to SET-4).

Everything lives under one home folder (default C:\\TreeProfiling on Windows):
    state\\settings.json, state\\runs.sqlite      the app's own records
    runs\\<site>_<date>_<time>\\                    one working folder per run
The home folder can be moved with the TREEPROF_HOME environment variable (used by the tests).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_WARN_GB = 100
CLOUDCOMPARE_GUESSES = (r"C:\Program Files\CloudCompare\CloudCompare.exe",
                        r"C:\Program Files (x86)\CloudCompare\CloudCompare.exe")


def default_home() -> Path:
    env = os.environ.get("TREEPROF_HOME")
    if env:
        return Path(env)
    return Path(r"C:\TreeProfiling") if os.name == "nt" else Path.home() / "TreeProfiling"


def default_scripts_dir() -> Path:
    """The processes folder next to the app folder (same repository)."""
    return Path(__file__).resolve().parents[2] / "processes"


def default_script_python() -> list:
    """The interpreter that runs the scripts. On the processing computer that is the Python 3.10 the
    .bat files already use (it has the scripts' libraries); the app itself may run on another one."""
    return ["py", "-3.10"] if os.name == "nt" else [sys.executable]


@dataclass
class Settings:
    runs_root: str = ""                      # SET-1
    shared_root: str = ""                    # SET-2: folder opened first when browsing
    warn_free_gb: int = DEFAULT_WARN_GB      # SET-3
    cloudcompare: str = ""                   # SET-4
    scripts_dir: str = ""
    script_python: list = field(default_factory=list)
    last_browse_dir: str = ""
    last_save_dir: str = ""
    recent: list = field(default_factory=list)   # HOME-5: saved results opened lately, newest first
    path: Optional[str] = None               # where this file is saved (not written into the file)

    @classmethod
    def load(cls, home) -> "Settings":
        home = Path(home)
        path = home / "state" / "settings.json"
        data = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "path"}
        s = cls(**known)
        s.path = str(path)
        s.runs_root = s.runs_root or str(home / "runs")
        s.scripts_dir = s.scripts_dir or str(default_scripts_dir())
        s.script_python = s.script_python or default_script_python()
        if not s.cloudcompare:
            s.cloudcompare = next((p for p in CLOUDCOMPARE_GUESSES if os.path.isfile(p)), "")
        return s

    def save(self) -> None:
        data = asdict(self)
        data.pop("path")
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(part, path)


def runs_root_problem(folder) -> Optional[str]:
    """SET-1: results must stay on a local disk outside OneDrive. Returns the reason, or None if fine."""
    s = str(folder)
    if "onedrive" in s.lower():
        return "This folder is inside OneDrive. Choose a folder on a local disk."
    if s.startswith("\\\\") or s.startswith("//"):
        return "This folder is on a network drive. Choose a folder on a local disk."
    return None
