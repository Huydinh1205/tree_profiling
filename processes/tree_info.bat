@echo off
setlocal
cd /d "%~dp0"
title Tree info for CloudCompare
REM Drag one or more segmentation results (SegmentAnyTree result, the Galaxy "Processed Files" .zip, points_treeid.laz,
REM any .las/.laz with PredInstance_SAT or treeID) onto this file. For each one it writes next to it:
REM   <name>_info.laz    open in CloudCompare: each tree in its own colour, click a tree with Point picking to see
REM                      its height, crown diameter, crown area, crown volume ... (scalar fields)
REM   <name>_trees.csv   one row per tree (Excel)
REM   <name>_crowns.shp  crown outlines (CloudCompare, QGIS)

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
echo Measuring trees in: %~nx1
%PY% tree_info.py "%~f1"
if errorlevel 1 goto failed
set "LAST=%~dpn1_info.laz"
shift
goto next

:done
echo.
echo Done. Open the file ending in _info.laz in CloudCompare (keep all extra fields ticked when it asks).
echo Then Tools ^> Point picking (or the "i" icon) and click a tree.
if exist "%LAST%" explorer /select,"%LAST%"
pause
exit /b 0

:noargs
echo Drag a segmentation result file (.laz, .las, or the Galaxy "Processed Files" .zip) onto tree_info.bat.
pause
exit /b 0

:failed
echo.
echo Something went wrong (see the message above). If it says "No tree ID field", the file is not a
echo segmentation result. Send a screenshot of this window to Ryan.
pause
exit /b 1
