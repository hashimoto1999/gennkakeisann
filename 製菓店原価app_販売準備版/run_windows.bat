@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: creating a private Python environment...
  py -3 -m venv .venv >nul 2>&1
  if errorlevel 1 (
    python -m venv .venv
    if errorlevel 1 (
      echo Python 3.10 or later was not found. Install Python, then run this file again.
      pause
      exit /b 1
    )
  )
)

set "REQ_HASH="
for /f "tokens=1" %%H in ('certutil -hashfile requirements.txt SHA256 ^| findstr /R /V "hash CertUtil"') do set "REQ_HASH=%%H"
set "OLD_HASH="
if exist ".venv\.requirements-hash" set /p OLD_HASH=<".venv\.requirements-hash"
.venv\Scripts\python.exe -c "import streamlit, pandas, pykakasi, platformdirs" >nul 2>&1
if errorlevel 1 set "OLD_HASH="

if not "%REQ_HASH%"=="%OLD_HASH%" (
  echo Installing or updating required packages...
  .venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo Package installation failed. Check your internet connection and try again.
    pause
    exit /b 1
  )
  >".venv\.requirements-hash" echo %REQ_HASH%
)

echo Starting the confectionery costing app...
.venv\Scripts\python.exe -m streamlit run app.py
if errorlevel 1 (
  echo The app stopped with an error. Review the message above.
  pause
)
