@echo off
setlocal
cd /d "%~dp0"
title Tree profiling benchmark (FOR-instanceV2)

REM ---- 1. Find Python 3.10 (the company Python, no venv) ----
set "PY="
py -3.10 -c "import sys" >nul 2>&1 && set "PY=py -3.10"
if defined PY goto havepy
python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python"
if defined PY goto havepy
echo Python 3.10 was not found. Ask Ryan to check the Python install on this machine.
pause
exit /b 1
:havepy
%PY% -c "import laspy, lazrs, numpy, scipy, skimage, CSF, rasterio, geopandas, shapely, pyproj, matplotlib, plyfile, numba" >nul 2>&1
if not errorlevel 1 goto ready
echo Installing missing libraries (only once, a few minutes)...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installing the libraries failed. Send a screenshot of this window to Ryan.
  pause
  exit /b 1
)
:ready

REM ---- 2. Data: download if missing, unzip if needed ----
set "V2=..\data\FOR-instanceV2"
if exist "%V2%\test_data\*.ply" goto unzipped
if exist "%V2%\test_data.zip" goto unzip
echo Downloading the FOR-instanceV2 test plots (0.4 GB)...
mkdir "%V2%" 2>nul
curl.exe -L -C - -o "%V2%\test_data.zip" "https://zenodo.org/api/records/16742708/files/test_data.zip/content"
if errorlevel 1 (
  echo Download failed. Check the internet connection and run this again.
  pause
  exit /b 1
)
:unzip
echo Unzipping...
powershell -NoProfile -Command "Expand-Archive -Force '%V2%\test_data.zip' '%V2%'"
:unzipped

REM ---- 3. Convert the labelled .ply plots to .las, thinned to 100 pts/m2 (like our DJI L2) ----
if exist "%V2%\plots_100\*.las" goto prepared
echo Preparing 29 labelled plots...
%PY% prep_labelled.py "%V2%\test_data\*.ply" --out "%V2%\plots_100" --density 100
if errorlevel 1 goto failed
:prepared

REM ---- 4. Which models? ----
set "MOS=%V2%\mosaic_100"
echo.
echo   Enter  = run the 3 classical models (about 40 minutes, li2012 is slow)
echo   F      = fast run: CHM watershed + Dalponte only (a few minutes)
echo   S      = add SegmentAnyTree results from usegalaxy.eu to the comparison
set "MODE="
set /p "MODE=Choice: "
set "MODELS=chm_watershed dalponte_py li2012_py"
set "OUT=results\v2_100"
set "EXTRA="
if /i "%MODE%"=="F" (
  set "MODELS=chm_watershed dalponte_py"
  set "OUT=results\v2_100_fast"
)
if /i "%MODE%"=="S" goto sat
goto run

REM ---- SegmentAnyTree: one upload to usegalaxy.eu for all plots ----
:sat
if not exist "%MOS%\index.csv" (
  echo Putting all plots in one file for upload...
  %PY% mosaic_labelled.py build "%V2%\plots_100\*.las" --out "%MOS%"
  if errorlevel 1 goto failed
)
if exist "%MOS%\sat_result.laz" goto satsplit
echo.
echo SegmentAnyTree needs an NVIDIA GPU, so run it on usegalaxy.eu (free account):
echo   1. Upload this file:  %CD%\%MOS%\mosaic_unlabelled.laz
echo   2. Search the tools for "3DTrees: SegmentAnyTree" and run it on that file.
echo   3. Download the result. Galaxy names it "...[Processed Files].zip" but it is a LAZ file:
echo      rename it to  sat_result.laz  and put it in the folder that opens now.
echo   4. Run this .bat again and choose S.
start "" "%MOS%"
start "" "https://usegalaxy.eu"
pause
exit /b 0
:satsplit
echo Cutting the SegmentAnyTree result back into plots...
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result.laz" --out "%MOS%\sat_by_plot"
if errorlevel 1 goto failed
set "EXTRA=--external sat=%MOS%\sat_by_plot"
echo The 3 classical models are run too if they are not done yet.

REM ---- 5. Run (plots already done are skipped, so you can stop and run again) ----
:run
echo.
echo Running... results go to %CD%\%OUT%
%PY% benchmark_folder.py "%V2%\plots_100" --out "%OUT%" --models %MODELS% %EXTRA% --title "Tree segmentation evaluation: FOR-instanceV2 test plots at 100 pts/m2"
if errorlevel 1 goto failed
start "" "%OUT%\report.html"
start "" "%OUT%"
echo.
echo Done. The evaluation report has opened in your browser.
pause
exit /b 0

:failed
echo.
echo Something went wrong. Send a screenshot of this window to Ryan.
pause
exit /b 1
