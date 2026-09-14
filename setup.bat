@echo off
rem One-time setup of the Engineering Project Platform on a new Windows PC.
rem Needs Python 3.12 and Node.js on the PATH. Safe to run again: what is
rem already there is kept (an existing backend\.env is never overwritten).
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo Python 3.12 is not installed or not on PATH: https://www.python.org/downloads/ & exit /b 1)
where npm >nul 2>nul || (echo Node.js is not installed or not on PATH: https://nodejs.org/ & exit /b 1)

echo === Backend: Python environment and packages
cd backend
if not exist venv (
    python -m venv venv || exit /b 1
)
venv\Scripts\python -m pip install --upgrade pip >nul
venv\Scripts\pip install -r requirements.txt || exit /b 1

if not exist .env (
    echo === Backend: creating .env with a new secret key
    copy /y .env.example .env >nul
    for /f %%k in ('venv\Scripts\python -c "import secrets; print(secrets.token_hex(32))"') do (
        venv\Scripts\python -c "import pathlib; p=pathlib.Path('.env'); p.write_text(p.read_text(encoding='utf-8').replace('SECRET_KEY=\n', 'SECRET_KEY=%%k\n', 1), encoding='utf-8')"
    )
) else (
    echo === Backend: keeping the existing .env
)
cd ..

echo === Frontend: packages
cd frontend
call npm install || exit /b 1
cd ..

echo.
echo Setup finished.
where tesseract >nul 2>nul || if not exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" echo NOTE: Tesseract OCR is not installed; reading scanned DRFs and Design Sheets needs it: https://github.com/UB-Mannheim/tesseract/wiki
where claude >nul 2>nul || echo NOTE: Claude Code is not installed; the AI features need it, signed in once with "claude": https://claude.com/claude-code
echo Run start.bat to start the platform.
endlocal
