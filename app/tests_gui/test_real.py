"""The window on top of the REAL merge_sat.py and tree_info.py (only Docker is stood in for), on a
small made-up forest: the crown map draws the real outlines and the 3D view the real points.
Skipped when the scripts or their libraries are not there (see tests/test_real_scripts.py)."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from conftest import FakeDialogs
from tests.real_pipeline.prepare import Prepared, script_python, why_not
from treeprof_app import crowns, summary
from treeprof_app.appstate import AppState
from treeprof_app.gui.main import MainWindow
from treeprof_app.gui.results import ResultsView
from treeprof_app.runner import RunnerConfig

REASON = why_not()
pytestmark = pytest.mark.skipif(bool(REASON), reason=REASON)


def test_real_scripts_from_new_run_to_reopened_results(qtbot, tmp_path):
    made = Prepared(tmp_path)
    cfg = RunnerConfig(python=script_python(), scripts_dir=made.scripts, needs_copy=lambda p: True,
                       stop_containers=lambda: None, copy_retry_wait=0,
                       extra_env={"TREEPROF_TEST_SAT_RESULT": str(made.sat)})
    state = AppState(tmp_path / "home", runner_config=cfg, machine_runner=lambda cmd: 0)
    win = MainWindow(state, dialogs=FakeDialogs(), use_tray=False)
    qtbot.addWidget(win)
    win.show()
    try:
        win.handle_paths([str(made.input)])                        # as if the file was dropped on the window
        page = win.page
        qtbot.waitUntil(lambda: page.files[0]["report"] is not None and win.bg.running == 0, timeout=30_000)
        assert page.files[0]["report"].crs_name == "GDA2020 / MGA zone 55"
        page.run_by.setCurrentText("Sam")
        page.start.click()
        run_id = win.page.run_id
        qtbot.waitUntil(lambda: state.store.get(run_id)["status"] in ("finished", "failed"), timeout=300_000)
        assert state.store.get(run_id)["status"] == "finished", "\n".join(state.log_tail(run_id, 40))
        win.tick()
        view = win.page.inner
        assert isinstance(view, ResultsView)

        work = next((tmp_path / "home" / "runs").iterdir())
        trees = summary.load_trees(work / "sat_result_merged_trees.csv")
        outlines = crowns.read_crowns(work / "sat_result_merged_crowns.shp")
        assert len(view.map.items_by_id) == len(trees) == len(outlines)
        assert made.planted <= len(trees) < made.pieces            # the cut crowns were joined again
        tallest = max(trees, key=lambda t: t["height_m"])
        assert view.selected == tallest["tree_id"]
        item = view.map.items_by_id[tallest["tree_id"]]            # the real outline, to scale
        xs = [p[0] for p in outlines[tallest["tree_id"]]]
        assert abs(item.polygon().boundingRect().width() - (max(xs) - min(xs))) < 0.6

        qtbot.waitUntil(lambda: win.findChild(object, "tree_3d") is not None, timeout=60_000)
        v = win.findChild(object, "tree_3d")
        assert len(v.points) > 500 and abs(v.tree_height - tallest["height_m"]) < 2.0

        other = min(trees, key=lambda t: t["height_m"])
        QTest.mouseClick(view.map.viewport(), Qt.LeftButton, Qt.NoModifier, view.map.position_of(other["tree_id"]))
        assert view.selected == other["tree_id"]
        assert win.findChild(object, "fact_height").text() == f"{other['height_m']:,.1f} m"

        info = {w.objectName(): w.text() for w in view.findChildren(object) if w.objectName().startswith("info_")}
        assert info["info_trees_after_joining_crowns"] == str(len(trees))
        assert int(info["info_trees_before_joining_crowns"]) > len(trees)
        assert info["info_ground_used_for_heights"].startswith("Worked out from the points")

        view.save()
        qtbot.waitUntil(lambda: view.dialog.isVisible(), timeout=5000)
        view.dialog.findChild(object, "save_go").click()
        qtbot.waitUntil(lambda: view.dialog.step == "saved", timeout=60_000)
        view.dialog.accept()
        folder = made.job / "Project" / "Test Site – 13 Feb 25 – Tree Profiling"
        assert {"Test Site.treeprof", "Test Site_info.laz", "Test Site_crowns.prj"} <= {p.name for p in folder.iterdir()}

        win.handle_paths([str(folder / "Test Site.treeprof")])     # as if double-clicked in Explorer
        again = win.page
        assert isinstance(again, ResultsView) and len(again.map.items_by_id) == len(trees)
        qtbot.waitUntil(lambda: win.findChild(object, "tree_3d") is not None, timeout=60_000)
    finally:
        win.timer.stop()
        state.shutdown()
