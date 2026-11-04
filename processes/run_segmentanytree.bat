@echo off
setlocal
cd /d "%~dp0"
title SegmentAnyTree on this computer (full density)
REM Drag one or more of OUR point clouds (.las / .laz, e.g. a DJI Terra export) onto this file.
REM Each file is used at FULL density (nothing is thinned) and never leaves this computer:
REM   1. SegmentAnyTree v1 with Docker on this computer (Docker Desktop must be running)   -> sat_result.laz
REM   2. merge_sat.py joins crowns that SegmentAnyTree cut into pieces                      -> sat_result_merged.laz
REM      (+ sat_result_merged_colour.laz to view, _merged.png before/after, _merged_log.csv)
REM   3. process 2 report: CHM watershed + SegmentAnyTree raw + merged, tree tables, crown polygons -> report.html
REM Everything goes to processes\results\<file name>_sat\  (not to GitHub: results are git-ignored).
REM A file that already has sat_result.laz there skips step 1; delete sat_result.laz to run SegmentAnyTree again.

set "PY="
py -3.10 -c "import sys" >nul 2>&1 && set "PY=py -3.10"
if defined PY goto havepy
python -c "import laspy" >nul 2>&1 && set "PY=python"
if defined PY goto havepy
echo Python was not found. Ask Ryan to check the Python install on this machine.
pause
exit /b 1
:havepy
if "%~1"=="" goto noargs
set "LAST="

:next
if "%~1"=="" goto done
set "OUT=results\%~n1_sat"
if not exist "%OUT%" mkdir "%OUT%"
echo.
echo ===== %~nx1   (results go to %CD%\%OUT%)
if exist "%OUT%\sat_result.laz" goto have_sat
echo --- 1/3 SegmentAnyTree with Docker, full density (a big file can take hours, keep this window open)
%PY% sat_docker.py "%~f1" --out "%OUT%\sat_result.laz" --log "%OUT%\sat_docker.log"
if errorlevel 1 goto failed
:have_sat
echo --- 2/3 merging crowns cut into pieces (merge_sat.py)
%PY% merge_sat.py "%OUT%\sat_result.laz"
if errorlevel 1 goto failed
echo --- 3/3 report (process 2: CHM watershed + SegmentAnyTree raw + merged)
%PY% process2_segment.py "%~f1" --out "%OUT%" --models chm_watershed --external "sat=%OUT%\sat_result.laz" "sat_merged=%OUT%\sat_result_merged.laz" --site-name "%~n1"
if errorlevel 1 goto failed
set "LAST=%OUT%\report.html"
shift
goto next

:done
echo.
echo Done. Open report.html in each results folder; view the trees in sat_result_merged_colour.laz (CloudCompare).
if defined LAST start "" "%LAST%"
pause
exit /b 0

:noargs
echo Drag a point cloud of ours (.las or .laz, the ORIGINAL export, not a Galaxy result) onto run_segmentanytree.bat.
pause
exit /b 0

:failed
echo.
echo Something went wrong. The messages above say why (Docker Desktop not running, GPU, out of memory...).
echo SegmentAnyTree's full output is in %CD%\%OUT%\sat_docker.log. Send a screenshot of this window to Ryan.
pause
exit /b 1
