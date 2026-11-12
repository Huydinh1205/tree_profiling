"""Shared by the window tests: a real main window (never shown on a screen) on top of the stand-in
pipeline of tests/fake_pipeline, and dialogs that answer what the test put in instead of waiting for
a click.   Run from the app folder:   python -m pytest tests_gui
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")          # no screen needed

import pytest                                                  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from PySide6.QtWidgets import QPushButton                      # noqa: E402

from tests.helpers import write_las_stub                       # noqa: E402
from treeprof_app import saving                                # noqa: E402
from treeprof_app.appstate import AppState                     # noqa: E402
from treeprof_app.gui.common import apply_look                 # noqa: E402
from treeprof_app.gui.main import MainWindow                   # noqa: E402
from treeprof_app.runner import RunnerConfig                   # noqa: E402

FAKE = APP / "tests" / "fake_pipeline"


class FakeDialogs:
    """Stands in for the Windows dialogs. answers: title of a question -> the button to press."""

    def __init__(self):
        self.files = self.folder = self.project = self.program = self.typed = None
        self.answers, self.asked = {}, []

    def pick_files(self, initial=""):
        return self.files

    def pick_folder(self, initial=""):
        return self.folder

    def pick_project(self, initial=""):
        return self.project

    def pick_program(self, initial=""):
        return self.program

    def ask(self, title, text, buttons, danger=False):
        self.asked.append((title, text, list(buttons)))
        return self.answers.get(title, buttons[-1])                # not told what to press: the safe button

    def ask_text(self, title, text):
        self.asked.append((title, text, []))
        return self.typed


class Harness:
    def __init__(self, qtbot, tmp: Path, machine_code: int = 0, **conf):
        self.qtbot, self.tmp = qtbot, tmp
        self.job = tmp / "net" / "TEST-26001"
        folder = self.job / "Project" / "Test Site - 13 Feb 25 - LiDAR"
        folder.mkdir(parents=True)
        self.input = folder / "cloud.las"
        write_las_stub(self.input, 5000)
        self.cfg = RunnerConfig(python=sys.executable, scripts_dir=FAKE, needs_copy=lambda p: True,
                                stop_containers=lambda: None, copy_retry_wait=0,
                                extra_env={"FAKE_PIPELINE": json.dumps(conf or {"tiles": 2})},
                                prepare_view=lambda run: {"area_ha": 2.0})
        self.machine_code = machine_code
        self.dialogs = FakeDialogs()
        self.state = self.win = None
        self.start()

    def start(self) -> None:
        """Open the app (again): a new AppState on the same folder, as after closing and reopening."""
        self.state = AppState(self.tmp / "home", runner_config=self.cfg, machine_runner=lambda cmd: self.machine_code)
        self.win = MainWindow(self.state, dialogs=self.dialogs, use_tray=False)
        self.qtbot.addWidget(self.win)
        self.win.show()
        self.settle()

    def close(self) -> None:
        if self.state is not None and not self.state.closed:
            self.win.timer.stop()
            self.state.shutdown()

    # ---- the pipeline
    def fake(self, **conf) -> None:
        self.cfg.extra_env["FAKE_PIPELINE"] = json.dumps(conf)

    def add(self, site="Test Site") -> str:
        return self.state.add_run(self.input, site=site, run_by="Sam", capture_date="2025-02-13")

    def wait_status(self, run_id, *statuses, seconds=40) -> None:
        self.until(lambda: self.state.store.get(run_id)["status"] in statuses, seconds)

    def finished(self, **kw) -> str:
        run_id = self.add(**kw)
        self.wait_status(run_id, "finished")
        return run_id

    def save(self, run_id, name="Out"):
        res = self.state.open_results(self.state.project_path_for(run_id))
        return self.state.save(res, self.job / "Project", name, saving.default_selection(), site="Test Site", run_id=run_id)

    # ---- the window
    def until(self, condition, seconds=20) -> None:
        self.qtbot.waitUntil(condition, timeout=int(seconds * 1000))

    def settle(self) -> None:
        """Wait for work that was sent off the main thread (checks, storage sizes)."""
        self.until(lambda: self.win.bg.running == 0)
        self.qtbot.wait(20)

    def tick(self) -> None:
        self.win.tick()
        self.qtbot.wait(10)

    def pump(self) -> None:
        """Let Qt lay out and show what was just put on screen."""
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def find(self, name: str, root=None):
        self.pump()
        widget = (root or self.win).findChild(object, name)
        assert widget is not None, f"nothing named {name} on this screen"
        return widget

    def has(self, name: str, root=None) -> bool:
        return (root or self.win).findChild(object, name) is not None

    def text(self, name: str, root=None) -> str:
        return self.find(name, root).text()

    def button(self, text: str, root=None) -> QPushButton:
        self.pump()
        found = [b for b in (root or self.win).findChildren(QPushButton) if b.text() == text and b.isVisible()]
        assert found, f"no button '{text}' on this screen"
        return found[0]

    def buttons(self, root=None) -> list:
        self.pump()
        return [b.text() for b in (root or self.win).findChildren(QPushButton) if b.isVisible()]

    def press(self, text: str, root=None) -> None:
        self.button(text, root).click()
        self.qtbot.wait(10)

    def texts(self, root=None) -> str:
        """Every word on the screen, for 'is this sentence shown' checks."""
        from PySide6.QtWidgets import QLabel
        self.pump()
        return "\n".join(w.text() for w in (root or self.win).findChildren(QLabel) if w.isVisible())

    def status_on_disk(self, run_id: str) -> str:
        """The status of a run as it is written in the app's records (also after the app was closed)."""
        import sqlite3
        db = sqlite3.connect(str(self.tmp / "home" / "state" / "runs.sqlite"))
        try:
            return db.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
        finally:
            db.close()

    def set_status_on_disk(self, run_id: str, status: str) -> None:
        import sqlite3
        db = sqlite3.connect(str(self.tmp / "home" / "state" / "runs.sqlite"))
        db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        db.commit()
        db.close()

    def sees(self, sentence: str, root=None) -> bool:
        return sentence in self.texts(root)


@pytest.fixture(scope="session", autouse=True)
def styled(qapp):
    from PySide6.QtGui import QColor, QPalette
    dark = QPalette()                                              # as if Windows is set to dark mode
    for role, colour in ((QPalette.Window, "#202020"), (QPalette.Base, "#2B2B2B"), (QPalette.Button, "#2B2B2B"),
                         (QPalette.Text, "#FFFFFF"), (QPalette.WindowText, "#FFFFFF"), (QPalette.ButtonText, "#FFFFFF")):
        dark.setColor(role, QColor(colour))
    qapp.setPalette(dark)
    apply_look(qapp)                                               # what the app does at start
    return qapp


@pytest.fixture
def make(qtbot, tmp_path):
    """make(**conf) -> Harness, with conf for the stand-in pipeline (tiles, tile_seconds, fail, trees)."""
    made = []

    def build(machine_code: int = 0, **conf) -> Harness:
        h = Harness(qtbot, tmp_path, machine_code, **conf)
        made.append(h)
        return h
    yield build
    for h in made:
        h.close()


CSV_COLUMNS = ("tree_uid,tree_id,top_x,top_y,top_z,height_m,crown_diam_m,crown_width_max_m,crown_area_m2,crown_volume_m3,"
               "foliage_volume_m3,crown_base_m,crown_depth_m,canopy_density_pct,nn_spacing_m,n_points,h_p25_m,h_p50_m,h_p75_m,"
               "height_sd_m,point_density_m2,qc_flag,species_code,species_prob,foliage_condition,thermal_anomaly,confidence,"
               "koala_value")


def make_saved(folder: Path, n: int, points: bool = False) -> Path:
    """A saved-results folder with n made-up trees on a grid (heights 5 + i % 30 m, every 17th tree at
    the edge). points=True also writes a real small _info.laz (needs laspy) so the 3D view has data.
    Returns the .treeprof file."""
    import math

    from tests.helpers import write_crowns_shapefile
    from treeprof_app import projectfile

    folder.mkdir(parents=True, exist_ok=True)
    side = int(math.sqrt(n)) + 1
    rows, shapes, tops = [CSV_COLUMNS], [], []
    for i in range(1, n + 1):
        x, y = 500000.0 + (i % side) * 8.0, 6250000.0 + (i // side) * 8.0
        h, d = 5.0 + i % 30, 3.0 + (i % 5)
        qc = "edge" if i % 17 == 0 else "ok"
        rows.append(f"TEST_SITE_TREE{i:05d},{i},{x:.2f},{y:.2f},{100 + h:.2f},{h:.2f},{d:.2f},{d * 1.1:.2f},{math.pi * d * d / 4:.2f},"
                    f"{h * d:.1f},{h * d / 3:.1f},{h / 3:.2f},{h * 2 / 3:.2f},,{8.0:.2f},{100 + i},{h / 4:.2f},{h / 2:.2f},"
                    f"{h * 0.75:.2f},2.0,80.0,{qc},,,,,,")
        r = d / 2
        shapes.append((i, [(x - r, y - r), (x + r, y - r), (x + r, y + r), (x - r, y + r)]))
        tops.append((i, x, y, h, r))
    (folder / "Test Site_trees.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    write_crowns_shapefile(folder / "Test Site_crowns.shp", shapes, z=100.0)
    if points:
        import laspy
        import numpy as np
        rng = np.random.default_rng(0)
        xs, ys, zs, ids = [], [], [], []
        for i, x, y, h, r in tops:
            k = 300
            xs.append(x + rng.normal(0, r / 2, k))
            ys.append(y + rng.normal(0, r / 2, k))
            zs.append(100 + rng.uniform(0, h, k))
            ids.append(np.full(k, i, np.int32))
        header = laspy.LasHeader(point_format=7, version="1.4")
        header.offsets, header.scales = [500000.0, 6250000.0, 0.0], [0.001, 0.001, 0.001]
        header.add_extra_dim(laspy.ExtraBytesParams(name="TreeID", type=np.int32))
        las = laspy.LasData(header)
        las.x, las.y, las.z = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)
        las["TreeID"] = np.concatenate(ids)
        las.write(str(folder / "Test Site_info.laz"))
    else:
        write_las_stub(folder / "Test Site_info.laz", 1000, laz=True)
    project = projectfile.new_project(site_name="Test Site", run_by="Sam", app_version="0.2.0", capture_date="2025-02-13",
                                      files={"trees_csv": "Test Site_trees.csv", "crowns_shp": "Test Site_crowns.shp",
                                             "info_laz": "Test Site_info.laz"})
    path = folder / "Test Site.treeprof"
    projectfile.write_project(path, project)
    return path
