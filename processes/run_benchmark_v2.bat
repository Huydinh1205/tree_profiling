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
echo.
echo Press Enter to run all 3 models (about 40 minutes, li2012 is slow).
echo Type F then Enter for the fast run (CHM watershed + Dalponte only, a few minutes).
set "MODE="
set /p "MODE=Choice: "
set "MODELS=chm_watershed dalponte_py li2012_py"
set "OUT=results\v2_100"
if /i "%MODE%"=="F" (
  set "MODELS=chm_watershed dalponte_py"
  set "OUT=results\v2_100_fast"
)

REM ---- 5. Run (plots already done are skipped, so you can stop and run again) ----
echo.
echo Running... results go to %CD%\%OUT%
%PY% benchmark_folder.py "%V2%\plots_100" --out "%OUT%" --models %MODELS%
if errorlevel 1 goto failed
start "" notepad "%OUT%\summary.md"
start "" "%OUT%"
echo.
echo Done. The summary has opened in Notepad.
pause
exit /b 0

:failed
echo.
echo Something went wrong. Send a screenshot of this window to Ryan.
pause
exit /b 1
