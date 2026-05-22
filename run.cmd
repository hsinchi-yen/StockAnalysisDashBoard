@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=api"

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] .venv not found, creating virtual environment...
  py -3 -m venv .venv >nul 2>&1
  if errorlevel 1 (
    python -m venv .venv
    if errorlevel 1 goto :error
  )
)

set "PY=.venv\Scripts\python.exe"

"%PY%" -m pip --disable-pip-version-check show fastapi >nul 2>&1
if errorlevel 1 (
  echo [INFO] Installing dependencies from requirements.txt...
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 goto :error
)

if /I "%MODE%"=="api" (
  set "HOST=localhost"
  set "PORT=9000"

  echo [INFO] Starting FastAPI at http://!HOST!:!PORT!
  "%PY%" -m uvicorn api:app --host !HOST! --port !PORT!
  goto :eof
)

if /I "%MODE%"=="streamlit" (
  echo [INFO] Starting Streamlit at http://localhost:8501
  "%PY%" -m streamlit run app.py
  goto :eof
)

echo [ERROR] Unknown mode: %MODE%
echo Usage: run.cmd [api^|streamlit]
exit /b 1

:error
echo [ERROR] Failed to prepare environment.
exit /b 1
