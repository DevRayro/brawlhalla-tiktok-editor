@echo off
REM Windows setup for the Brawlhalla TikTok auto-editor.
REM Detects NVIDIA GPU generation and installs the matching PyTorch wheel,
REM then SAM2 with --no-deps so it doesn't drag a different torch in.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==^> Checking system dependencies
where ffmpeg >nul 2>&1 || (
    echo ERROR: ffmpeg not found. Install it ^( https://www.gyan.dev/ffmpeg/builds/ ^) and add to PATH.
    pause & exit /b 1
)
where node >nul 2>&1 || (
    echo ERROR: Node.js 20+ not found. Install from https://nodejs.org/
    pause & exit /b 1
)

REM ----------------------------------------------------------------
REM Pick a compatible Python: 3.12, 3.11, then 3.10. We deliberately
REM avoid 3.13+ because numpy 1.26.4 / opencv 4.10 don't ship Windows
REM wheels for it yet, and pip would try to compile from source (which
REM fails without Visual Studio C++).
REM ----------------------------------------------------------------
set "PYEXE="

where py >nul 2>&1
if %errorlevel% equ 0 (
    for %%V in (3.12 3.11 3.10) do (
        if not defined PYEXE (
            py -%%V --version >nul 2>&1 && set "PYEXE=py -%%V"
        )
    )
)

REM Fall back to the bare `python` only if it's a supported version.
if not defined PYEXE (
    where python >nul 2>&1 && (
        for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
        for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
            set "PYMAJ=%%a"
            set "PYMIN=%%b"
        )
        if "!PYMAJ!"=="3" (
            if !PYMIN! geq 10 if !PYMIN! leq 12 set "PYEXE=python"
        )
    )
)

REM No compatible Python found — try to install 3.12 via winget.
if not defined PYEXE (
    echo.
    echo No compatible Python found ^(need 3.10, 3.11 or 3.12^).
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo ==^> Installing Python 3.12 via winget
        winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
        REM Refresh PATH so the freshly installed python launcher is visible.
        set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
        py -3.12 --version >nul 2>&1 && set "PYEXE=py -3.12"
    )
)

if not defined PYEXE (
    echo.
    echo ERROR: Python 3.10/3.11/3.12 is required.
    echo Install from https://www.python.org/downloads/release/python-3120/
    echo Make sure to tick "Add python.exe to PATH" during install.
    pause & exit /b 1
)

echo     Using Python: %PYEXE%
%PYEXE% --version

echo ==^> Creating Python venv
if not exist .venv (
    %PYEXE% -m venv .venv
)
call .venv\Scripts\activate.bat

echo ==^> Installing base Python dependencies
python -m pip install --upgrade pip wheel >nul
pip install -r pipeline\requirements.txt
if errorlevel 1 ( echo pip install failed & pause & exit /b 1 )

REM ----- Torch installation tuned to the local hardware -----
set "TORCH_INDEX="
where nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    REM NVIDIA detected. Read the GPU name to pick the right CUDA toolkit
    REM build. Blackwell (RTX 50xx) needs cu128; older cards work on cu121.
    for /f "usebackq delims=" %%i in (`nvidia-smi --query-gpu=name --format=csv,noheader 2^>nul`) do set "GPUNAME=%%i"
    echo     GPU: !GPUNAME!
    echo !GPUNAME! | findstr /I "RTX 50" >nul
    if !errorlevel! equ 0 (
        set "TORCH_INDEX=https://download.pytorch.org/whl/cu128"
        echo ==^> Blackwell detected, installing CUDA 12.8 build of torch
    ) else (
        set "TORCH_INDEX=https://download.pytorch.org/whl/cu121"
        echo ==^> NVIDIA detected, installing CUDA 12.1 build of torch
    )
) else (
    echo ==^> No NVIDIA GPU detected, installing CPU build of torch
    set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
)

pip install --upgrade torch torchvision --index-url %TORCH_INDEX%
if errorlevel 1 ( echo torch install failed & pause & exit /b 1 )

REM ----- SAM 2 (no-deps so it doesn't reinstall a different torch) -----
echo ==^> Installing SAM 2
pip install --no-deps "SAM-2 @ git+https://github.com/facebookresearch/sam2.git"
if errorlevel 1 ( echo SAM-2 install failed ^(non-fatal, tracker will fall back^) )

echo ==^> Downloading SAM2 checkpoint ^(~180MB^)
if not exist models mkdir models
if not exist models\sam2.1_hiera_small.pt (
    curl -L -o models\sam2.1_hiera_small.pt https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt
)

echo ==^> Installing Remotion dependencies
pushd remotion
call npm install --no-audit --no-fund
if errorlevel 1 ( echo npm install failed & popd & pause & exit /b 1 )
popd

echo.
echo ==^> Setup complete. Run start.bat to launch the editor.
endlocal
