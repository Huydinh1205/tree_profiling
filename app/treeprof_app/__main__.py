"""Start the app:   python -m treeprof_app [results.treeprof]

Starting it a second time does not start a second copy: the window that is already open comes to
the front and opens the .treeprof file given on the command line (SYS-1, OPEN-1).

Other uses (setup_windows.bat and update_windows.bat call these):
    python -m treeprof_app --install         shortcuts and the .treeprof file type, then the computer checks
    python -m treeprof_app --check-closed    exit code 1 when the app is open (an update must wait)
    python -m treeprof_app --backup-state    copy the app's records to state_backups before an update
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
from pathlib import Path

from . import __version__, desktop
from .settings import default_home

LOG_MAX_BYTES = 10 * 1024 * 1024          # NFR-6: app.log and one older copy, 20 MB at most
BACKUPS_KEPT = 5


def log_to_file(home: Path) -> Path:
    """Started with pythonw.exe there is no console, so Python has no place to print to. Send what is
    printed (and any error) to state/app.log instead."""
    folder = Path(home) / "state"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "app.log"
    try:
        if path.is_file() and path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name("app.log.1"))
    except OSError:
        pass
    stream = open(path, "a", encoding="utf-8", buffering=1)
    stream.write(f"\n===== {dt.datetime.now().isoformat(timespec='seconds')}  Tree Profiling {__version__} started\n")
    sys.stdout = sys.stderr = stream
    return path


def backup_state(home: Path) -> Path:
    """Copy the app's records (not the runs, not the 3D caches) next to them. Keeps the last few."""
    home = Path(home)
    target = home / "state_backups" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    state = home / "state"
    for p in sorted(state.glob("*")) if state.is_dir() else []:
        if p.is_file() and not p.name.startswith("app.log"):
            shutil.copy2(p, target / p.name)
    old = sorted(d for d in target.parent.iterdir() if d.is_dir())[:-BACKUPS_KEPT]
    for d in old:
        shutil.rmtree(d, ignore_errors=True)
    return target


def app_is_open() -> bool:
    from PySide6.QtCore import QCoreApplication
    from .gui.main import send_to_running
    QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    return send_to_running({"ping": True})


def install(home: Path) -> int:
    from . import checks
    from .settings import Settings
    ok = desktop.install()
    s = Settings.load(home)
    print("\nThis computer:")
    for c in checks.machine_checks(runs_root=s.runs_root, shared_root=s.shared_root, warn_free_gb=s.warn_free_gb):
        print(f"  {c.level.upper():8}{c.text}" + (f"  ({c.help})" if c.help and c.level != checks.OK else ""))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="treeprof_app", description="Tree Profiling")
    ap.add_argument("file", nargs="?", help="a .treeprof file to open, or a .las / .laz file to start a run with")
    ap.add_argument("--home", help="folder for the app's records and runs (default C:\\TreeProfiling)")
    ap.add_argument("--install", action="store_true", help="make the shortcuts and register .treeprof files")
    ap.add_argument("--check-closed", action="store_true", help="exit code 1 when the app is open")
    ap.add_argument("--backup-state", action="store_true", help="copy the app's records to state_backups")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    home = Path(a.home) if a.home else default_home()

    if a.install:
        return install(home)
    if a.check_closed:
        if app_is_open():
            print("Tree Profiling is open. Close it first (if a run is going, choose 'Cancel run and quit', "
                  "or wait until it is finished).")
            return 1
        return 0
    if a.backup_state:
        print(f"Records copied to {backup_state(home)}")
        return 0

    if sys.stdout is None or sys.stderr is None:               # pythonw.exe
        log_to_file(home)
    from .gui.main import run
    return run(home, os.path.abspath(a.file) if a.file else None)


if __name__ == "__main__":
    sys.exit(main())
