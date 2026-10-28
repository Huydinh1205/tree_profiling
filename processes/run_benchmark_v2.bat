@echo off
setlocal
cd /d "%~dp0"
title Tree segmentation benchmark (FOR-instanceV2)
REM Run 1: prepares everything, opens usegalaxy.eu for SegmentAnyTree, then runs the 3 classical models here.
REM Run 2 (after you put the Galaxy result in data\FOR-instanceV2\mosaic_100): adds SegmentAnyTree to the same report.

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
%PY% -c "import laspy, lazrs, numpy, scipy, skimage, CSF, rasterio, geopandas, shapely, pyproj, matplotlib, plyfile, numba, PIL" >nul 2>&1
if not errorlevel 1 goto ready
echo Installing missing libraries (only once, a few minutes)...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto failed
:ready

REM ---- 2. Data: download if missing, unzip if needed ----
set "V2=..\data\FOR-instanceV2"
set "MOS=%V2%\mosaic_100"
if exist "%V2%\test_data\*.ply" goto unzipped
if exist "%V2%\test_data.zip" goto unzip
echo Downloading the FOR-instanceV2 test plots (0.4 GB)...
mkdir "%V2%" 2>nul
curl.exe -L -C - -o "%V2%\test_data.zip" "https://zenodo.org/api/records/16742708/files/test_data.zip/content"
if errorlevel 1 goto failed
:unzip
echo Unzipping...
powershell -NoProfile -Command "Expand-Archive -Force '%V2%\test_data.zip' '%V2%'"
:unzipped

REM ---- 3. Labelled plots at 100 pts/m2 (like our DJI L2), and one file with all plots for Galaxy ----
if exist "%V2%\plots_100\*.las" goto prepared
echo Preparing 29 labelled plots...
%PY% prep_labelled.py "%V2%\test_data\*.ply" --out "%V2%\plots_100" --density 100
if errorlevel 1 goto failed
:prepared
if exist "%MOS%\index.csv" goto mosaic_ok
echo Putting all plots in one file for usegalaxy.eu...
%PY% mosaic_labelled.py build "%V2%\plots_100\*.las" --out "%MOS%"
if errorlevel 1 goto failed
:mosaic_ok

REM ---- 4. SegmentAnyTree result already in the folder? ----
set "EXTRA="
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 goto nosat
echo SegmentAnyTree result found: it will be added to the report.
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result.laz" --out "%MOS%\sat_by_plot" >nul
if errorlevel 1 goto failed
set "EXTRA=--external sat=%MOS%\sat_by_plot"
goto choose
:nosat
echo.
echo ============================================================================
echo  SegmentAnyTree runs on usegalaxy.eu (it needs an NVIDIA GPU). Do this now,
echo  while the classical models run on this computer:
echo    1. Upload  mosaic_unlabelled.laz  from the folder that opens now.
echo    2. Search the tools for "3DTrees: SegmentAnyTree" and run it on that file.
echo    3. When it is done, download the result and put it in that SAME folder.
echo       Keep the name Galaxy gives it (e.g. "Galaxy5-[Processed Files].zip").
echo    4. Run this .bat again: it adds SegmentAnyTree to the report.
echo ============================================================================
start "" "%MOS%"
start "" "https://usegalaxy.eu"

REM ---- 5. Which classical models? ----
:choose
echo.
echo   Enter = 3 classical models (about 40 minutes the first time, li2012 is slow)
echo   F     = fast: CHM watershed + Dalponte only (a few minutes)
echo   (models already done are skipped, so a second run only adds what is new)
set "MODE="
set /p "MODE=Choice: "
set "MODELS=chm_watershed dalponte_py li2012_py"
set "OUT=results\v2_100"
if /i "%MODE%"=="F" set "MODELS=chm_watershed dalponte_py"
if /i "%MODE%"=="F" set "OUT=results\v2_100_fast"

REM ---- 6. Run ----
echo.
echo Running... results go to %CD%\%OUT%
%PY% benchmark_folder.py "%V2%\plots_100" --out "%OUT%" --models %MODELS% %EXTRA% --title "Tree segmentation evaluation: FOR-instanceV2 test plots at 100 pts/m2"
if errorlevel 1 goto failed

REM ---- 7. Galaxy finished while the models were running? Add it now ----
if defined EXTRA goto show
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 goto show_nosat
echo SegmentAnyTree result found: adding it to the report...
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result.laz" --out "%MOS%\sat_by_plot" >nul
if errorlevel 1 goto failed
%PY% benchmark_folder.py "%V2%\plots_100" --out "%OUT%" --models %MODELS% --external sat=%MOS%\sat_by_plot --title "Tree segmentation evaluation: FOR-instanceV2 test plots at 100 pts/m2"
if errorlevel 1 goto failed
goto show
:show_nosat
echo.
echo SegmentAnyTree is not in this report yet. When Galaxy is done, put its result in
echo %CD%\%MOS%  and run this .bat again (it only adds SegmentAnyTree, a few minutes).
:show
start "" "%OUT%\report.html"
echo.
echo Done. The evaluation report has opened in your browser:  %CD%\%OUT%\report.html
pause
exit /b 0

:failed
echo.
echo Something went wrong. Send a screenshot of this window to Ryan.
pause
exit /b 1
