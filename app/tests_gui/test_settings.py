"""Settings, Storage and the log for support (STO-1 to STO-5, SET-1 to SET-5, SYS-6)."""
import threading
import zipfile
from pathlib import Path

from treeprof_app import desktop
from treeprof_app.gui.dialogs import SettingsPage


def settings(h) -> SettingsPage:
    h.press("Settings")
    h.settle()
    assert isinstance(h.win.page, SettingsPage)
    return h.win.page


def test_storage_lists_what_is_kept_here(make):                                 # STO-3
    h = make()
    settings(h)
    assert h.text("storage_empty") == "No runs are kept on this computer."
    run_id = h.finished()
    settings(h)
    assert h.text("storage_total").startswith("Tree Profiling results: ")
    assert h.sees("Test Site – 13 Feb 25") and "Not saved" in h.texts()
    assert h.has(f"delete_{run_id}")


def test_deleting_results_that_were_never_saved_needs_the_site_name(make):      # STO-3
    h = make()
    run_id = h.finished()
    workdir = Path(h.state.store.get(run_id)["workdir"])
    settings(h)
    h.find(f"delete_{run_id}").click()                             # the box is closed without typing
    assert workdir.is_dir() and h.dialogs.asked[-1][0] == "Test Site was never saved"
    h.dialogs.typed = "Wrong Site"
    h.find(f"delete_{run_id}").click()
    assert workdir.is_dir() and "Type the site name exactly" in h.win.last_notice
    h.dialogs.typed = "test site"
    h.find(f"delete_{run_id}").click()
    h.settle()
    assert not workdir.exists() and h.state.store.list_runs() == []
    assert h.text("storage_empty") == "No runs are kept on this computer."


def test_deleting_the_local_copy_of_a_saved_run_keeps_the_saved_folder(make):   # STO-3, STO-4
    h = make()
    run_id = h.finished()
    project = h.save(run_id, "Out")
    workdir = Path(h.state.store.get(run_id)["workdir"])
    settings(h)
    h.find(f"delete_{run_id}").click()                             # answered "Keep it"
    assert workdir.is_dir()
    h.dialogs.answers["Delete the local copy of Test Site?"] = "Delete local copy"
    h.find(f"delete_{run_id}").click()
    h.settle()
    assert not workdir.exists() and project.is_file()
    assert [r["id"] for r in h.state.store.list_runs()] == [run_id]     # still on Home, opened from the saved folder
    h.win.go_run(run_id)
    assert h.text("title") == "Test Site – 13 Feb 25"


def test_intermediate_tiles_can_be_deleted_when_there_are_some(make):           # STO-5
    h = make(tiles=2)
    run_id = h.finished()
    workdir = Path(h.state.store.get(run_id)["workdir"])
    assert list(workdir.glob("sat_result_tiles_*"))
    settings(h)
    h.find(f"tiles_{run_id}").click()
    h.settle()
    assert h.win.last_notice.startswith("Freed ") and not list(workdir.glob("sat_result_tiles_*"))
    assert not h.has(f"tiles_{run_id}") and (workdir / "sat_result_merged_trees.csv").is_file()


def test_folders_and_the_warning_level_are_kept(make):                          # SET-1 to SET-4
    h = make()
    settings(h)
    assert h.text("value_cloudcompare") == "Not set"
    shared = h.tmp / "shared"
    shared.mkdir()
    h.dialogs.folder = str(shared)
    h.press("Change", h.find("change_shared_root").parent())
    h.find("change_shared_root").click()
    assert h.text("value_shared_root") == str(shared)
    onedrive = h.tmp / "OneDrive" / "runs"
    onedrive.mkdir(parents=True)
    h.dialogs.folder = str(onedrive)
    before = h.text("value_runs_root")
    h.find("change_runs_root").click()                             # SET-1: not inside OneDrive
    assert h.text("value_runs_root") == before and "OneDrive" in h.win.last_notice
    h.dialogs.program = str(h.tmp / "CloudCompare.exe")
    h.find("change_cloudcompare").click()
    h.find("warn_gb").setValue(250)
    h.close()
    h.start()                                                      # opened again: still there
    assert h.state.settings.shared_root == str(shared) and h.state.settings.warn_free_gb == 250
    assert h.state.settings.cloudcompare == str(h.tmp / "CloudCompare.exe")


def test_this_computer_and_about(make):                                         # HOME-3, SET-5
    h = make(machine_code=1)
    settings(h)
    assert h.sees("Docker is not running") and "Tree Profiling 0." in h.text("about")
    answer = threading.Event()
    h.state._machine_runner = lambda cmd: 0 if answer.wait(20) else -1    # Docker takes its time to answer
    h.press("Check again")
    assert h.find("check_again").text() == "Checking..." and not h.find("check_again").isEnabled()
    answer.set()
    h.settle()
    assert h.find("check_again").text() == "Check again"
    assert h.sees("Docker running") and not h.sees("Docker is not running")


def test_send_log_makes_a_zip_without_results(make, monkeypatch):               # SYS-6
    h = make(tiles=2, fail={"join": "error"})
    run_id = h.add()
    h.wait_status(run_id, "failed")
    shown = []
    monkeypatch.setattr(desktop, "desktop_folder", lambda: h.tmp)
    monkeypatch.setattr(desktop, "reveal_file", lambda path: shown.append(Path(path)) or True)
    h.win.go_run(run_id)
    h.press("Send log to Ryan")
    assert len(shown) == 1 and shown[0].parent == h.tmp and "Send this file to Ryan" in h.win.last_notice
    with zipfile.ZipFile(shown[0]) as z:
        names = z.namelist()
    assert "summary.txt" in names and any(n.startswith("log/") for n in names)
    assert not any(n.endswith((".laz", ".las", ".csv", ".shp")) for n in names)
