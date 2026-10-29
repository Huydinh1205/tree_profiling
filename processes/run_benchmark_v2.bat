@echo off
setlocal
cd /d "%~dp0"
title Tree segmentation benchmark
REM Benchmark on labelled data. One report PER DATASET:
REM   FOR-instanceV2   -> results\benchmark_v2_100\report.html
REM   Synthetic Forest -> results\benchmark_syn_100\report.html
REM   CedarCypress3D   -> results\benchmark_ced_100\report.html
REM Run 1: prepares the data, makes one file per dataset for SegmentAnyTree on usegalaxy.eu, runs the classical models here.
REM Run 2 (same choices, after you put the Galaxy results in the mosaic folders): adds SegmentAnyTree to the reports.

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
echo Which labelled data? Each dataset gets its own report.
echo   1     = FOR-instanceV2   (real drone plots, Europe / Oceania / Asia, downloads 0.4 GB if needed)
echo   2     = Synthetic Forest (3 simulated eucalypt scenes, downloads 8.6 GB if needed)
echo   3     = CedarCypress3D   (34 real drone plots, Japan; 18 of them DJI Zenmuse L2 like ours, downloads 3 GB if needed)
echo   Enter = all three, one after the other
echo   (several at once is fine, e.g. 13)
set "DS="
set /p "DS=Choice: "
set "SEL="
if not defined DS set "SEL=v2 syn ced"
if not defined DS goto sel_done
echo %DS%| find "1" >nul && set "SEL=%SEL% v2"
echo %DS%| find "2" >nul && set "SEL=%SEL% syn"
echo %DS%| find "3" >nul && set "SEL=%SEL% ced"
:sel_done
if defined SEL goto sel_ok
echo Unknown choice. Type 1, 2, 3 or press Enter.
goto failed
:sel_ok
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
set "CED=..\data\CedarCypress3D"
REM Before 29/10/2026 everything went to one folder results\benchmark_100 (only FOR-instanceV2 had been run): keep that work.
if exist "results\benchmark_100" if not exist "results\benchmark_v2_100" ren "results\benchmark_100" "benchmark_v2_100"

REM ---- 1. data, and one file per dataset for usegalaxy.eu ----
for %%D in (%SEL%) do call :prep_%%D || goto failed
set "NEEDSAT="
for %%D in (%SEL%) do call :mosaic %%D || goto failed
if not defined NEEDSAT goto models
echo.
echo ============================================================================
echo  SegmentAnyTree runs on usegalaxy.eu (it needs an NVIDIA GPU). Do this now,
echo  while the classical models run on this computer. For EACH folder listed above
echo  (they open now):
echo    1. Upload  mosaic_unlabelled.laz  from that folder.
echo    2. Search the tools for "3DTrees: SegmentAnyTree" and run it on that file.
echo    3. When it is done, download the result and put it in that SAME folder.
echo       Keep the name Galaxy gives it (e.g. "Galaxy5-[Processed Files].zip").
echo    4. Run this .bat again (same choices): it adds SegmentAnyTree to the reports.
echo ============================================================================
start "" "https://usegalaxy.eu"

REM ---- 2. classical models + report, one dataset at a time ----
:models
for %%D in (%SEL%) do call :run %%D || goto failed
echo.
echo ============================================================================
for %%D in (%SEL%) do call :show %%D
echo ============================================================================
if defined NEEDSAT echo When Galaxy is done, put each result in its mosaic folder and run this .bat again with the same choices.
pause
exit /b 0

:failed
echo.
echo Something went wrong. Send a screenshot of this window to Ryan.
pause
exit /b 1


REM =============================== subroutines ===============================

:vars
REM sets NAME, PLOTS, MOS, OUT, TITLE for dataset %1 (v2, syn or ced)
if /i "%~1"=="v2" (
  set "NAME=FOR-instanceV2"
  set "PLOTS=%V2%\plots_100"
  set "MOS=..\data\mosaic_v2_100"
  set "OUT=results\benchmark_v2_100"
)
if /i "%~1"=="syn" (
  set "NAME=Synthetic Forest"
  set "PLOTS=%SYN%\plots_100"
  set "MOS=..\data\mosaic_syn_100"
  set "OUT=results\benchmark_syn_100"
)
if /i "%~1"=="ced" (
  set "NAME=CedarCypress3D"
  set "PLOTS=%CED%\plots_100"
  set "MOS=..\data\mosaic_ced_100"
  set "OUT=results\benchmark_ced_100"
)
set "TITLE=Tree segmentation evaluation: %NAME% at 100 pts/m2"
exit /b 0

:mosaic
call :vars %1
if exist "%MOS%\index.csv" goto mosaic_ok
echo Putting all %NAME% plots in one file for usegalaxy.eu...
%PY% mosaic_labelled.py build "%PLOTS%\*.las" --out "%MOS%" || exit /b 1
:mosaic_ok
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 goto mosaic_nosat
echo   %NAME%: SegmentAnyTree result found, it will be in the report.
exit /b 0
:mosaic_nosat
set "NEEDSAT=1"
echo   %NAME%: SegmentAnyTree not run yet. Upload from: %CD%\%MOS%
start "" "%MOS%"
exit /b 0

:run
call :vars %1
set "EXTRA="
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 goto run_models
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result.laz" --out "%MOS%\sat_by_plot" >nul || exit /b 1
set "EXTRA=--external sat=%MOS%\sat_by_plot"
:run_models
echo.
echo ===== %NAME%: running %MODELS% ... results go to %CD%\%OUT%
%PY% benchmark_folder.py "%PLOTS%" --out "%OUT%" --models %MODELS% %EXTRA% --title "%TITLE%" || exit /b 1
if defined EXTRA exit /b 0
REM Galaxy finished while the models were running? Add it now.
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 exit /b 0
echo   %NAME%: SegmentAnyTree result found, adding it to the report...
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result.laz" --out "%MOS%\sat_by_plot" >nul || exit /b 1
%PY% benchmark_folder.py "%PLOTS%" --out "%OUT%" --models %MODELS% --external sat=%MOS%\sat_by_plot --title "%TITLE%" || exit /b 1
exit /b 0

:show
call :vars %1
if not exist "%OUT%\report.html" exit /b 0
echo   %NAME%: %CD%\%OUT%\report.html
start "" "%OUT%\report.html"
exit /b 0

:download
REM %1 = file, %2 = URL, %3 = size in bytes on Zenodo. Downloads to <file>.part and renames it only when the size is
REM right, so a cut download is never mistaken for a finished one (an older cut file is moved back to .part and resumed).
REM If Zenodo drops the connection, waits and resumes where it stopped (up to 20 tries).
set "SZ="
if exist "%~1" for %%F in ("%~1") do set "SZ=%%~zF"
if "%SZ%"=="%~3" exit /b 0
if defined SZ echo   %~nx1 is incomplete ^(%SZ% of %~3 bytes^), resuming the download...
if defined SZ if not exist "%~1.part" move /y "%~1" "%~1.part" >nul
if defined SZ if exist "%~1" del "%~1"
set "TRY=0"
:download_try
set /a TRY+=1
curl.exe -L --fail -C - -o "%~1.part" "%~2"
set "SZ="
for %%F in ("%~1.part") do set "SZ=%%~zF"
if "%SZ%"=="%~3" goto download_ok
if %TRY% GEQ 20 goto download_fail
echo   Connection dropped, resuming in 15 seconds ^(try %TRY% of 20^)...
timeout /t 15 /nobreak >nul
goto download_try
:download_fail
echo Download of %~nx1 failed 20 times. Check the internet connection and run this .bat again: it resumes.
exit /b 1
:download_ok
move /y "%~1.part" "%~1" >nul || exit /b 1
exit /b 0

:prep_v2
if exist "%V2%\plots_100\*.las" exit /b 0
if exist "%V2%\test_data\*.ply" goto v2_unzipped
if exist "%V2%\test_data.zip" goto v2_unzip
echo Downloading the FOR-instanceV2 test plots (0.4 GB)...
mkdir "%V2%" 2>nul
call :download "%V2%\test_data.zip" "https://zenodo.org/api/records/16742708/files/test_data.zip/content" 377105598 || exit /b 1
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
echo Synthetic Forest: 3 scenes of about 3 GB each (skipped when already complete)...
call :download "%SYN%\WoodLand.pcd" "https://zenodo.org/api/records/17568131/files/WoodLand.pcd/content" 2519321570 || exit /b 1
call :download "%SYN%\OpenForest.pcd" "https://zenodo.org/api/records/17568131/files/OpenForest.pcd/content" 2859047883 || exit /b 1
call :download "%SYN%\ClosedForest.pcd" "https://zenodo.org/api/records/17568131/files/ClosedForest.pcd/content" 3180388832 || exit /b 1
echo Preparing the 3 synthetic scenes at 100 pts/m2 (40 million lines each, a few minutes per scene)...
%PY% prep_labelled.py "%SYN%\*.pcd" --out "%SYN%\plots_100" --density 100 || exit /b 1
exit /b 0

:prep_ced
REM rgb_kept.txt marks plots prepared with their true colour (since 29/10/2026); older ones are prepared again
REM (same points, so finished model runs and Galaxy results stay valid).
if exist "%CED%\plots_100\rgb_kept.txt" exit /b 0
if exist "%CED%\Kokonoe\plot_1\Kokonoe_ULS_plot-1.las" goto ced_unzipped
if exist "%CED%\CedarCypress3D_data.zip" goto ced_unzip
echo Downloading CedarCypress3D (3 GB)...
mkdir "%CED%" 2>nul
call :download "%CED%\CedarCypress3D_data.zip" "https://zenodo.org/api/records/22168721/files/CedarCypress3D_data.zip/content" 3002103950 || exit /b 1
:ced_unzip
echo Unzipping the drone point clouds and field data (the terrestrial scans and photos are skipped)...
%PY% -c "import sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); [z.extract(n, sys.argv[2]) for n in z.namelist() if '_ULS_' in n or n.endswith(('.csv', '.xlsx', 'README.txt'))]" "%CED%\CedarCypress3D_data.zip" "%CED%" || exit /b 1
:ced_unzipped
echo Preparing 34 labelled plots at 100 pts/m2...
%PY% prep_labelled.py "%CED%\*\plot_*\*_ULS_*.las" --out "%CED%\plots_100" --density 100 || exit /b 1
echo prepared with true colour > "%CED%\plots_100\rgb_kept.txt"
exit /b 0
