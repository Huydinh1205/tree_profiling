"""The .treeprof project file: a small JSON file next to the saved results. Double-clicking it reopens
the results in the app (OPEN-1 to OPEN-7). File paths inside are relative to the .treeprof file, so the
whole folder can be copied, moved or renamed.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path
from typing import Optional

FORMAT = "treeprof"
FORMAT_VERSION = 1
EXTENSION = ".treeprof"
FILE_KEYS = ("trees_csv", "crowns_shp", "info_laz", "sat_merged_laz", "sat_raw_laz")
REQUIRED_FILES = ("trees_csv", "crowns_shp")          # without these the results cannot be shown
SHAPEFILE_PARTS = (".shx", ".dbf")                    # a .shp is unusable without them


class ProjectFileError(Exception):
    """code: not_found | not_json | not_treeprof | newer_version | missing_field | missing_file | ambiguous"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def new_project(*, site_name: str, run_by: str, files: dict, app_version: str, capture_date: Optional[str] = None,
                run_id: Optional[str] = None, started_at: Optional[str] = None, finished_at: Optional[str] = None,
                machine: Optional[str] = None, input: Optional[dict] = None, pipeline: Optional[dict] = None,
                summary: Optional[dict] = None) -> dict:
    """A project dictionary in the layout of spec 7.1. capture_date is an ISO date (YYYY-MM-DD) or None."""
    data = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": app_version,
        "run_id": run_id or str(uuid.uuid4()),
        "site_name": site_name,
        "capture_date": capture_date,
        "run_by": run_by,
        "started_at": started_at,
        "finished_at": finished_at,
        "machine": machine,
        "input": input or {},
        "pipeline": pipeline or {},
        "files": {k: files.get(k) for k in FILE_KEYS},
        "summary": summary or {},
    }
    validate(data)
    return data


def validate(data) -> None:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ProjectFileError("not_treeprof", "This is not a Tree Profiling project file.")
    version = data.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProjectFileError("missing_field", "The project file has no valid format_version.")
    if version > FORMAT_VERSION:
        raise ProjectFileError("newer_version", "This file was made by a newer version of Tree Profiling.")
    if not isinstance(data.get("site_name"), str) or not data["site_name"].strip():
        raise ProjectFileError("missing_field", "The project file has no site_name.")
    files = data.get("files")
    if not isinstance(files, dict):
        raise ProjectFileError("missing_field", "The project file has no list of result files.")
    for key in REQUIRED_FILES:
        if not isinstance(files.get(key), str) or not files[key].strip():
            raise ProjectFileError("missing_field", f"The project file does not name its {key} file.")


def write_project(path, data: dict) -> Path:
    """Write through a temporary name, so a reader never sees half a file."""
    validate(data)
    path = Path(path)
    part = path.with_name(path.name + ".part")
    with open(part, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)
    return path


def read_project(path) -> dict:
    """The project dictionary. Unknown fields are kept and ignored. Raises ProjectFileError."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ProjectFileError("not_found", f"File not found: {path}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ProjectFileError("not_json", f"The project file cannot be read: {e}") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise ProjectFileError("not_json", "The project file is damaged (it is not valid JSON).") from None
    validate(data)
    return _migrate(data)


def _migrate(data: dict) -> dict:
    """Bring an older format_version up to the current layout. Version 1 is the first one."""
    data = copy.deepcopy(data)
    data.setdefault("files", {})
    for key in FILE_KEYS:
        data["files"].setdefault(key, None)
    return data


def result_paths(project_path, data: dict) -> dict:
    """key -> absolute Path of each result file named in the project (None if it was not saved)."""
    folder = Path(project_path).parent
    out = {}
    for key in FILE_KEYS:
        name = data.get("files", {}).get(key)
        out[key] = folder / str(name).replace("\\", "/") if name else None
    return out


def missing_files(project_path, data: dict) -> list:
    """(key, Path) of every file the project names that is not on disk (shapefile parts included)."""
    missing = []
    for key, p in result_paths(project_path, data).items():
        if p is None:
            continue
        if not p.is_file():
            missing.append((key, p))
        elif p.suffix.lower() == ".shp":
            missing += [(key, p.with_suffix(ext)) for ext in SHAPEFILE_PARTS if not p.with_suffix(ext).is_file()]
    return missing


def check_openable(project_path, data: dict) -> list:
    """OPEN-5. Raises if a file needed to show the results is missing; returns the optional files that
    are missing (the caller switches off what depends on them, e.g. the 3D view)."""
    missing = missing_files(project_path, data)
    required = [p for key, p in missing if key in REQUIRED_FILES]
    if required:
        names = ", ".join(p.name for p in required)
        raise ProjectFileError("missing_file", f"Cannot open these results: {names} is missing from "
                                               f"{Path(project_path).parent}.")
    return [(key, p) for key, p in missing if key not in REQUIRED_FILES]


def find_in_folder(folder) -> Path:
    """OPEN-1: the single .treeprof file of a saved folder."""
    folder = Path(folder)
    found = sorted(p for p in folder.glob("*" + EXTENSION) if p.is_file()) if folder.is_dir() else []
    if not found:
        raise ProjectFileError("not_found", f"No {EXTENSION} file in {folder}.")
    if len(found) > 1:
        raise ProjectFileError("ambiguous", f"{folder} has {len(found)} {EXTENSION} files. Open one of them.")
    return found[0]
