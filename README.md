# Tree profiling: how to run

## Easiest way (no coding needed)

1. Download this repository (green "Code" button, "Download ZIP") and unzip it.
   Keep it outside OneDrive (e.g. `C:\work\tree_profiling`).
2. Open the `processes` folder.
3. Windows: drag your `.las` / `.laz` file onto `run_tree_profiling.bat`,
   or double-click it and paste the file path when asked
   (Shift + right-click the file, "Copy as path").
   Mac: double-click `run_tree_profiling.command` and drag the file into the window.
   (First time on Mac: right-click, "Open", to get past the security warning.)
4. Press Enter to process the whole file, or type a size such as `200` to test a
   200 x 200 m square in the middle of a big file first.
5. When it finishes, the report opens in your browser and the results folder opens
   (`processes/results/<file name>/`).

On Windows the .bat uses the machine's Python 3.10 directly (`py -3.10`, no venv).
If a library is missing it installs `requirements.txt` once, which takes a few minutes.
Python 3.10 to 3.13 work, 3.14 does not. Data and results stay on your computer: nothing is uploaded.

The rest of this page is the manual way, with all options.

All commands are for PowerShell on the company Windows machine, run from the
`processes` folder.

## 1. Setup (once)

The machine's default Python 3.14 does not work. Use Python 3.11.

```powershell
cd processes
py -0p                          # check that 3.11 is listed
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Or run `setup_venv.bat` once, which does the same. No need to `activate`:
always run scripts with `.venv\Scripts\python.exe`.

Keep the folder outside OneDrive (e.g. `C:\work\tree_profiling`).

## 2. Process 2: find trees in our LiDAR

```powershell
.venv\Scripts\python.exe process2_segment.py "D:\...\cloud_merged.las" --out results\site1 --site-name "Site name"
```

* Put the file path in double quotes (Shift + right-click the file, "Copy as path").
* Big file: add `--crop-size 200` to run on a 200 x 200 m square in the middle first.
* Results go to `processes\results\site1\`. Open `report.html` to present.

Each model writes a sub-folder with `trees_crowns.gpkg` (crown polygons), `trees_tops.gpkg`
(tree tops), `trees.csv` and `points_treeid.laz`. One row per tree, following the pilot
document (section 4.2 LiDAR metrics, section 5 data model):

| Field | Meaning |
|---|---|
| `tree_uid`, `model`, `tree_id` | e.g. `SITE1_TREE00042`, model that found it |
| `top_x`, `top_y`, `height_m` | tree top position and height above ground |
| `crown_base_m`, `crown_depth_m` | crown base estimate (10th percentile of point heights above 1 m) and depth |
| `crown_area_m2`, `crown_diam_m`, `crown_width_max_m`, `crown_elongation` | crown footprint, equivalent diameter, longest width, long/short ratio |
| `crown_volume_m3` | geometric crown volume (3D convex hull) |
| `foliage_volume_m3`, `canopy_density_pct` | occupied 0.5 m voxels, and their share of the crown hull |
| `h_p25_m`, `h_p50_m`, `h_p75_m`, `height_sd_m` | vertical profile and height variability |
| `nn_spacing_m`, `point_density_m2`, `n_points` | distance to nearest tree top, LiDAR points per m2 of crown, point count |
| `qc_flag` | `ok`, `few_points` (< 20 points) or `edge` (top within 2 m of the data edge) |

Crown base, volumes and density are LiDAR proxies, not field measurements.
`run_metadata.json` records the input file, point density, ground source, models and parameters.

Options:

| Option | Meaning |
|---|---|
| `--out` | Output folder |
| `--site-name` | Title at the top of the report (optional) |
| `--models` | `chm_watershed`, `dalponte_py`, `li2012_py`, `lidr_dalponte`, `lidr_li2012`, `segmentanytree`. Default: all; any that cannot run are skipped and the report says why |
| `--crop-size`, `--crop-center X Y` | Work on a square of N metres |
| `--external sat=file.laz` | Add a SegmentAnyTree result made elsewhere (e.g. usegalaxy.eu) |
| `--benchmark file.csv` | `scores_summary.csv` from process 1, to show accuracy in the report |
| `--ground` | `auto` (default), `file` or `csf` |
| `--allow-slow` | Let `li2012_py` / `lidr_li2012` run on more than 3 million points |

What each model needs:

| Model | Needs | Note |
|---|---|---|
| `chm_watershed` | Python only | Our CHM + watershed |
| `dalponte_py` | Python only | Python port of lidR dalponte2016, same crowns as lidR (checked) |
| `li2012_py` | Python only | Python port of lidR li2012, slow: use `--crop-size` on big files |
| `lidr_dalponte`, `lidr_li2012` | R + lidR | Original R versions, only to cross-check the ports. Skipped without R |
| `segmentanytree` | Docker + NVIDIA GPU | Without Docker: run it on usegalaxy.eu ("3DTrees: SegmentAnyTree") and pass the result with `--external sat=segmented_pc.laz` (ask before uploading company data) |

On the company machine (no R, no Docker) run the three Python models:

```powershell
.venv\Scripts\python.exe process2_segment.py "D:\...\cloud_merged.las" --out results\site1 --models chm_watershed dalponte_py li2012_py --crop-size 200
```

The first run of `dalponte_py` / `li2012_py` takes ~10 s extra to compile.

## 3. Process 1: score models on labelled data

```powershell
.venv\Scripts\python.exe process1_benchmark.py ..\data\RMIT\test.las --out results\benchmark_rmit
```

* Input: FOR-instance `.las` or FOR-instanceV2 `.ply` (labelled trees).
* Results go to `processes\results\benchmark_rmit\` (`report.md`, `scores_summary.csv`, figures).
* Same `--models`, `--external` and `--allow-slow` options as process 2.
* Pass its `scores_summary.csv` to process 2 with `--benchmark` to show accuracy in the site report.

The RMIT benchmark data is in the repo as `.laz` (`data/RMIT/`, see its README):
`train.laz` (159 trees), `test.laz` (64 trees) and `train_test.laz` (the whole plot, 222 trees).

## 4. Process 3: where does the segmentation go wrong?

```powershell
.venv\Scripts\python.exe process3_errors.py ..\data\RMIT\train_test.laz --bench results\benchmark_rmit_all --out results\errors_rmit_all
```

* Needs the `predictions\` folder written by process 1 (`--bench` = its `--out`).
* Sorts every labelled tree into `ok`, `merged` (several trees found as one), `split` (one tree found as
  several), `missed` (points left unassigned) or `boundary` (right tree, wrong outline), and every
  predicted tree into `ok`, `merge`, `fragment`, `ground/noise` or `boundary`.
* Writes `report.md`, `summary.csv`, one `errors_<model>.csv` with the x, y of every labelled tree and its
  error type, and `fig_errors_<model>.png` (map of the error types).

## 5. Merge labelled files

```powershell
.venv\Scripts\python.exe merge_labelled.py ..\data\RMIT\train.laz ..\data\RMIT\test.laz --out ..\data\RMIT\train_test.laz
```

Keeps classification and treeID, removes points with identical coordinates (RMIT train and test
overlap at the border and train has internal duplicates). Add `--shift-ids` for files from
different plots whose tree IDs would collide.

## 6. Benchmark on many labelled plots (FOR-instanceV2, Synthetic Forest)

Easiest: double-click `processes\run_benchmark_v2.bat`, twice.

1. **Run 1** downloads the FOR-instanceV2 test plots if needed (0.4 GB), converts them to 100 pts/m2, makes one file with all
   plots for SegmentAnyTree (`data\FOR-instanceV2\mosaic_100\mosaic_unlabelled.laz`), opens that folder and usegalaxy.eu,
   then runs the 3 classical models on this computer (about 40 minutes; type `F` for a fast run with 2 models).
2. **Meanwhile on usegalaxy.eu**: upload `mosaic_unlabelled.laz`, run "3DTrees: SegmentAnyTree", download the result and put it
   in the same `mosaic_100` folder, keeping Galaxy's file name (e.g. `Galaxy5-[Processed Files].zip`, it is really a LAZ).
3. If the Galaxy file is already there when the models finish, it is added straight away. Otherwise **run 2** of the .bat
   skips the models that are done and only adds SegmentAnyTree (a few minutes).

The result is `processes\results\v2_100\report.html`: F1 overall and by region, error types, one image per model for one plot
per region, and right/wrong close-ups for every model.

Manual steps:

```powershell
cd tree_profiling\processes
.\download_datasets.bat                                  # once, about 13 GB into ..\data\
Expand-Archive ..\data\FOR-instanceV2\test_data.zip ..\data\FOR-instanceV2
py -3.10 prep_labelled.py "..\data\FOR-instanceV2\test_data\*.ply" --out ..\data\FOR-instanceV2\plots_100 --density 100
py -3.10 benchmark_folder.py ..\data\FOR-instanceV2\plots_100 --out results\v2_100
```

* `prep_labelled.py` turns each labelled `.ply` into a labelled `.las` thinned to 100 pts/m2 (close to our DJI L2).
  Drop `--density` to keep full resolution (much slower for li2012).
* `benchmark_folder.py` runs process 1 on every plot (skips plots already done, so it can be stopped and re-run)
  and writes `results\v2_100\summary.md`: pooled precision / recall / F1 per model, per region and overall.
* For SegmentAnyTree run elsewhere: `mosaic_labelled.py build` puts all plots in one file to run once,
  `mosaic_labelled.py split` cuts the result back per plot, then pass it to process 1 with `--external sat=<plot>.laz`.
