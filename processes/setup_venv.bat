@echo off
REM One-time setup: venv with Python 3.11 + libraries. Run from this folder.
cd /d "%~dp0"
py -3.11 -m venv .venv
if errorlevel 1 (
  echo Python 3.11 not found. Run "py -0p" to list installed Pythons.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo Done. Run scripts with: .venv\Scripts\python.exe process2_segment.py ...
pause
