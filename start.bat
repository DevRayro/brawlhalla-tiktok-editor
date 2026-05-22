@echo off
REM Windows launcher — double-click in File Explorer.
setlocal
cd /d "%~dp0"

REM First-run setup (idempotent).
if not exist ".venv\Scripts\python.exe" (
    call setup.bat
    if errorlevel 1 (
        echo Setup failed.
        pause
        exit /b 1
    )
)
if not exist "remotion\node_modules" (
    pushd remotion
    call npm install --no-audit --no-fund
    popd
)

call .venv\Scripts\activate.bat
python deploy\local\server.py
endlocal
pause
