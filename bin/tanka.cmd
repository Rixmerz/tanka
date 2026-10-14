@echo off
rem Tanka launcher for Windows (PowerShell and cmd): runs tanka.py, next to this file, with Python 3.
rem TANKA_PYTHON picks the interpreter; otherwise the py launcher, then python on PATH.
setlocal
if defined TANKA_PYTHON (
  "%TANKA_PYTHON%" "%~dp0tanka.py" %*
  exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0tanka.py" %*
  exit /b
)
python "%~dp0tanka.py" %*
