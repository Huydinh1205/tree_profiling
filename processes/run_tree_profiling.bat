@echo off
setlocal
cd /d "%~dp0"
title Tree profiling

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
echo Using:
%PY% -c "import sys; print(sys.executable, sys.version.split()[0])"

REM ---- 2. Install the libraries only if something is missing ----
%PY% -c "import laspy, lazrs, numpy, scipy, skimage, CSF, rasterio, geopandas, shapely, pyproj, matplotlib, plyfile, numba" >nul 2>&1
if not errorlevel 1 goto ready
echo Some libraries are missing. Installing them now (only happens once, a few minutes).
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installing the libraries failed. Send a screenshot of this window to Ryan.
  pause
  exit /b 1
)
:ready

REM ---- 3. Which file? (dragged onto this .bat, or pasted) ----
set "LAS=%~1"
if defined LAS goto check
:ask
echo.
echo Drag your LiDAR file (.las or .laz) into this window, or paste its path, then press Enter.
echo Tip: in File Explorer, Shift + right-click the file and choose "Copy as path".
set "LAS="
set /p "LAS=File: "
if not defined LAS goto ask
:check
set "LAS=%LAS:"=%"
if exist "%LAS%" goto found
echo Cannot find this file: "%LAS%"
goto ask
:found
for %%F in ("%LAS%") do set "NAME=%%~nF"

REM ---- 4. Whole file or a test square? ----
echo.
echo Press Enter to process the whole file.
echo For a quick test on a big file, type a size in metres (for example 200) to use only a square in the middle.
set "CROP="
set /p "CROP=Size (Enter = whole file): "
set "OUT=results\%NAME%"
set "CROPARG="
if not defined CROP goto run
set "OUT=results\%NAME%_crop%CROP%m"
set "CROPARG=--crop-size %CROP%"

REM ---- 5. Run ----
:run
echo.
echo Working on %NAME% ... results will be in %CD%\%OUT%
echo li2012 is slow: about 3 minutes for 3 million points, much longer for bigger files.
%PY% process2_segment.py "%LAS%" --out "%OUT%" --site-name "%NAME%" --models chm_watershed dalponte_py li2012_py --allow-slow %CROPARG%
if errorlevel 1 (
  echo.
  echo Something went wrong. Send a screenshot of this window to Ryan.
  pause
  exit /b 1
)
start "" "%OUT%\report.html"
start "" "%OUT%"
echo.
echo Done. The report has opened in your browser.
pause
