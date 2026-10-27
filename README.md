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

The first run installs Python libraries and takes a few minutes. It needs Python 3.11
(3.10 to 3.13 work, 3.14 does not). Data and results stay on your computer: nothing is uploaded.

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
