@echo off
setlocal
cd /d "%~dp0"
title Bursar Cashbook

if not exist ".venv\Scripts\python.exe" (
  echo Cashbook setup has not been completed.
  echo Please run SETUP CASHBOOK.bat first.
  pause
  exit /b 1
)

if not exist "cashbook.db" (
  echo The Cashbook database is missing.
  echo Please contact support before continuing.
  pause
  exit /b 1
)

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"

echo ========================================
echo          BURSAR CASHBOOK
echo ========================================
echo.
echo The Cashbook is running locally.
echo Keep this window open while you work.
echo Close it or press Ctrl+C when finished.
echo.

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

if errorlevel 1 (
  echo.
  echo The Cashbook stopped unexpectedly.
  echo Please send a screenshot of this window and a support bundle to the developer.
  pause
)
