# Tree Profiling app

A desktop app for the tree profiling pipeline in `../processes`, for people who do not use the
command line: pick a point cloud, press Start, look at the trees, save the results, reopen them later
from a `.treeprof` file.

The app does not change the pipeline. It calls the same scripts as `run_segmentanytree.bat` and
`tree_info.bat` (`sat_docker.py`, then `merge_sat.py`, then `tree_info.py`) and shows what they print.

## Status

Work in progress. This folder holds the parts that do not depend on the user interface:

| Module | What it does |
|---|---|
| `treeprof_app/naming.py` | Site name and capture date from the folder name, suggested save folder, safe Windows names |
| `treeprof_app/projectfile.py` | The `.treeprof` project file (write, read, check, reopen after the folder moved) |
| `treeprof_app/steps.py` | The command of each step, the files it must leave, and the rule for "this step is finished" |
| `treeprof_app/lasheader.py` | Point count and extent of a LAS / LAZ file from its header, no library needed |
| `treeprof_app/progress.py` | Progress from the lines the scripts print; plain messages for known failures |
| `treeprof_app/filecopy.py` | Copy a big file with a checksum, resuming when a network drive drops |
| `treeprof_app/saving.py` | Save the results to a folder the user chose; the `.treeprof` file is written last |
| `treeprof_app/summary.py` | Read the tree table and work out the overview numbers |

Still to come: the job runner (queue, cancel, resume), the 3D data of each tree, and the screens.

## Tests

No extra packages are needed (Python 3.10 or newer):

```
cd app
python -m unittest discover -s tests -t .
```

`pytest tests` works too. One test compares the LAS header reader with `laspy` and is skipped when
`laspy` is not installed. The sample script outputs in `tests/test_progress.py` are written from the
scripts' print statements; real captured outputs will replace them.
