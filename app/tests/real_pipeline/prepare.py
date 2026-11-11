"""Sets up what the tests with the real scripts need: a copy of the real processes folder with
sat_docker.py replaced by the stand-in next to this file, a made-up forest and its ready
'SegmentAnyTree result'. See tests/test_real_scripts.py."""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from treeprof_app import settings

HERE = Path(__file__).parent
SCRIPTS = Path(os.environ.get("TREEPROF_SCRIPTS_DIR") or HERE.parents[2] / "processes")
NEEDED = "import laspy, numpy, scipy, matplotlib, geopandas, shapely, CSF"


def script_python() -> list:
    env = os.environ.get("TREEPROF_SCRIPT_PYTHON")
    return shlex.split(env, posix=os.name != "nt") if env else settings.default_script_python()


def why_not() -> str:
    """Reason these tests cannot run here, or '' when they can."""
    if not (SCRIPTS / "merge_sat.py").is_file() or not (SCRIPTS / "tree_info.py").is_file():
        return f"the real scripts are not in {SCRIPTS}"
    try:
        import laspy  # noqa: F401
        import numpy  # noqa: F401
        import pyproj  # noqa: F401
    except ImportError as e:
        return f"the app's Python misses a library ({e.name})"
    try:
        r = subprocess.run(script_python() + ["-c", NEEDED], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return f"the scripts' Python cannot be started ({e})"
    if r.returncode != 0:
        return "the scripts' Python misses a library: " + (r.stderr.strip().splitlines() or ["?"])[-1]
    return ""


class Prepared:
    def __init__(self, tmp, n_trees: int = 12):
        from . import make_forest
        tmp = Path(tmp)
        self.scripts = tmp / "scripts"
        self.scripts.mkdir(parents=True)
        for p in SCRIPTS.glob("*.py"):
            shutil.copy2(p, self.scripts / p.name)
        shutil.copy2(HERE / "sat_docker.py", self.scripts / "sat_docker.py")
        self.job = tmp / "net" / "TEST-26001"
        self.input = self.job / "Project" / "Test Site - 13 Feb 25 - LiDAR" / "cloud.las"
        self.sat = tmp / "ready" / "sat_result.laz"
        self.points, self.planted, self.pieces = make_forest.make(self.input, self.sat, n_trees)
