@echo off
REM Windows launcher — double-click in File Explorer.
REM Three-stage flow:
REM   1. Install prerequisites via winget (ffmpeg, Python 3.11, Node 20).
REM   2. Show first-run GUI that runs setup.bat with progress.
REM   3. Launch the actual server.
setlocal
cd /d "%~dp0"

REM ---- Stage 1: prerequisites (only if any are missing) ----
where python >nul 2>&1
set NEED_PREREQS=%errorlevel%
where node >nul 2>&1
if errorlevel 1 set NEED_PREREQS=1
where ffmpeg >nul 2>&1
if errorlevel 1 set NEED_PREREQS=1

if "%NEED_PREREQS%"=="1" (
    echo Installing system prerequisites ^(ffmpeg, Python 3.11, Node 20^) via winget...
    powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0installers\windows\install-prereqs.ps1"
    if errorlevel 1 (
        echo Prerequisite install failed.
        pause
        exit /b 1
    )
    REM Refresh PATH for this cmd session.
    for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path') do set "MACHINE_PATH=%%B"
    for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%B"
    set "PATH=%MACHINE_PATH%;%USER_PATH%"
)

REM ---- Stage 2: first-run setup GUI (skipped if .install-complete exists) ----
if not exist ".install-complete" (
    python "%~dp0installers\shared\first_run_gui.py"
    if errorlevel 1 (
        echo First-run setup did not complete.
        pause
        exit /b 1
    )
)

REM ---- Stage 3: regular launch ----
if not exist ".venv\Scripts\python.exe" (
    echo Internal error: .install-complete exists but .venv is missing.
    echo Delete .install-complete and re-run this launcher.
    pause
    exit /b 1
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
