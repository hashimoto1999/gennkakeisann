@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv >nul 2>&1
  if errorlevel 1 python -m venv .venv
  if errorlevel 1 (
    echo Python 3.10 or later was not found. Install Python, then try again.
    pause
    exit /b 1
  )
)

.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check your internet connection and try again.
  pause
  exit /b 1
)
for /f "tokens=1" %%H in ('certutil -hashfile requirements.txt SHA256 ^| findstr /R /V "hash CertUtil"') do >".venv\.requirements-hash" echo %%H
echo Setup complete. Double-click run_windows.bat to start the app.
pause
