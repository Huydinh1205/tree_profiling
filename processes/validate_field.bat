@echo off
setlocal
cd /d "%~dp0"
title Check tree measurements against field data
REM Double-click this file. It checks how close the tree measurements (height, crown diameter, crown area,
REM crown base) are to trees measured in the field, on the public dataset of Weiser et al. 2022
REM (https://doi.org/10.1594/PANGAEA.942856). No SegmentAnyTree, Docker or graphics card is needed: the trees
REM in that dataset are already cut out one by one, so only the measuring is checked.
REM
REM The first run downloads about 1 GB (only the single-tree drone point clouds of the 6 plots with field
REM measurements) into ..\data\Weiser2022. Later runs only download what is missing.
REM
REM From a command window you can give plots, e.g.   validate_field.bat KA10        (14 trees, quick try)
REM                                                  validate_field.bat --no-download

set "PY="
py -3.10 -c "import sys" >nul 2>&1 && set "PY=py -3.10"
if defined PY goto havepy
python -c "import laspy" >nul 2>&1 && set "PY=python"
if defined PY goto havepy
echo Python was not found. Ask Ryan to check the Python install on this machine.
pause
exit /b 1
:havepy

echo Checking tree measurements against field data (Weiser et al. 2022).
echo The first run downloads about 1 GB, this can take several minutes.
echo.
%PY% validate_field.py %*
if errorlevel 1 goto failed

echo.
echo Done. One row per tree is in data\Weiser2022\validation.csv
if exist "..\data\Weiser2022\validation.csv" explorer /select,"%~dp0..\data\Weiser2022\validation.csv"
pause
exit /b 0

:failed
echo.
echo Something went wrong (see the message above). If it is a download error, check the internet
echo connection and run this file again: files already downloaded are kept. Otherwise send a
echo screenshot of this window to Ryan.
pause
exit /b 1
