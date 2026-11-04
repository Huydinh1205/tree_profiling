@echo off
setlocal
cd /d "%~dp0"
title Tree segmentation benchmark
REM Benchmark on labelled data. One report PER DATASET and PER DENSITY:
REM   FOR-instanceV2   -> results\benchmark_v2_full\report.html   (original density; _100 = thinned to 100 pts/m2)
REM   Synthetic Forest -> results\benchmark_syn_full\report.html
REM   CedarCypress3D   -> results\benchmark_ced_full\report.html
REM Each density has its own plots (plots_full / plots_100) and mosaics (mosaic_*_full / mosaic_*_100).
REM Run 1: prepares the data, makes one file per dataset for SegmentAnyTree on usegalaxy.eu, runs the classical models here.
REM Run 2 (same choices, after you put the Galaxy results in the mosaic folders): adds SegmentAnyTree to the reports.
REM Or, with Docker Desktop + the NVIDIA GPU on this computer: model choice S (SegmentAnyTree only) or A (all) runs
REM SegmentAnyTree here (sat_docker.py) on each mosaic, so Galaxy is not needed. Results go to the same folders.
REM Every SegmentAnyTree result (Docker or Galaxy) is also merged with merge_sat.py (broad crowns cut into pieces are
REM joined again); the report scores both: "SegmentAnyTree v1" (raw) and "SegmentAnyTree v1 + merge_sat".

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
echo Point density?
echo   Enter = original density of each dataset, nothing thinned (slower; Synthetic Forest is very big)
echo   1     = thinned to 100 pts/m2 like our cloud0 (the earlier runs)
set "DENS="
set /p "DENS=Choice: "
set "DTAG=full"
set "DARG="
set "DLABEL=original density"
if "%DENS%"=="1" set "DTAG=100"
if "%DENS%"=="1" set "DARG=--density 100"
if "%DENS%"=="1" set "DLABEL=100 pts/m2"
echo.
echo Which models?
echo   Enter = 3 classical models (CHM watershed, Dalponte, li2012; li2012 is slow)
echo   F     = fast: CHM watershed + Dalponte only
echo   S     = SegmentAnyTree only, run with Docker on this computer (open Docker Desktop first)
echo   A     = all: the 3 classical models + SegmentAnyTree with Docker
echo   (every SegmentAnyTree result is also merged with merge_sat.py; the report shows raw and merged)
echo   (plots, models and SegmentAnyTree results already done are skipped, so a second run only adds what is new)
set "MODE="
set /p "MODE=Choice: "
set "RUNSAT="
set "MODELS=chm_watershed dalponte_py li2012_py"
if /i "%MODE%"=="F" set "MODELS=chm_watershed dalponte_py"
if /i "%MODE%"=="S" set "MODELS="
if /i "%MODE%"=="S" set "RUNSAT=1"
if /i "%MODE%"=="A" set "RUNSAT=1"
if not "%DTAG%"=="full" goto models_ok
if not "%MODELS%"=="chm_watershed dalponte_py li2012_py" goto models_ok
echo   li2012 is left out at original density: on these point counts it would take days.
set "MODELS=chm_watershed dalponte_py"
:models_ok
set "MLABEL=%MODELS%"
if not defined MODELS set "MLABEL=SegmentAnyTree scoring only"

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
  set "PLOTS=%V2%\plots_%DTAG%"
  set "MOS=..\data\mosaic_v2_%DTAG%"
  set "OUT=results\benchmark_v2_%DTAG%"
)
if /i "%~1"=="syn" (
  set "NAME=Synthetic Forest"
  set "PLOTS=%SYN%\plots_%DTAG%"
  set "MOS=..\data\mosaic_syn_%DTAG%"
  set "OUT=results\benchmark_syn_%DTAG%"
)
if /i "%~1"=="ced" (
  set "NAME=CedarCypress3D"
  set "PLOTS=%CED%\plots_%DTAG%"
  set "MOS=..\data\mosaic_ced_%DTAG%"
  set "OUT=results\benchmark_ced_%DTAG%"
)
set "TITLE=Tree segmentation evaluation: %NAME% at %DLABEL%"
exit /b 0

:mosaic
call :vars %1
if exist "%MOS%\index.csv" goto mosaic_ok
echo Putting all %NAME% plots in one file for SegmentAnyTree...
%PY% mosaic_labelled.py build "%PLOTS%\*.las" --out "%MOS%" || exit /b 1
:mosaic_ok
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 goto mosaic_nosat
echo   %NAME%: SegmentAnyTree result found, it will be in the report.
exit /b 0
:mosaic_nosat
if defined RUNSAT goto mosaic_docker
set "NEEDSAT=1"
echo   %NAME%: SegmentAnyTree not run yet. Upload from: %CD%\%MOS%
start "" "%MOS%"
exit /b 0
:mosaic_docker
echo.
echo ===== %NAME%: running SegmentAnyTree with Docker on this computer (minutes to hours, keep this window open) ...
REM --index: a mosaic of more than 20 million points (CedarCypress3D / Synthetic Forest at original density) runs in
REM groups of whole plots, otherwise Windows kills SegmentAnyTree for lack of RAM (SIGKILL). Finished groups are
REM kept in sat_result_parts\, so running the .bat again resumes where it stopped.
%PY% sat_docker.py "%MOS%\mosaic_unlabelled.laz" --out "%MOS%\sat_result.laz" --log "%MOS%\sat_docker.log" --index "%MOS%\index.csv" || exit /b 1
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" || exit /b 1
echo   %NAME%: SegmentAnyTree done, it will be in the report.
exit /b 0

:run
call :vars %1
call :satextra || exit /b 1
:run_models
echo.
echo ===== %NAME%: running %MLABEL% ... results go to %CD%\%OUT%
%PY% benchmark_folder.py "%PLOTS%" --out "%OUT%" --models %MODELS% %EXTRA% --title "%TITLE%" || exit /b 1
if defined EXTRA exit /b 0
REM Galaxy finished while the models were running? Add it now.
call :satextra || exit /b 1
if not defined EXTRA exit /b 0
echo   %NAME%: SegmentAnyTree result found, adding it to the report...
%PY% benchmark_folder.py "%PLOTS%" --out "%OUT%" --models %MODELS% %EXTRA% --title "%TITLE%" || exit /b 1
exit /b 0

:satextra
REM If the mosaic has a SegmentAnyTree result: split it per plot (sat), merge its over-segmented pieces with
REM merge_sat.py (again only when the result is newer than the merged file) and split that too (sat_merged).
REM Sets EXTRA for benchmark_folder.py; EXTRA stays empty when there is no result yet.
set "EXTRA="
%PY% mosaic_labelled.py find "%MOS%\index.csv" --search "%MOS%" --out "%MOS%\sat_result.laz" >nul 2>&1
if errorlevel 1 exit /b 0
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result.laz" --out "%MOS%\sat_by_plot" >nul || exit /b 1
%PY% -c "import os, sys; a, b = sys.argv[1:]; sys.exit(0 if os.path.exists(b) and os.path.getmtime(b) >= os.path.getmtime(a) else 1)" "%MOS%\sat_result.laz" "%MOS%\sat_result_merged.laz" && goto satextra_split
echo   %NAME%: merging over-segmented SegmentAnyTree trees (merge_sat.py)...
%PY% merge_sat.py "%MOS%\sat_result.laz" || exit /b 1
:satextra_split
%PY% mosaic_labelled.py split "%MOS%\index.csv" "%MOS%\sat_result_merged.laz" --out "%MOS%\sat_merged_by_plot" >nul || exit /b 1
set "EXTRA=--external sat=%MOS%\sat_by_plot sat_merged=%MOS%\sat_merged_by_plot"
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
if exist "%V2%\plots_%DTAG%\*.las" exit /b 0
if exist "%V2%\test_data\*.ply" goto v2_unzipped
if exist "%V2%\test_data.zip" goto v2_unzip
echo Downloading the FOR-instanceV2 test plots (0.4 GB)...
mkdir "%V2%" 2>nul
call :download "%V2%\test_data.zip" "https://zenodo.org/api/records/16742708/files/test_data.zip/content" 377105598 || exit /b 1
:v2_unzip
echo Unzipping...
powershell -NoProfile -Command "Expand-Archive -Force '%V2%\test_data.zip' '%V2%'" || exit /b 1
:v2_unzipped
echo Preparing 29 labelled plots at %DLABEL%...
%PY% prep_labelled.py "%V2%\test_data\*.ply" --out "%V2%\plots_%DTAG%" %DARG% || exit /b 1
exit /b 0

:prep_syn
if exist "%SYN%\plots_%DTAG%\*.las" exit /b 0
mkdir "%SYN%" 2>nul
echo Synthetic Forest: 3 scenes of about 3 GB each (skipped when already complete)...
call :download "%SYN%\WoodLand.pcd" "https://zenodo.org/api/records/17568131/files/WoodLand.pcd/content" 2519321570 || exit /b 1
call :download "%SYN%\OpenForest.pcd" "https://zenodo.org/api/records/17568131/files/OpenForest.pcd/content" 2859047883 || exit /b 1
call :download "%SYN%\ClosedForest.pcd" "https://zenodo.org/api/records/17568131/files/ClosedForest.pcd/content" 3180388832 || exit /b 1
echo Preparing the 3 synthetic scenes at %DLABEL% (40 million lines each, a few minutes per scene)...
%PY% prep_labelled.py "%SYN%\*.pcd" --out "%SYN%\plots_%DTAG%" %DARG% || exit /b 1
exit /b 0

:prep_ced
REM rgb_kept.txt marks plots prepared with their true colour (since 29/10/2026); older ones are prepared again
REM (same points, so finished model runs and Galaxy results stay valid).
if exist "%CED%\plots_%DTAG%\rgb_kept.txt" exit /b 0
if exist "%CED%\Kokonoe\plot_1\Kokonoe_ULS_plot-1.las" goto ced_unzipped
if exist "%CED%\CedarCypress3D_data.zip" goto ced_unzip
echo Downloading CedarCypress3D (3 GB)...
mkdir "%CED%" 2>nul
call :download "%CED%\CedarCypress3D_data.zip" "https://zenodo.org/api/records/22168721/files/CedarCypress3D_data.zip/content" 3002103950 || exit /b 1
:ced_unzip
echo Unzipping the drone point clouds and field data (the terrestrial scans and photos are skipped)...
%PY% -c "import sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); [z.extract(n, sys.argv[2]) for n in z.namelist() if '_ULS_' in n or n.endswith(('.csv', '.xlsx', 'README.txt'))]" "%CED%\CedarCypress3D_data.zip" "%CED%" || exit /b 1
:ced_unzipped
echo Preparing 34 labelled plots at %DLABEL%...
%PY% prep_labelled.py "%CED%\*\plot_*\*_ULS_*.las" --out "%CED%\plots_%DTAG%" %DARG% || exit /b 1
echo prepared with true colour > "%CED%\plots_%DTAG%\rgb_kept.txt"
exit /b 0
