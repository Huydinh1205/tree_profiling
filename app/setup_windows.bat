@echo off
setlocal
cd /d "%~dp0"
title Tree Profiling: set up
REM Run this once on the processing computer (and again after moving the folder). It makes a private
REM Python environment for the app in app\.venv, checks the app, then makes the Desktop and Start menu
REM shortcuts and lets a double-clicked .treeprof file open in the app. No administrator rights needed.
REM The processing scripts keep using the computer's Python 3.10, as the .bat files in processes do.

set "PY="
py -3.10 -c "import sys" >nul 2>&1 && set "PY=py -3.10"
if not defined PY py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if not defined PY py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
  echo Python 3.10, 3.11 or 3.12 was not found. Ask Ryan to check the Python install on this machine.
  pause
  exit /b 1
)
echo Using %PY%
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Checking the app (about a minute)...
".venv\Scripts\python.exe" -m unittest discover -s tests -t .
if errorlevel 1 goto failed
echo.
".venv\Scripts\python.exe" -m treeprof_app --install
if errorlevel 1 echo Some of the shortcuts could not be made. The app still works: start it with "Tree Profiling.bat".
echo.
echo Done. Start the app with the "Tree Profiling" icon on the Desktop (or "Tree Profiling.bat" in this folder).
pause
exit /b 0

:failed
echo.
echo Something went wrong. Send a screenshot of this window to Ryan.
pause
exit /b 1
