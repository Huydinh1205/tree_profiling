@echo off
setlocal
cd /d "%~dp0"
title Colour each tree
REM Drag one or more segmentation results (e.g. the "Galaxy14-[Processed Files].zip" from Galaxy, any .las/.laz with
REM PredInstance_SAT or treeID) onto this file. For each one it writes <name>_colour.laz next to it: every tree in its own
REM colour, ground and unassigned points grey. Open that file in CloudCompare, QGIS or DJI Terra: no settings needed.

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
echo Colouring trees in: %~nx1
%PY% mosaic_labelled.py colour "%~f1"
if errorlevel 1 goto failed
set "LAST=%~dpn1_colour.laz"
shift
goto next

:done
echo.
echo Done. The coloured file is next to the original (name ends in _colour.laz).
explorer /select,"%LAST%"
pause
exit /b 0

:noargs
echo Drag a segmentation result file (.laz, .las, or the Galaxy "Processed Files" .zip) onto colour_trees.bat.
pause
exit /b 0

:failed
echo.
echo This file has no tree numbers (no PredInstance_SAT or treeID field), or it could not be read.
echo Check that it is the "Processed Files" result from Galaxy. Send a screenshot of this window to Ryan.
pause
exit /b 1
