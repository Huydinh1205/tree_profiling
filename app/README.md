# Tree Profiling app

A desktop app for the tree profiling pipeline in `../processes`, for people who do not use the
command line: pick a point cloud, press Start, look at the trees, save the results, reopen them later
from a `.treeprof` file.

The app does not change the pipeline. It calls the same scripts as `run_segmentanytree.bat` and
`tree_info.bat` (`sat_docker.py`, then `merge_sat.py`, then `tree_info.py`) and shows what they print.

It is a normal Windows program with its own window, built with Qt (PySide6).

## On the processing computer (Windows)

1. Put the repository on a local disk, outside OneDrive (for example `C:\TreeProfiling\tree_profiling`).
2. Double-click `app\setup_windows.bat`. It makes the app's own Python environment (`app\.venv`), runs
   the tests, puts a **Tree Profiling** icon on the Desktop and in the Start menu, and makes a
   double-clicked `.treeprof` file open in the app. No administrator rights are needed. Run it again
   after moving the folder.
3. Start the app with the Desktop icon (or `app\Tree Profiling.bat`).

To get a newer version: close the app, then double-click `app\update_windows.bat`.

Things to know:

- Drop a `.las` / `.laz` file on the window to start a run with it, or a `.treeprof` file (or its
  folder) to open saved results. Browse and pasting a path work too.
- Closing the window while a run is going asks what to do. "Keep running in the background" hides the
  window; the run goes on and the app stays as an icon next to the clock. Click that icon to get the
  window back. Starting the app a second time also just shows the window that is already there.
- Runs, results and the app's records are kept in `C:\TreeProfiling` (`runs`, `state`). Results are
  never deleted automatically; Settings shows what is kept and lets you delete it.
- The processing scripts run with the computer's Python 3.10 (`py -3.10`), exactly as the `.bat` files
  in `processes` do. The app's own environment only holds what `requirements.txt` lists.
- What the app prints goes to `C:\TreeProfiling\state\app.log`. "Send log" makes a zip on the Desktop
  with the logs of a run; it never contains point clouds or results.
- The 3D view of a tree is drawn by the app itself, without the graphics card, so it also works
  through Remote Desktop.

## What is where

| File | What it does |
|---|---|
| `treeprof_app/__main__.py`, `launch.pyw` | Starts the app; setup and update helpers |
| `treeprof_app/gui/main.py` | The window: which screen is shown, dropped files, closing, the icon next to the clock, one copy at a time |
| `treeprof_app/gui/pages.py` | Home, New run, progress of a run |
| `treeprof_app/gui/results.py` | Results: crown map, one tree in 3D, tree table, run info |
| `treeprof_app/gui/dialogs.py` | Save dialog, Settings and Storage |
| `treeprof_app/gui/common.py` | The look, small building blocks, work off the main thread, standard dialogs |
| `treeprof_app/present.py` | What the screens show, worked out without any screen code (wording, table, map geometry and colours) |
| `treeprof_app/appstate.py` | Everything the screens need: queue, results, save, storage |
| `treeprof_app/runner.py` | Runs one job: copy, the three scripts, progress, cancel, resume, retry |
| `treeprof_app/store.py` | The app's records (SQLite): runs, finished steps, saves |
| `treeprof_app/steps.py` | The command of each step, the files it must leave, the rule for "this step is finished" |
| `treeprof_app/progress.py` | Progress from the lines the scripts print; plain messages for known failures |
| `treeprof_app/checks.py` | Is this computer ready (Docker, graphics card, disk), can this file be run |
| `treeprof_app/viewdata.py` | Points of each tree for the 3D view, read from `_info.laz` in blocks |
| `treeprof_app/crowns.py` | Crown outlines for the map, read from the shapefile without a GIS library |
| `treeprof_app/saving.py`, `filecopy.py` | Save to a chosen folder; copy big files with a checksum |
| `treeprof_app/projectfile.py` | The `.treeprof` project file |
| `treeprof_app/naming.py`, `summary.py`, `lasheader.py`, `settings.py` | Names and dates, overview numbers, LAS header, settings |
| `treeprof_app/desktop.py`, `winproc.py` | Windows itself: open a folder, shortcuts, file type, no console windows |

## Tests

```
cd app
python -m unittest discover -s tests -t .                 core: nothing with a window
python -m pip install -r requirements-dev.txt
python -m pytest tests_gui                                the window, driven by the tests (no screen needed)
```

- `tests/fake_pipeline` holds stand-ins for the three scripts, so most tests need no Docker, graphics
  card or point cloud.
- `tests/test_real_scripts.py` and `tests_gui/test_real.py` run the **real** `merge_sat.py` and
  `tree_info.py` on a small made-up forest (`tests/real_pipeline`); only the Docker step is stood in
  for. They are skipped when the scripts' libraries are missing. `TREEPROF_SCRIPT_PYTHON` names the
  Python that has them (default `py -3.10` on Windows).
- `tests_gui/test_results.py` also opens a site of 50,000 trees and checks that it stays quick.
- `.github/workflows/app-tests.yml` runs all of this on Windows and Linux for every change.

Not covered by automatic tests, to check by hand on the processing computer: the real Docker step,
Cancel stopping the container, the icon next to the clock, the shortcuts, and use through Remote Desktop.
