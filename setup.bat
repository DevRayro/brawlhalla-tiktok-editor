@echo off
REM Windows setup for the Brawlhalla TikTok auto-editor.
REM Detects NVIDIA GPU generation and installs the matching PyTorch wheel,
REM then SAM2 with --no-deps so it doesn't drag a different torch in.
setlocal
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

REM Prefer Python 3.11 if py launcher is available — it's the most compatible
REM with our pinned dependencies (numpy 1.26, opencv 4.10, faster-whisper).
set PYEXE=
where py >nul 2>&1
if %errorlevel% equ 0 (
    py -3.11 --version >nul 2>&1 && set PYEXE=py -3.11
    if "!PYEXE!"=="" py -3.12 --version >nul 2>&1 && set PYEXE=py -3.12
)
if "%PYEXE%"=="" (
    where python >nul 2>&1 || (
        echo ERROR: Python 3.11+ not found. Install from https://www.python.org/downloads/
        pause & exit /b 1
    )
    set PYEXE=python
)

echo ==^> Creating Python venv with %PYEXE%
if not exist .venv (
    %PYEXE% -m venv .venv
)
call .venv\Scripts\activate.bat

echo ==^> Installing base Python dependencies
python -m pip install --upgrade pip wheel >nul
pip install -r pipeline\requirements.txt
if errorlevel 1 ( echo pip install failed & pause & exit /b 1 )

REM ----- Torch installation tuned to the local hardware -----
set TORCH_INDEX=
where nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    REM NVIDIA detected. Read the GPU name to pick the right CUDA toolkit
    REM build. Blackwell (RTX 50xx) needs cu128; older cards work on cu121.
    for /f "usebackq delims=" %%i in (`nvidia-smi --query-gpu=name --format=csv,noheader 2^>nul`) do set GPUNAME=%%i
    echo     GPU: !GPUNAME!
    echo !GPUNAME! | findstr /I "RTX 50" >nul
    if !errorlevel! equ 0 (
        set TORCH_INDEX=https://download.pytorch.org/whl/cu128
        echo ==^> Blackwell detected, installing CUDA 12.8 build of torch
    ) else (
        set TORCH_INDEX=https://download.pytorch.org/whl/cu121
        echo ==^> NVIDIA detected, installing CUDA 12.1 build of torch
    )
) else (
    echo ==^> No NVIDIA GPU detected, installing CPU build of torch
    set TORCH_INDEX=https://download.pytorch.org/whl/cpu
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
