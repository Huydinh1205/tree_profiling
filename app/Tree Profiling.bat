@echo off
REM Starts the Tree Profiling app, or shows its window if it is already running.
REM Drag a .treeprof file onto this file to open saved results.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo The app is not set up yet. Double-click setup_windows.bat first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0launch.pyw" %*
