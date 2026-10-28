@echo off
setlocal
cd /d "%~dp0"
title Tree segmentation benchmark
REM Run 1: prepares the data, opens usegalaxy.eu for SegmentAnyTree, then runs the classical models here.
REM Run 2 (after you put the Galaxy result in the mosaic folder it shows): adds SegmentAnyTree to the same report.
REM One report for everything: results\benchmark_100\report.html

REM ---- Python 3.10 (the company Python, no venv) ----
set "PY="
py -3.10 -c "import sys" >nul 2>&1 && set "PY=py -3.10"
if defined PY goto havepy
python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python"
if defined PY goto havepy
echo Python 3.10 was not found. Ask Ryan to check the Python install on this machine.
pause
exit /b 1
:havepy
%PY% -c "import laspy, lazrs, numpy, scipy, skimage, CSF, rasterio, geopandas, shapely, pyproj, matplotlib, plyfile, numba, PIL, pandas" >nul 2>&1
if not errorlevel 1 goto ready
echo Installing missing libraries (only once, a few minutes)...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto failed
:ready

REM ---- Which data, which models ----
echo.
echo Which labelled data? (one report either way)
echo   Enter = both: FOR-instanceV2 (29 real drone plots, 1744 trees) + Synthetic Forest (3 eucalypt scenes, 407 trees)
echo   1     = FOR-instanceV2 only  (downloads 0.4 GB if needed)
echo   2     = Synthetic Forest only (downloads 8.6 GB if needed)
set "DS="
set /p "DS=Choice: "
echo.
echo Which classical models?
echo   Enter = 3 models (CHM watershed, Dalponte, li2012; li2012 is slow)
echo   F     = fast: CHM watershed + Dalponte only
echo   (plots and models already done are skipped, so a second run only adds what is new)
set "MODE="
set /p "MODE=Choice: "
set "MODELS=chm_watershed dalponte_py li2012_py"
if /i "%MODE%"=="F" set "MODELS=chm_watershed dalponte_py"

set "V2=..\data\FOR-instanceV2"
set "SYN=..\data\SyntheticForest"
set "OUT=results\benchmark_100"
if "%DS%"=="1" goto sel_v2
if "%DS%"=="2" goto sel_syn
call :prep_v2 || goto failed
call :prep_syn || goto failed
set "PLOTS="%V2%\plots_100" "%SYN%\plots_100""
set "GLOBS="%V2%\plots_100\*.las" "%SYN%\plots_100\*.las""
set "MOS=..\data\mosaic_all_100"
set "TITLE=Tree segmentation evaluation: FOR-instanceV2 + Synthetic Forest at 100 pts/m2"
goto run
:sel_v2
call :prep_v2 || goto failed
set "PLOTS="%V2%\plots_100""
set "GLOBS="%V2%\plots_100\*.las""
set "MOS=..\data\mosaic_v2_100"
set "TITLE=Tree segmentation evaluation: FOR-instanceV2 at 100 pts/m2"
goto run
:sel_syn
call :prep_syn || goto failed
set "PLOTS="%SYN%\plots_100""
set "GLOBS="%SYN%\plots_100\*.las""
set "MOS=..\data\mosaic_syn_100"
set "TITLE=Tree segmentation evaluation: Synthetic Forest at 100 pts/m2"

:run
REM ---- one file with all selected plots, for usegalaxy.eu ----
if exist "%MOS%\index.csv" goto mos_ok
echo Putting all plots in one file for usegalaxy.eu...
%PY% mosaic_labelled.py build %GLOBS% --out "%MOS%" || goto failed
:mos_ok
set "EXTRA="
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 goto nosat
echo SegmentAnyTree result found in %MOS%: it will be in the report.
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result.laz" --out "%MOS%\sat_by_plot" >nul || goto failed
set "EXTRA=--external sat=%MOS%\sat_by_plot"
goto models
:nosat
echo.
echo ============================================================================
echo  SegmentAnyTree runs on usegalaxy.eu (it needs an NVIDIA GPU). Do this now,
echo  while the classical models run on this computer:
echo    1. Upload  mosaic_unlabelled.laz  from the folder that opens now:
echo       %CD%\%MOS%
echo    2. Search the tools for "3DTrees: SegmentAnyTree" and run it on that file.
echo    3. When it is done, download the result and put it in that SAME folder.
echo       Keep the name Galaxy gives it (e.g. "Galaxy5-[Processed Files].zip").
echo    4. Run this .bat again (same choices): it adds SegmentAnyTree to the report.
echo ============================================================================
start "" "%MOS%"
start "" "https://usegalaxy.eu"

:models
echo.
echo Running %MODELS% ... results go to %CD%\%OUT%
%PY% benchmark_folder.py %PLOTS% --out "%OUT%" --models %MODELS% %EXTRA% --title "%TITLE%" || goto failed
if defined EXTRA goto show
REM ---- Galaxy finished while the models were running? Add it now ----
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 goto show_nosat
echo SegmentAnyTree result found: adding it to the report...
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result.laz" --out "%MOS%\sat_by_plot" >nul || goto failed
%PY% benchmark_folder.py %PLOTS% --out "%OUT%" --models %MODELS% --external sat=%MOS%\sat_by_plot --title "%TITLE%" || goto failed
goto show
:show_nosat
echo.
echo SegmentAnyTree is not in this report yet. When Galaxy is done, put its result in
echo %CD%\%MOS%  and run this .bat again with the same choices.
:show
start "" "%OUT%\report.html"
echo.
echo Done. Report: %CD%\%OUT%\report.html
pause
exit /b 0

:failed
echo.
echo Something went wrong. Send a screenshot of this window to Ryan.
pause
exit /b 1


REM =============================== subroutines ===============================

:prep_v2
if exist "%V2%\plots_100\*.las" exit /b 0
if exist "%V2%\test_data\*.ply" goto v2_unzipped
if exist "%V2%\test_data.zip" goto v2_unzip
echo Downloading the FOR-instanceV2 test plots (0.4 GB)...
mkdir "%V2%" 2>nul
curl.exe -L -C - -o "%V2%\test_data.zip" "https://zenodo.org/api/records/16742708/files/test_data.zip/content" || exit /b 1
:v2_unzip
echo Unzipping...
powershell -NoProfile -Command "Expand-Archive -Force '%V2%\test_data.zip' '%V2%'" || exit /b 1
:v2_unzipped
echo Preparing 29 labelled plots at 100 pts/m2...
%PY% prep_labelled.py "%V2%\test_data\*.ply" --out "%V2%\plots_100" --density 100 || exit /b 1
exit /b 0

:prep_syn
if exist "%SYN%\plots_100\*.las" exit /b 0
mkdir "%SYN%" 2>nul
for %%S in (WoodLand OpenForest ClosedForest) do (
  if not exist "%SYN%\%%S.pcd" (
    echo Downloading Synthetic Forest %%S.pcd ^(about 3 GB^)...
    curl.exe -L -C - -o "%SYN%\%%S.pcd" "https://zenodo.org/api/records/17568131/files/%%S.pcd/content" || exit /b 1
  )
)
echo Preparing the 3 synthetic scenes at 100 pts/m2 (40 million lines each, a few minutes per scene)...
%PY% prep_labelled.py "%SYN%\*.pcd" --out "%SYN%\plots_100" --density 100 || exit /b 1
exit /b 0
