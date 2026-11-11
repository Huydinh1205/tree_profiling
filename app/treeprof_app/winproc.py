"""Starting other programs from an app that has no console window of its own.

On Windows the app runs with pythonw.exe (no black console window). A console program started from
it (py, docker, nvidia-smi, git, taskkill) would then pop up its own console window for a moment,
unless it is started with CREATE_NO_WINDOW. Programs it starts in turn (docker run from
sat_docker.py) share that hidden console.
"""
from __future__ import annotations

import os
import subprocess


def hidden(extra_flags: int = 0) -> dict:
    """Keyword arguments for subprocess.run / Popen: no console window on Windows, nothing elsewhere."""
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW | extra_flags}
