@echo off
setlocal
cd /d "%~dp0"
title Bursar Cashbook Setup

echo ========================================
echo        BURSAR CASHBOOK SETUP
echo ========================================
echo.

set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>nul && set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo Python 3 is not installed or is not available in PATH.
  echo Please contact support.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the local Python environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing Cashbook requirements...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

if not exist "cashbook.db" (
  if exist "data\2020_cashbook.xls" (
    echo Creating the initial Cashbook database...
    ".venv\Scripts\python.exe" seed.py "data\2020_cashbook.xls"
    if errorlevel 1 goto :failed
  ) else (
    echo.
    echo No existing cashbook.db or seed workbook was found.
    echo Contact support before processing real statements.
  )
)

if not exist "config" mkdir config
if not exist "backups" mkdir backups
if not exist "logs" mkdir logs

echo.
echo Setup completed successfully.
echo You can now double-click START CASHBOOK.bat.
pause
exit /b 0

:failed
echo.
echo Setup failed. Please send a screenshot of this window to support.
pause
exit /b 1
