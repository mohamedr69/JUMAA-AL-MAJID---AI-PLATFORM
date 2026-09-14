@echo off
rem Start the Engineering Project Platform: the API on http://localhost:8000
rem and the web app on http://localhost:5173, each in its own window.
rem Run setup.bat once first on a new PC.
setlocal
cd /d "%~dp0"

if not exist backend\venv (
    echo The platform is not set up on this PC yet: run setup.bat first.
    exit /b 1
)

start "EP Platform - API" cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m uvicorn app.main:app --reload --reload-dir app --port 8000"
start "EP Platform - Web" cmd /k "cd /d "%~dp0frontend" && npm run dev"

timeout /t 8 >nul
start "" http://localhost:5173
endlocal
