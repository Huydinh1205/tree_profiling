"""Save: copy the results of a run from its working folder to a folder the user chose (SAVE-1 to
SAVE-9). Files get the site's name; the .treeprof project file is written last, so a folder that has
one is complete. The working folder is only read, never changed.
"""
from __future__ import annotations

import copy
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import filecopy, projectfile, steps
from .naming import safe_name

FREE_SPACE_MARGIN = 50 * 1024 * 1024


class SaveError(Exception):
    """code: missing_source | conflict | not_writable | no_space | copy_failed"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SaveCancelled(Exception):
    pass


@dataclass(frozen=True)
class SaveItem:
    key: str                         # key in the .treeprof "files" section
    work_name: str                   # file name in the working folder
    saved_suffix: str                # saved as <site><saved_suffix>
    label: str                       # shown next to the tick box
    always: bool = False             # cannot be unticked: needed to reopen the results
    default: bool = False            # ticked when the dialog opens
    parts_required: tuple = ()       # other extensions that belong to the file
    parts_optional: tuple = ()


ITEMS = (
    SaveItem("trees_csv", steps.TREES_CSV, "_trees.csv", "Tree table (Excel)", always=True, default=True),
    SaveItem("crowns_shp", steps.CROWNS_SHP, "_crowns.shp", "Crown outlines (QGIS, CloudCompare)", always=True,
             default=True, parts_required=(".shx", ".dbf"), parts_optional=(".prj", ".cpg")),
    SaveItem("info_laz", steps.INFO_LAZ, "_info.laz", "Point cloud with tree numbers (CloudCompare, 3D view)",
             default=True),
    SaveItem("sat_merged_laz", steps.SAT_MERGED, "_sat_merged.laz", "Segmentation after joining crowns"),
    SaveItem("sat_raw_laz", steps.SAT_RAW, "_sat_raw.laz", "Segmentation before joining crowns"),
)
NO_3D_WARNING = "3D view will not be available when you reopen"      # SAVE-3, shown when info_laz is unticked


def default_selection() -> set:
    return {i.key for i in ITEMS if i.default}


@dataclass
class SavePlan:
    folder: Path
    stem: str
    copies: list = field(default_factory=list)          # (source Path, destination Path)
    files: dict = field(default_factory=dict)           # key -> saved file name, or None
    project_path: Optional[Path] = None
    total_bytes: int = 0
    conflicts: list = field(default_factory=list)       # destinations that already exist


def make_plan(workdir, folder, site: str, selected: Optional[Iterable[str]] = None, stem_suffix: str = "") -> SavePlan:
    """What a Save would copy. selected: keys of the ticked items (always-items are added)."""
    workdir, folder = Path(workdir), Path(folder)
    chosen = default_selection() if selected is None else set(selected)
    chosen |= {i.key for i in ITEMS if i.always}
    stem = safe_name(site) + stem_suffix
    plan = SavePlan(folder, stem, files={i.key: None for i in ITEMS})
    for item in ITEMS:
        if item.key not in chosen:
            continue
        src = workdir / item.work_name
        dst = folder / (stem + item.saved_suffix)
        pairs = [(src, dst)]
        pairs += [(src.with_suffix(ext), dst.with_suffix(ext)) for ext in item.parts_required]
        pairs += [(src.with_suffix(ext), dst.with_suffix(ext)) for ext in item.parts_optional
                  if src.with_suffix(ext).is_file()]
        for s, _ in pairs:
            if not s.is_file():
                raise SaveError("missing_source", f"{s.name} is missing from the run's folder, so it cannot be saved.")
        plan.copies += pairs
        plan.files[item.key] = dst.name
    plan.project_path = folder / (stem + projectfile.EXTENSION)
    plan.total_bytes = sum(s.stat().st_size for s, _ in plan.copies)
    plan.conflicts = [d for _, d in plan.copies if d.exists()]
    if plan.project_path.exists():
        plan.conflicts.append(plan.project_path)
    return plan


def plan_keeping_both(workdir, folder, site: str, selected: Optional[Iterable[str]] = None) -> SavePlan:
    """SAVE-5 'Keep both': the same files under '<site> (2)', '<site> (3)' ... so nothing is replaced."""
    n = 2
    while True:
        plan = make_plan(workdir, folder, site, selected, stem_suffix=f" ({n})")
        if not plan.conflicts:
            return plan
        n += 1


def _existing_parent(path: Path) -> Path:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def check_destination(plan: SavePlan, free_bytes: Optional[int] = None) -> None:
    """SAVE-5: the folder can be written to and has room. Leaves nothing behind."""
    base = _existing_parent(plan.folder)
    if not base.is_dir() or not os.access(base, os.W_OK):
        raise SaveError("not_writable", f"Cannot save to {plan.folder}: this folder cannot be written to.")
    probe = base / ".treeprof_write_test"
    try:
        open(probe, "wb").close()
        probe.unlink()
    except OSError:
        raise SaveError("not_writable", f"Cannot save to {plan.folder}: this folder cannot be written to.") from None
    if free_bytes is None:
        free_bytes = shutil.disk_usage(base).free
    need = plan.total_bytes + FREE_SPACE_MARGIN
    if free_bytes < need:
        raise SaveError("no_space", f"Not enough space in {base}: {need / 1e9:.1f} GB needed, "
                                    f"{free_bytes / 1e9:.1f} GB free.")


def execute(plan: SavePlan, project: dict, *, replace: bool = False,
            progress: Optional[Callable[[int, int, str], None]] = None,
            cancel: Optional[Callable[[], bool]] = None, copy_file: Callable = filecopy.copy_with_hash) -> Path:
    """Copy the files, then write the .treeprof file. Returns the .treeprof path.
    If anything fails or the user cancels, every file this call created is removed again."""
    if plan.conflicts and not replace:
        raise SaveError("conflict", f"{plan.conflicts[0].name} already exists in {plan.folder}.")
    check_destination(plan)
    made_folder = not plan.folder.exists()
    created, done_before = [], 0
    try:
        plan.folder.mkdir(parents=True, exist_ok=True)
        for src, dst in plan.copies:
            existed = dst.exists()
            size = src.stat().st_size
            if not existed:
                created.append(dst)

            def report(done, _total, base=done_before, name=dst.name):
                if progress:
                    progress(base + done, plan.total_bytes, name)

            result = copy_file(src, dst, progress=report, cancel=cancel)
            if result.bytes != size:
                raise SaveError("copy_failed", f"{dst.name} was not copied completely.")
            done_before += size
        data = copy.deepcopy(project)
        data["files"] = dict(plan.files)
        if not plan.project_path.exists():
            created.append(plan.project_path)
        projectfile.write_project(plan.project_path, data)
        return plan.project_path
    except BaseException as e:
        for p in created:
            for q in (p, p.with_name(p.name + ".part")):
                try:
                    q.unlink()
                except OSError:
                    pass
        if made_folder:
            try:
                plan.folder.rmdir()                     # only if empty
            except OSError:
                pass
        if isinstance(e, filecopy.CopyCancelled):
            raise SaveCancelled() from None
        if isinstance(e, (filecopy.CopyError, OSError)):
            raise SaveError("copy_failed", str(e)) from None
        raise
