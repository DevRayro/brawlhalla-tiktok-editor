@echo off
REM Windows setup — installs Python venv + dependencies + downloads SAM2 model.
REM Detects NVIDIA GPU and installs CUDA-enabled PyTorch automatically.
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
where python >nul 2>&1 || (
    echo ERROR: Python 3.11+ not found. Install from https://www.python.org/downloads/
    pause & exit /b 1
)

echo ==^> Creating Python venv
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo ==^> Installing Python dependencies
python -m pip install --upgrade pip wheel >nul
pip install -r pipeline\requirements.txt
if errorlevel 1 ( echo pip install failed & pause & exit /b 1 )

REM CUDA detection.
where nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    echo ==^> NVIDIA GPU detected, installing CUDA build of torch
    pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu121
    pip install nvidia-cudnn-cu12==9.*
)

echo ==^> Downloading SAM2 checkpoint ^(~180MB, optional^)
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
