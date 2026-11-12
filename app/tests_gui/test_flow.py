"""The app from Home to saved results, as a person uses it: what is on screen and what the buttons do
(test plan A-20 to A-30). Stand-in pipeline, real window."""
import json
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDropEvent

from treeprof_app import store as st
from treeprof_app.gui import main as gui_main
from treeprof_app.gui.pages import HomePage, MessagePage, NewRunPage, ProgressView, RunPage
from treeprof_app.gui.results import ResultsView


def results(h) -> ResultsView:
    page = h.win.page.inner if isinstance(h.win.page, RunPage) else h.win.page
    assert isinstance(page, ResultsView), type(page).__name__
    return page


def new_run_with_file(h, path=None):
    h.press("New run")
    h.find("path").setText(str(path or h.input))
    h.press("Add")
    h.until(lambda: all(f["report"] is not None for f in h.win.page.files))
    h.settle()
    return h.win.page


# ---------------------------------------------------------------------------------- Home
def test_home_when_nothing_was_run_yet(make):
    h = make()
    assert isinstance(h.win.page, HomePage)
    assert h.sees("No runs yet") and h.sees("Docker running") and h.sees("Graphics card ready")
    assert {"New run", "Open saved results", "Check again", "Settings", "Tree Profiling"} <= set(h.buttons())
    assert not h.has("machine_problem")


def test_home_says_what_is_wrong_with_the_computer(make):                       # HOME-3
    h = make(machine_code=1)
    assert h.text("machine_problem").startswith("Docker is not running. Open Docker Desktop")


def test_home_sees_docker_being_closed_without_a_click(make):                  # HOME-3
    h = make()
    assert h.sees("Docker running") and h.text("checked_at").startswith("Checked at ")
    h.machine_code = 1                                             # Docker Desktop is quit
    h.tick()
    h.settle()
    assert h.sees("Docker running")                                # not asked again within half a minute
    h.state._machine_at -= 31
    h.tick()
    h.settle()
    assert h.text("machine_problem").startswith("Docker is not running")


def test_check_again_shows_that_it_is_checking(make):                          # HOME-3
    import threading
    h = make()
    answer = threading.Event()
    h.state._machine_runner = lambda cmd: 0 if answer.wait(20) else -1   # Docker takes its time to answer
    h.press("Check again")
    assert h.find("check_again").text() == "Checking..." and not h.find("check_again").isEnabled()
    h.tick()                                                       # no second check on top of the first
    assert h.win.bg.running == 1
    answer.set()
    h.settle()
    assert h.find("check_again").text() == "Check again" and h.find("check_again").isEnabled()


def test_a_run_cut_off_by_a_restart_can_be_resumed(make):                       # SYS-5
    h = make(tiles=3, tile_seconds=1.0)
    run_id = h.add()
    h.wait_status(run_id, "running")
    h.win.timer.stop()
    h.state._stop = True                                           # the app dies without tidying up
    h.state.runner.cancel()
    h.state.worker.join(20)
    h.state.store.close()
    h.state.closed = True
    h.set_status_on_disk(run_id, st.RUNNING)                       # what a power cut leaves behind
    h.fake(tiles=3)
    h.start()                                                      # opened again
    assert h.state.store.get(run_id)["status"] == st.INTERRUPTED
    assert h.sees("‖  Interrupted")
    h.press("Resume")
    h.wait_status(run_id, "finished")


# ---------------------------------------------------------------------------------- New run
def test_new_run_reads_the_file_and_the_folder_name(make):                      # NEW-1 to NEW-4
    h = make()
    page = new_run_with_file(h)
    assert isinstance(page, NewRunPage)
    assert h.sees("cloud.las") and h.sees("The file can be read") and h.sees("5,000")
    assert h.find("site").text() == "Test Site" and h.find("date").text() == "13 Feb 25"
    assert h.find("start").isEnabled() and h.find("start").text() == "Start"


def test_browse_adds_the_chosen_files_once(make):
    h = make()
    h.dialogs.files = [str(h.input), str(h.input)]
    h.press("New run")
    h.press("Browse...")
    h.settle()
    assert [f["path"] for f in h.win.page.files] == [str(h.input)]
    h.press("Remove")
    assert h.win.page.files == [] and not h.find("start").isEnabled()


def test_a_file_that_is_not_a_point_cloud_cannot_be_started(make):              # NEW-3
    h = make()
    bad = h.tmp / "notes.las"
    bad.write_text("hello")
    new_run_with_file(h, bad)
    assert not h.find("start").isEnabled()
    assert not h.has("site")
    assert h.sees("Checks before starting")


def test_run_by_is_required_and_the_date_must_be_a_date(make):                  # NEW-4, NEW-5
    h = make()
    new_run_with_file(h)
    h.press("Start")
    assert "Run by" in h.win.last_notice and h.state.store.list_runs() == []
    h.find("run_by").setCurrentText("Sam")
    h.find("date").setText("next week")
    h.press("Start")
    assert "not a date" in h.win.last_notice and h.state.store.list_runs() == []
    h.find("date").setText("")
    h.find("site").setText("  ")
    h.press("Start")
    assert "site name" in h.win.last_notice and h.state.store.list_runs() == []


def test_the_name_is_remembered_and_advanced_sets_the_tile_size(make):          # NEW-5, NEW-9
    h = make()
    h.finished()
    h.win.go_home()
    new_run_with_file(h)
    assert h.find("run_by").currentText() == "Sam"
    assert not h.find("max_points").isVisible()
    h.press("Advanced")
    assert h.find("max_points").isVisible()
    h.find("max_points").setValue(8_000_000)
    h.press("Start")
    run = h.state.store.list_runs()[0]
    assert run["max_points"] == 8_000_000
    h.wait_status(run["id"], "finished")


# ---------------------------------------------------------------------------------- the whole way
def test_whole_way_from_new_run_to_saved(make):
    h = make(tiles=2, tile_seconds=0.4, trees=7)
    new_run_with_file(h)
    h.find("run_by").setCurrentText("Sam")
    h.press("Start")
    assert isinstance(h.win.page, RunPage) and isinstance(h.win.page.inner, ProgressView)
    run_id = h.win.page.run_id
    assert h.text("title") == "Test Site – 13 Feb 25" and h.sees("Find trees")
    h.wait_status(run_id, "finished")
    h.tick()                                                       # RUN-7: the screen changes by itself
    view = results(h)
    assert h.text("card_trees") == "7" and h.text("count") == "Showing 7 of 7 trees"
    assert h.text("saved_pill") == "!  Not saved"
    assert h.text("selected_tree", view).startswith("TEST_SITE_TREE")
    h.until(lambda: "3D view not available" in h.text("view_note", view))   # the stand-in writes no real points

    h.press("Save")
    dialog = view.dialog
    assert h.find("save_name", dialog).text() == "Test Site – 13 Feb 25 – Tree Profiling"
    assert h.find("save_parent", dialog).text() == str(h.job / "Project")
    assert h.find("file_info_laz", dialog).isChecked() and not h.find("file_info_laz", dialog).isEnabled()
    assert not h.find("file_sat_raw_laz", dialog).isChecked()
    h.press("Save", dialog)
    h.until(lambda: dialog.step == "saved")
    assert "Double-click the .treeprof file" in h.text("saved", dialog)
    h.press("Close", dialog)
    folder = h.job / "Project" / "Test Site – 13 Feb 25 – Tree Profiling"
    names = {p.name for p in folder.iterdir()}
    assert {"Test Site.treeprof", "Test Site_trees.csv", "Test Site_info.laz", "Test Site_crowns.shp"} <= names
    assert "Test Site_sat_raw.laz" not in names
    assert h.text("saved_pill") == "✓  Saved"
    assert h.text("info_saved_to", view) == str(folder)
    h.press("Tree Profiling")                                      # back to Home
    assert h.sees("✓  Finished") and "Test Site – 13 Feb 25 – Tree Profiling" in h.buttons()


def test_home_shows_the_run_that_is_going(make):                                # HOME-4
    h = make(tiles=3, tile_seconds=1.0)
    run_id = h.add()
    h.until(lambda: bool((h.state.current() or {}).get("live") is not None
                         and h.state.current()["live"].steps["find_trees"] == "running"
                         and h.state.current()["live"].label.startswith("Tile")))
    h.tick()
    assert h.text("current_title") == "Test Site – 13 Feb 25"
    assert h.text("current_step").startswith("Step 2 of 5: Find trees · Tile")
    h.press("View progress")
    assert isinstance(h.win.page.inner, ProgressView) and "Cancel run" in h.buttons()
    assert h.sees("You can close this window.")
    h.wait_status(run_id, "finished")


def test_cancel_asks_first_then_resume_finishes(make):                          # RUN-5
    h = make(tiles=3, tile_seconds=1.0)
    run_id = h.add()
    h.wait_status(run_id, "running")
    h.win.go_run(run_id)
    h.press("Cancel run")                                          # the question is answered "Keep running"
    h.qtbot.wait(300)
    assert h.state.store.get(run_id)["status"] == "running"
    h.dialogs.answers["Stop this run?"] = "Stop the run"
    h.press("Cancel run")
    h.wait_status(run_id, "cancelled")
    h.tick()
    assert h.text("stopped") == "This run was stopped."
    h.fake(tiles=3)
    h.press("Resume")
    h.wait_status(run_id, "finished")
    h.tick()
    assert isinstance(h.win.page.inner, ResultsView)


def test_too_large_file_offers_smaller_tiles(make):                             # RUN-8
    h = make(tiles=2, fail={"find_trees": "oom"})
    run_id = h.add()
    h.wait_status(run_id, "failed")
    h.win.go_run(run_id)
    assert "too large to process in one piece" in h.text("error_message")
    assert h.find("log").isVisible()                               # details are open when a run failed
    assert h.buttons().index("Try again with smaller tiles") < h.buttons().index("Try again")
    h.fake(tiles=2)
    h.press("Try again with smaller tiles")
    h.wait_status(run_id, "finished")
    assert h.state.store.get(run_id)["max_points"] == 10_000_000


def test_show_details_shows_what_the_scripts_print(make):                       # RUN-6
    h = make(tiles=3, tile_seconds=0.5)
    run_id = h.add()
    h.wait_status(run_id, "running")
    h.win.go_run(run_id)
    assert not h.find("log").isVisible()
    h.press("Show details")
    assert h.find("log").isVisible() and h.find("show_details").text() == "Hide details"
    h.until(lambda: (h.tick(), "tile" in h.find("log").toPlainText())[1])
    h.wait_status(run_id, "finished")


def test_second_file_waits_in_the_queue_and_can_be_removed(make):               # RUN-9, NEW-8
    h = make(tiles=3, tile_seconds=1.0)
    first = h.add()
    h.wait_status(first, "running")
    new_run_with_file(h)
    assert h.find("start").text() == "Add to queue"
    h.find("run_by").setCurrentText("Sam")
    h.press("Add to queue")
    second = h.win.page.run_id
    assert second != first and h.sees("This run starts when the run before it is finished.")
    h.press("Remove from queue")
    assert h.state.store.get(second)["status"] == "cancelled"
    h.dialogs.answers["Stop this run?"] = "Stop the run"
    h.state.cancel(first)
    h.wait_status(first, "cancelled")


def test_no_trees_found(make):                                                  # RES-10
    h = make(tiles=2, fail={"measure": "no_trees"})
    run_id = h.finished()
    h.win.go_run(run_id)
    assert isinstance(h.win.page.inner, MessagePage)
    assert h.text("message_title") == "No trees were found in this file"
    h.dialogs.answers["Delete this run?"] = "Delete"
    h.press("Delete this run")
    assert h.state.store.list_runs() == [] and isinstance(h.win.page, HomePage)


# ---------------------------------------------------------------------------------- Save
def test_same_folder_twice_asks_what_to_do(make):                               # SAVE-5
    h = make()
    run_id = h.finished()
    h.save(run_id, "Out")
    h.win.go_run(run_id)
    view = results(h)
    h.press("Save")
    dialog = view.dialog
    h.find("save_name", dialog).setText("Out")
    h.press("Save", dialog)
    h.until(lambda: dialog.step == "conflict")
    assert "already has results with the same name" in h.text("conflict", dialog)
    h.press("Keep both", dialog)
    h.until(lambda: dialog.step == "saved")
    names = {p.name for p in (h.job / "Project" / "Out").iterdir()}
    assert {"Test Site.treeprof", "Test Site (2).treeprof", "Test Site (2)_trees.csv"} <= names


def test_save_needs_a_folder_and_a_name(make):                                  # SAVE-1, SAVE-2
    h = make()
    h.win.go_run(h.finished())
    h.press("Save")
    dialog = results(h).dialog
    h.find("save_parent", dialog).setText(str(h.tmp / "does not exist"))
    h.press("Save", dialog)
    assert h.text("save_message", dialog) == "Choose the folder to save in."
    h.dialogs.folder = str(h.tmp)
    h.press("Change folder", dialog)
    assert h.find("save_parent", dialog).text() == str(h.tmp)
    h.find("save_name", dialog).setText("???")
    h.press("Save", dialog)
    assert h.text("save_message", dialog) == "Give the new folder a name."
    h.press("Cancel", dialog)
    assert not list(h.tmp.glob("*.treeprof")) and h.state.store.saves(h.state.store.list_runs()[0]["id"]) == []


def test_extra_files_are_saved_when_ticked(make):                               # SAVE-3
    h = make()
    h.win.go_run(h.finished())
    h.press("Save")
    dialog = results(h).dialog
    h.find("file_sat_raw_laz", dialog).setChecked(True)
    h.press("Save", dialog)
    h.until(lambda: dialog.step == "saved")
    folder = h.job / "Project" / "Test Site – 13 Feb 25 – Tree Profiling"
    assert (folder / "Test Site_sat_raw.laz").is_file() and not (folder / "Test Site_sat_merged.laz").exists()


def test_save_from_home_opens_the_dialog(make):
    h = make()
    h.finished()
    h.tick()
    assert h.sees("!  Not saved")
    h.press("Save")
    assert results(h).dialog.isVisible()


# ---------------------------------------------------------------------------------- opening saved results
def test_open_saved_results_in_every_way(make):                                 # OPEN-1 to OPEN-3, OPEN-8
    h = make(trees=5)
    run_id = h.finished()
    project = h.save(run_id, "Out")
    moved = h.tmp / "moved elsewhere"
    project.parent.rename(moved)                                   # OPEN-3: the folder was moved
    h.dialogs.project = str(moved / project.name)
    h.press("Open saved results")
    view = results(h)
    assert h.text("title") == "Test Site – 13 Feb 25" and h.text("count") == "Showing 5 of 5 trees"
    assert "Save a copy" in h.buttons() and not h.has("saved_pill")
    assert "Saved to" not in h.texts(view)
    h.win.go_home()
    h.win.handle_paths([str(moved)])                               # the folder itself
    assert h.text("title") == "Test Site – 13 Feb 25"
    h.press("Save a copy")
    dialog = results(h).dialog
    h.find("save_parent", dialog).setText(str(h.tmp))
    h.find("save_name", dialog).setText("Copy")
    h.press("Save", dialog)
    h.until(lambda: dialog.step == "saved")
    assert (h.tmp / "Copy" / "Test Site.treeprof").is_file()


def test_saved_results_that_cannot_be_opened_say_why(make):                     # OPEN-5 to OPEN-7
    h = make()
    project = h.save(h.finished(), "Out")
    (project.parent / "Test Site_trees.csv").unlink()
    h.win.go_saved(project)
    assert h.text("message_title") == "These results cannot be opened" and "Test Site_trees.csv" in h.text("message_text")
    newer = h.tmp / "newer.treeprof"
    newer.write_text(json.dumps({"format": "treeprof", "format_version": 99, "site_name": "x",
                                 "files": {"trees_csv": "a.csv", "crowns_shp": "a.shp"}}))
    h.win.go_saved(newer)
    assert "newer version of Tree Profiling" in h.text("message_text")
    broken = h.tmp / "broken.treeprof"
    broken.write_text("{ not json")
    h.win.go_saved(broken)
    assert h.text("message_title") == "These results cannot be opened"


def test_saved_without_the_point_cloud_still_opens(make):                       # OPEN-5
    h = make()
    project = h.save(h.finished(), "Out")
    (project.parent / "Test Site_info.laz").unlink()
    h.win.go_saved(project)
    assert "Test Site_info.laz is not in this folder" in h.text("missing")
    assert "3D view not available" in h.text("view_note")


def test_deleted_everywhere_says_not_found(make):                               # STO-4
    h = make()
    run_id = h.finished()
    project = h.save(run_id, "Out")
    h.state.delete_local_copy(run_id)
    h.win.go_run(run_id)
    assert isinstance(h.win.page.inner, ResultsView)               # opened from the saved folder
    project.unlink()
    h.win.go_run(run_id)
    assert h.text("message_title") == "Not found"
    h.win.go_home()
    assert h.sees("?  Not found")


# ---------------------------------------------------------------------------------- files handed to the app
def test_dropped_files_go_where_they_belong(make):                              # HOME-7, NEW-1, OPEN-1
    h = make()
    h.win.handle_paths([str(h.input)])
    assert isinstance(h.win.page, NewRunPage) and [f["path"] for f in h.win.page.files] == [str(h.input)]
    other = h.input.with_name("second.laz")
    other.write_bytes(h.input.read_bytes())
    drop = QMimeData()
    drop.setUrls([QUrl.fromLocalFile(str(other))])
    h.win.dropEvent(QDropEvent(QPoint(50, 50), Qt.CopyAction, drop, Qt.LeftButton, Qt.NoModifier))
    assert [Path(f["path"]).name for f in h.win.page.files] == ["cloud.las", "second.laz"]
    third = h.input.with_name("third.las")
    third.write_bytes(h.input.read_bytes())
    h.find("path").setText(QUrl.fromLocalFile(str(third)).toString())      # dropped on the text box itself
    h.press("Add")
    assert Path(h.win.page.files[-1]["path"]) == third
    h.settle()
    notes = h.tmp / "notes.txt"
    notes.write_text("x")
    h.win.go_home()
    h.win.handle_paths([str(notes)])
    assert "Drop a .las or .laz point cloud" in h.win.last_notice and isinstance(h.win.page, HomePage)


CLIENT = ("import json, sys, time; from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
          "from treeprof_app.gui.main import send_to_running; start = time.perf_counter(); "
          "ok = send_to_running(json.loads(sys.argv[1]), sys.argv[2]); "
          "print(json.dumps({'ok': ok, 'seconds': time.perf_counter() - start}))")


def _second_start(message, name):
    """What a second start of the app does: another process hands over a message."""
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    return subprocess.Popen([sys.executable, "-c", CLIENT, json.dumps(message), name], env=env, stdout=subprocess.PIPE,
                            text=True, cwd=str(Path(gui_main.__file__).resolve().parents[2]))


def _answer(proc) -> dict:
    return json.loads(proc.communicate(timeout=30)[0].strip().splitlines()[-1])


def test_second_start_hands_its_file_to_the_open_app(make, qtbot):              # SYS-1
    import time
    h = make()
    name = f"treeprof-test-{h.tmp.name}"
    assert gui_main.send_to_running({"open": "x"}, name) is False  # nothing is open under that name
    server = gui_main.InstanceServer(name)
    got = []
    server.received.connect(got.append)
    try:
        proc = _second_start({"open": str(h.input)}, name)
        qtbot.waitUntil(lambda: bool(got) and proc.poll() is not None, timeout=30_000)
        answer = _answer(proc)
        assert got == [{"open": str(h.input)}] and answer["ok"] is True
        assert answer["seconds"] < 2.5, answer                     # answered at once, not after the waiting time

        # the app is busy for a moment (drawing a big site): the message waits, it is not lost
        proc = _second_start({"open": "second.treeprof"}, name)
        time.sleep(3.0)                                            # nothing is handled meanwhile
        assert proc.poll() is None                                 # the second start is still waiting for the answer
        qtbot.waitUntil(lambda: len(got) == 2 and proc.poll() is not None, timeout=30_000)
        assert got[1] == {"open": "second.treeprof"} and _answer(proc)["ok"] is True

        both = [_second_start({"n": i}, name) for i in range(2)]   # two at the same moment
        qtbot.waitUntil(lambda: len(got) == 4 and all(p.poll() is not None for p in both), timeout=30_000)
        assert sorted(m["n"] for m in got[2:]) == [0, 1] and all(_answer(p)["ok"] for p in both)

        second = gui_main.InstanceServer(name)                     # the name is taken: the first one keeps it
        assert server.listening and not second.listening
        proc = _second_start({"ping": True}, name)
        qtbot.waitUntil(lambda: len(got) == 5 and proc.poll() is not None, timeout=30_000)
        assert got[4] == {"ping": True} and _answer(proc)["ok"] is True
        second.close()
    finally:
        server.close()
    proc = _second_start({"ping": True}, name)
    assert _answer(proc)["ok"] is False                            # closed: nobody answers


# ---------------------------------------------------------------------------------- closing the window (SYS-2)
def test_closing_with_nothing_running_quits(make):
    h = make()
    h.win.close()
    assert h.state.closed and h.dialogs.asked == []


def test_closing_while_a_run_is_going_asks(make):
    h = make(tiles=4, tile_seconds=1.0)
    run_id = h.add()
    h.wait_status(run_id, "running")
    h.win.close()                                                  # answered "Stay"
    assert h.dialogs.asked[-1][2] == [gui_main.KEEP, gui_main.QUIT, gui_main.STAY]
    assert h.win.isVisible() and not h.win.isMinimized() and not h.state.closed
    h.dialogs.answers["A run is still going"] = gui_main.KEEP
    h.win.close()
    assert not h.state.closed and (h.win.isMinimized() or not h.win.isVisible())
    assert h.state.store.get(run_id)["status"] == "running"       # the run goes on
    h.win.show_again()
    assert h.win.isVisible() and not h.win.isMinimized()
    h.dialogs.answers["A run is still going"] = gui_main.QUIT
    h.win.close()
    assert h.state.closed and h.status_on_disk(run_id) in ("cancelled", "interrupted")


# ---------------------------------------------------------------------------------- opened recently (HOME-5)
def test_saved_results_opened_from_elsewhere_are_listed_on_home(make):
    from conftest import make_saved
    h = make()
    assert not h.has("recent_title")                               # nothing opened yet: no empty box
    first = make_saved(h.tmp / "share" / "Site A", 6)
    second = make_saved(h.tmp / "share" / "Site B", 9)
    h.win.go_saved(first)
    h.win.go_saved(second.parent)                                  # opened through its folder
    h.win.go_home()
    h.settle()
    assert h.text("recent_title") == "Opened recently"
    assert h.find("recent_0").text() == "Test Site – 13 Feb 25" and h.sees("9 trees · opened Today")
    assert str(second.parent) in h.texts() and str(first.parent) in h.texts()
    h.find("recent_1").click()                                     # the older one: opens it and moves it to the top
    assert isinstance(results(h), ResultsView) and h.text("count") == "Showing 6 of 6 trees"
    assert [r["path"] for r in h.state.recent()] == [str(first), str(second)]

    h.close()
    second.unlink()                                                # deleted while the app was closed
    h.start()
    assert h.find("recent_1").text() == "Test Site – 13 Feb 25" and h.sees("?  Not found")
    assert not hasattr(h.find("recent_1"), "click")                # a plain line, not something to press
    h.find("forget_1").click()
    h.settle()
    assert [r["path"] for r in h.state.recent()] == [str(first)] and not h.sees("?  Not found")
    h.find("forget_0").click()
    h.settle()
    assert not h.has("recent_title")


def test_results_of_a_run_on_this_computer_are_not_listed_twice(make):
    h = make()
    project = h.save(h.finished(), "Out")
    h.win.go_saved(project)
    assert isinstance(h.win.page, ResultsView)
    h.win.go_home()
    h.settle()
    assert not h.has("recent_title") and h.sees("✓  Finished")
