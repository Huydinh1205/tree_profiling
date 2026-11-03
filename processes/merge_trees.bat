@echo off
setlocal
cd /d "%~dp0"
title Merge over-segmented trees
REM Drag one or more SegmentAnyTree results (e.g. "Galaxy16-[Processed Files].laz" or the .zip from Galaxy) onto this
REM file. SegmentAnyTree often cuts one broad crown into several pieces; this merges the pieces back into one tree.
REM For each input it writes next to it:
REM   <name>_merged.laz          same points, PredInstance_SAT = merged trees (original kept as PredInstance_SAT_raw)
REM   <name>_merged_colour.laz   one colour per merged tree, open in CloudCompare / QGIS / DJI Terra
REM   <name>_merged.png          before / after top view
REM   <name>_merged_log.csv      every merge and why

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
:next
if "%~1"=="" goto done
echo.
echo Merging tree pieces in: %~nx1
%PY% merge_sat.py "%~f1"
if errorlevel 1 goto failed
set "LAST=%~dpn1_merged.png"
shift
goto next

:done
echo.
echo Done. Results are next to the original (names end in _merged).
explorer /select,"%LAST%"
pause
exit /b 0

:noargs
echo Drag a SegmentAnyTree result (.laz, .las, or the Galaxy "Processed Files" .zip) onto merge_trees.bat.
pause
exit /b 0

:failed
echo.
echo Something went wrong (no PredInstance_SAT / treeID field, or the file could not be read).
echo Send a screenshot of this window to Ryan.
pause
exit /b 1
