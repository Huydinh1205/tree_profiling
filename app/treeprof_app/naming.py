"""Names: site and capture date from a folder name, the suggested save folder, safe Windows names.

Folder convention used for the data (processing manual, naming chapter):
    <Site location> - <dd mmm yy> - <Format of data>      e.g.  Test Site - 13 Feb 25 - LiDAR
The separator is usually an en dash; a hyphen and missing spaces are accepted too.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterable, Optional

EN_DASH = "–"
_DASHES = "–—\\-"                      # en dash, em dash, hyphen
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTHS_FULL = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
                "october", "november", "december"]
_FOLDER = re.compile(
    rf"^(?P<site>.+?)\s*[{_DASHES}]\s*(?P<d>\d{{1,2}})[\s/.]*(?P<m>[A-Za-z]{{3,9}})[\s/.]*(?P<y>\d{{4}}|\d{{2}})"
    rf"\s*(?:[{_DASHES}]\s*(?P<kind>.*))?$")
SAVE_LABEL = "Tree Profiling"                    # last part of the suggested save folder name
MAX_PATH_WARN = 240                              # SAVE-4


@dataclass(frozen=True)
class SiteInfo:
    site: str
    capture_date: Optional[dt.date]
    source: str                                  # "folder" (naming convention recognised) or "file"


def _pure(path):
    s = str(path)
    return PureWindowsPath(s) if ("\\" in s or re.match(r"^[A-Za-z]:", s)) else PurePosixPath(s)


def _month(token: str) -> Optional[int]:
    t = token.lower()
    for i, full in enumerate(_MONTHS_FULL, 1):
        if t == full[:3] or (len(t) > 3 and full.startswith(t)):
            return i
    return None


def parse_folder_name(name: str):
    """(site, capture date) from one folder name, or None if it does not follow the convention.
    A folder that follows it but has an impossible date (31 Feb) gives (site, None)."""
    m = _FOLDER.match(name.strip())
    if not m:
        return None
    month = _month(m.group("m"))
    site = m.group("site").strip()
    if month is None or not site:
        return None
    year = int(m.group("y"))
    if year < 100:
        year += 2000
    try:
        date = dt.date(year, month, int(m.group("d")))
    except ValueError:
        date = None
    return site, date


def site_from_path(path, max_levels: int = 6) -> SiteInfo:
    """NEW-4: site name and capture date for an input file. Looks at the parent folders, nearest first;
    falls back to the file name without a date."""
    p = _pure(path)
    for parent in list(p.parents)[:max_levels]:
        got = parse_folder_name(parent.name) if parent.name else None
        if got:
            return SiteInfo(got[0], got[1], "folder")
    return SiteInfo(p.stem or "Site", None, "file")


def project_folder(path) -> Optional[str]:
    """SAVE-1: the 'Project' folder of the job this input file belongs to (<job>/RAW and <job>/Project),
    or None if the path has no RAW or Project folder. The caller checks that it exists."""
    p = _pure(path)
    for parent in p.parents:
        low = parent.name.lower()
        if low == "project":
            return str(parent)
        if low == "raw":
            return str(parent.parent / "Project")
    return None


def format_capture_date(date: dt.date) -> str:
    """13 Feb 25 (English month whatever the Windows language is)."""
    return f"{date.day:02d} {_MONTHS[date.month - 1]} {date.year % 100:02d}"


def suggested_folder_name(site: str, capture_date: Optional[dt.date]) -> str:
    """SAVE-2: <Site> - <dd mmm yy> - Tree Profiling (en dashes), or without the date."""
    parts = [safe_name(site)]
    if capture_date:
        parts.append(format_capture_date(capture_date))
    parts.append(SAVE_LABEL)
    return f" {EN_DASH} ".join(parts)


def next_free_name(name: str, existing: Iterable[str]) -> str:
    """SAVE-2: name, or 'name v2', 'name v3' ... if it is taken (Windows names are not case sensitive)."""
    taken = {e.lower() for e in existing}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} v{n}".lower() in taken:
        n += 1
    return f"{name} v{n}"


_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def safe_name(name: str, fallback: str = "Untitled") -> str:
    """SAVE-4: a file or folder name Windows accepts. Accents and dashes are kept."""
    s = _ILLEGAL.sub(" ", str(name))
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ").strip()
    if not s:
        return fallback
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    return s


def path_too_long(path, limit: int = MAX_PATH_WARN) -> bool:
    return len(str(path)) > limit
