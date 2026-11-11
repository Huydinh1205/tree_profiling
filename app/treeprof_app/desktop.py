"""The parts that talk to Windows itself: opening a folder or CloudCompare, the zip of logs for
support, and what setup does once (icon, shortcuts, the .treeprof file type) (SYS-6, RES-9, OPEN-1,
spec 10). The window and its dialogs are in treeprof_app/gui.
"""
from __future__ import annotations

import datetime as dt
import os
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path
from typing import Optional

from .winproc import hidden

APP_DIR = Path(__file__).resolve().parents[1]          # the folder that holds treeprof_app, launch.pyw, .venv
APP_NAME = "Tree Profiling"
FILE_TYPE = "TreeProfiling.Results"


def open_folder(folder) -> bool:
    """Show a folder in File Explorer."""
    try:
        if os.name == "nt":
            os.startfile(str(folder))                          # noqa: only exists on Windows
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return True
    except OSError:
        return False


def reveal_file(path) -> bool:
    """Open File Explorer with this file selected."""
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
            return True
        return open_folder(Path(path).parent)
    except OSError:
        return False


def open_with(program: str, path) -> bool:
    try:
        subprocess.Popen([program, str(path)])
        return True
    except OSError:
        return False


def desktop_folder() -> Path:
    """The Desktop of this Windows account (it can be inside OneDrive), else the home folder."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and os.path.isdir(buf.value):
                return Path(buf.value)                             # 0x10 = CSIDL_DESKTOPDIRECTORY
        except (OSError, AttributeError):
            pass
    for candidate in (Path(os.environ.get("USERPROFILE", "")) / "Desktop", Path.home() / "Desktop"):
        if str(candidate) != "Desktop" and candidate.is_dir():
            return candidate
    return Path.home()


def make_log_zip(home, *, run: Optional[dict] = None, machine: Optional[list] = None, out_dir=None) -> Path:
    """SYS-6: one zip with the logs of a run (or of the app), its .treeprof file and the computer
    checks. Never any point cloud or result table."""
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(out_dir or desktop_folder()) / f"TreeProfiling_log_{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        lines = [f"made: {dt.datetime.now().isoformat(timespec='seconds')}", f"home: {home}"]
        for c in machine or []:
            lines.append(f"check {c.key}: {c.level}: {c.text}")
        if run:
            for key in ("id", "site", "run_by", "status", "input_path", "input_points", "max_points", "workdir",
                        "started_at", "finished_at", "error_kind", "error_message", "error_detail"):
                lines.append(f"run {key}: {run.get(key)}")
            workdir = Path(run["workdir"])
            for p in sorted((workdir / "log").glob("*")) if (workdir / "log").is_dir() else []:
                if p.is_file() and p.stat().st_size < 50 * 1024 * 1024:
                    z.write(p, f"log/{p.name}")
            for p in workdir.glob("*.treeprof"):
                z.write(p, p.name)
        settings = Path(home) / "state" / "settings.json"
        if settings.is_file():
            z.write(settings, "settings.json")
        z.writestr("summary.txt", "\n".join(lines) + "\n")
    return out


# ---------------------------------------------------------------------------------- set up once
def launcher_command(app_dir=APP_DIR) -> tuple:
    """(pythonw.exe of the app's own environment, launch.pyw): how Windows starts the app."""
    app_dir = Path(app_dir)
    return app_dir / ".venv" / "Scripts" / "pythonw.exe", app_dir / "launch.pyw"


def icon_png(size: int = 64) -> bytes:
    """The app's icon as a PNG (a dark green disc with a white tree), made here so the repository holds
    no binary file."""
    c = size / 2

    def pixel(x: int, y: int) -> bytes:
        dx, dy = x + 0.5 - c, y + 0.5 - c
        if dx * dx + dy * dy > (c - 1) ** 2:
            return b"\x00\x00\x00\x00"
        crown = dx * dx + (dy + size * 0.10) ** 2 <= (size * 0.27) ** 2
        stem = abs(dx) <= size * 0.045 and size * 0.10 <= dy <= size * 0.34
        return b"\xff\xff\xff\xff" if crown or stem else b"\x1f\x6b\x4a\xff"

    raw = b"".join(b"\x00" + b"".join(pixel(x, y) for x in range(size)) for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def icon_bytes(size: int = 64) -> bytes:
    """The same icon as a .ico file (for the shortcuts and the .treeprof file type)."""
    png = icon_png(size)
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), 22) + png


def register_file_type(app_dir=APP_DIR) -> str:
    """OPEN-1: double-clicking a .treeprof file opens it in the app. For this Windows account only
    (no administrator rights needed). Returns the command that was registered."""
    import winreg
    pythonw, launcher = launcher_command(app_dir)
    command = f'"{pythonw}" "{launcher}" "%1"'
    base = r"Software\Classes"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\.treeprof") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, FILE_TYPE)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + FILE_TYPE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Tree Profiling results")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + FILE_TYPE + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(Path(app_dir) / "treeprof.ico"))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + FILE_TYPE + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)      # tell Explorer file types changed
    except (OSError, AttributeError):
        pass
    return command


def shortcut_script(app_dir=APP_DIR) -> str:
    """The PowerShell lines that put a 'Tree Profiling' shortcut on the Desktop and in the Start menu."""
    def q(value) -> str:
        return "'" + str(value).replace("'", "''") + "'"
    pythonw, launcher = launcher_command(app_dir)
    return "\n".join([
        "$shell = New-Object -ComObject WScript.Shell",
        "foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {",
        f"  $link = $shell.CreateShortcut((Join-Path $folder {q(APP_NAME + '.lnk')}))",
        f"  $link.TargetPath = {q(pythonw)}",
        f"  $link.Arguments = {q(chr(34) + str(launcher) + chr(34))}",
        f"  $link.WorkingDirectory = {q(app_dir)}",
        f"  $link.IconLocation = {q(Path(app_dir) / 'treeprof.ico')}",
        f"  $link.Description = {q(APP_NAME)}",
        "  $link.Save()",
        "  Write-Output $link.FullName",
        "}",
    ])


def make_shortcuts(app_dir=APP_DIR) -> list:
    """Returns the shortcuts that were made."""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", shortcut_script(app_dir)],
                       capture_output=True, text=True, timeout=120, **hidden())
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout).strip() or f"PowerShell ended with code {r.returncode}")
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def install(app_dir=APP_DIR, out=print, windows: Optional[bool] = None) -> bool:
    """What setup_windows.bat does after the libraries are in: icon, shortcuts, file type. Each part
    reports for itself; a part that fails does not stop the others. Safe to run again."""
    app_dir = Path(app_dir)
    ok = True
    if "onedrive" in str(app_dir).lower():
        out(f"WARNING: the app is inside OneDrive ({app_dir}). Move the folder to a local disk, for example C:\\TreeProfiling.")
    if not (os.name == "nt" if windows is None else windows):
        out("Shortcuts and the .treeprof file type are only set up on Windows.")
        return True
    steps_ = (("icon", lambda: (app_dir / "treeprof.ico").write_bytes(icon_bytes()) and str(app_dir / "treeprof.ico")),
              ("shortcuts on the Desktop and in the Start menu", lambda: ", ".join(make_shortcuts(app_dir))),
              (".treeprof files open in Tree Profiling", lambda: register_file_type(app_dir)))
    for name, action in steps_:
        try:
            out(f"OK    {name}: {action()}")
        except Exception as e:
            ok = False
            out(f"FAILED {name}: {type(e).__name__}: {e}")
    return ok

