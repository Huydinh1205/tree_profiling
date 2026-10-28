@echo off
REM Download the open benchmark datasets for tree profiling (Windows, uses the built-in curl.exe).
REM Run from the processes folder. Files go to ..\data\. Re-run to resume an interrupted download.
setlocal
cd /d "%~dp0"
set "D=..\data"
mkdir "%D%\FOR-instanceV2" 2>nul
mkdir "%D%\SyntheticForest" 2>nul
mkdir "%D%\FOR-instance" 2>nul

echo === FOR-instanceV2 (Zenodo 16742708, GPL-3.0): labelled test plots 0.4 GB, train/val 2.4 GB
curl.exe -L -C - -o "%D%\FOR-instanceV2\test_data.zip"      "https://zenodo.org/api/records/16742708/files/test_data.zip/content"
curl.exe -L -C - -o "%D%\FOR-instanceV2\train_val_data.zip" "https://zenodo.org/api/records/16742708/files/train_val_data.zip/content"

echo === Synthetic Forest (Zenodo 17568131, CC BY 4.0): 3 synthetic eucalypt scenes, 8.6 GB
curl.exe -L -C - -o "%D%\SyntheticForest\WoodLand.pcd"     "https://zenodo.org/api/records/17568131/files/WoodLand.pcd/content"
curl.exe -L -C - -o "%D%\SyntheticForest\OpenForest.pcd"   "https://zenodo.org/api/records/17568131/files/OpenForest.pcd/content"
curl.exe -L -C - -o "%D%\SyntheticForest\ClosedForest.pcd" "https://zenodo.org/api/records/17568131/files/ClosedForest.pcd/content"

echo === FOR-instance v1 (Zenodo 8287792, CC BY 4.0): whole dataset 1.6 GB (RMIT alone is already in data\RMIT)
curl.exe -L -C - -o "%D%\FOR-instance\FORinstance_dataset.zip" "https://zenodo.org/api/records/8287792/files/FORinstance_dataset.zip/content"

echo.
echo Done. Unzip the two FOR-instance zips in place. Then, for example:
echo   python prep_labelled.py ..\data\FOR-instanceV2\test_data\*.ply --out ..\data\FOR-instanceV2\plots_100 --density 100
pause
