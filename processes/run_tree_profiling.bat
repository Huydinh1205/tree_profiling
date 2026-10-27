@echo off
setlocal
cd /d "%~dp0"
title Tree profiling

REM ---- 1. One-time setup (Python 3.11 + libraries) ----
if exist ".venv\Scripts\python.exe" goto ready
echo First run: setting up Python and libraries. This takes a few minutes and only happens once.
py -3.11 -m venv .venv
if errorlevel 1 (
  echo.
  echo Python 3.11 was not found. Install Python 3.11 from python.org, then run this again.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installing the libraries failed. Send a screenshot of this window to Ryan.
  pause
  exit /b 1
)
:ready

REM ---- 2. Which file? (dragged onto this .bat, or pasted) ----
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

REM ---- 3. Whole file or a test square? ----
echo.
echo Press Enter to process the whole file.
echo For a quick test on a big file, type a size in metres (for example 200) to use only a square in the middle.
set "CROP="
set /p "CROP=Size (Enter = whole file): "
set "OUT=results\%NAME%"
set "CROPARG="
if defined CROP (
  set "OUT=results\%NAME%_crop%CROP%m"
  set "CROPARG=--crop-size %CROP%"
)

REM ---- 4. Run ----
echo.
echo Working on %NAME% ... results will be in %CD%\%OUT%
.venv\Scripts\python.exe process2_segment.py "%LAS%" --out "%OUT%" --site-name "%NAME%" --models chm_watershed dalponte_py li2012_py %CROPARG%
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
