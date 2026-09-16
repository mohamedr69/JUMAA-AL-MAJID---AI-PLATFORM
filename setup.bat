@echo off
rem One-time setup of the Engineering Project Platform on a new Windows PC.
rem Needs Python 3.12 and Node.js on the PATH. Safe to run again: what is
rem already there is kept (an existing backend\.env is never overwritten).
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo Python 3.12 is not installed or not on PATH: https://www.python.org/downloads/ & exit /b 1)
where npm >nul 2>nul || (echo Node.js is not installed or not on PATH: https://nodejs.org/ & exit /b 1)

rem Windows' 260-character path limit: the company library's deepest datasheet
rem path is 166 characters before the clone folder is added, and a pull that
rem adds one stops with "Filename too long" without this. (The first clone
rem comes before this script; the README asks for the same setting there.)
where git >nul 2>nul && git config --global core.longpaths true

echo === Backend: Python environment and packages
cd backend
if not exist venv (
    python -m venv venv || exit /b 1
)
rem The company library's deepest datasheet path is 166 characters; past a
rem 90-character clone folder it crosses Windows' 260 limit and Python cannot
rem open it, so those datasheets drop out of the library. Warn, do not stop.
venv\Scripts\python -c "import os,sys; sys.exit(len(os.path.dirname(os.getcwd())) > 90)" || echo WARNING: this folder's path is long; move the clone to a short folder such as C:\dev or the deepest datasheets will not be found.
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
