@echo off
setlocal
cd /d "%~dp0"
title Tree Profiling: update
REM Gets the newest version from GitHub (app and processing scripts are in the same repository).
REM   update_windows.bat            newest version
REM   update_windows.bat v0.2.0     a given version (also the way back to an older one)
REM The app must be closed (also its icon next to the clock). Runs and results on this computer are not touched; the app's records are
REM copied to C:\TreeProfiling\state_backups first.

if not exist ".venv\Scripts\python.exe" (
  echo The app is not set up yet. Double-click setup_windows.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m treeprof_app --check-closed
if errorlevel 1 (
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m treeprof_app --backup-state
if errorlevel 1 goto failed
echo.
echo Getting the new version...
if "%~1"=="" (
  git -C .. pull --ff-only
) else (
  git -C .. fetch --tags
  git -C .. checkout %1
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Checking the app (about a minute)...
".venv\Scripts\python.exe" -m unittest discover -s tests -t .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m treeprof_app --install
echo.
echo Done. Start the app as usual.
pause
exit /b 0

:failed
echo.
echo Something went wrong. Nothing was lost. Send a screenshot of this window to Ryan.
pause
exit /b 1
