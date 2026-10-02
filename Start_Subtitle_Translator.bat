@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

:: ============================================================================
:: Subtitle Translator - Windows One-Click Launcher (v2: live captions)
:: ============================================================================

title Subtitle Translator - Launcher

set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%backend"
set "VENV_DIR=%BACKEND_DIR%\venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PIP_EXE=%VENV_DIR%\Scripts\pip.exe"
set "SERVER_HOST=127.0.0.1"
set "SERVER_PORT=8765"

echo.
echo ============================================================================
echo            SUBTITLE TRANSLATOR - One-Click Launcher
echo ============================================================================
echo.
echo Backend location: %BACKEND_DIR%
echo.

if not exist "%BACKEND_DIR%" (
    echo ERROR: Backend directory not found at %BACKEND_DIR%
    pause
    exit /b 1
)

:: ============================================================================
:: Step 1: Python
:: ============================================================================
echo [1/5] Checking Python installation...

python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.10 - 3.12 from https://python.org and tick "Add Python to PATH".
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYTHON_VERSION=%%v"
echo    Found Python %PYTHON_VERSION%
for /f "tokens=1,2 delims=." %%a in ("%PYTHON_VERSION%") do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)
if %PY_MAJOR% LSS 3 goto :python_too_old
if %PY_MAJOR%==3 if %PY_MINOR% LSS 10 goto :python_too_old
goto :python_ok
:python_too_old
echo ERROR: Python 3.10+ is required. Found %PYTHON_VERSION%
pause
exit /b 1
:python_ok

:: ============================================================================
:: Step 2: Virtual environment
:: ============================================================================
echo.
echo [2/5] Setting up Python virtual environment...
if not exist "%PYTHON_EXE%" (
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%" 2>nul
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
    echo    OK - Virtual environment created
) else (
    echo    OK - Virtual environment exists
)

:: ============================================================================
:: Step 3: Dependencies (core + GPU extras when an NVIDIA GPU is present)
:: ============================================================================
echo.
echo [3/5] Installing/verifying Python dependencies...

"%PYTHON_EXE%" -c "import fastapi, sherpa_onnx, ctranslate2" >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :core_ok
echo    Installing core dependencies (first run: a few minutes)...
"%PYTHON_EXE%" -m pip install --upgrade pip >nul
"%PIP_EXE%" install -r "%BACKEND_DIR%\requirements.txt"
if errorlevel 1 (
    echo ERROR: Failed to install dependencies. Try manually:
    echo    %PIP_EXE% install -r %BACKEND_DIR%\requirements.txt
    pause
    exit /b 1
)
:core_ok
echo    OK - Core dependencies installed

set "HAS_NVIDIA=0"
where nvidia-smi >nul 2>&1 && set "HAS_NVIDIA=1"
if "%HAS_NVIDIA%"=="1" (
    "%PYTHON_EXE%" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
    if !ERRORLEVEL! NEQ 0 (
        echo    NVIDIA GPU detected - installing CUDA extras ^(torch, faster-whisper^)...
        echo    This downloads ~3 GB once. GPU enables HY-MT translation and accuracy mode.
        "%PIP_EXE%" install -r "%BACKEND_DIR%\requirements-gpu.txt" --extra-index-url https://download.pytorch.org/whl/cu128
        if errorlevel 1 (
            echo    WARNING: GPU extras failed to install. Continuing on CPU ^(still works^).
        ) else (
            echo    OK - GPU extras installed
        )
    ) else (
        echo    OK - CUDA torch already installed
    )
) else (
    echo    No NVIDIA GPU detected - running on CPU ^(streaming captions + OPUS-MT^).
    "%PYTHON_EXE%" -c "import torch" >nul 2>&1 || "%PIP_EXE%" install torch --index-url https://download.pytorch.org/whl/cpu >nul 2>&1
)

:: ============================================================================
:: Step 4: Models
:: ============================================================================
echo.
echo [4/5] Models
echo    Speech models ^(English, Chinese, Bengali; ~350 MB^), the language-ID model,
echo    OPUS-MT converters and - with a GPU - llama.cpp + HY-MT1.5 ^(~2.2 GB^) are
echo    downloaded automatically on first start. The first launch can take several
echo    minutes; later launches take ~10 seconds.

:: ============================================================================
:: Step 5: Start the backend
:: ============================================================================
echo.
echo [5/5] Starting backend server...
netstat -an 2>nul | findstr ":%SERVER_PORT% " | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo    Server already running on http://%SERVER_HOST%:%SERVER_PORT%
    goto :server_ready
)
pushd "%BACKEND_DIR%"
set PYTHONIOENCODING=utf-8
start "Subtitle Translator Server" cmd /k ""%PYTHON_EXE%" run.py --host %SERVER_HOST% --port %SERVER_PORT%"
popd

echo    Waiting for server to start ^(model download on first run^)...
set "ATTEMPTS=0"
:wait_loop
timeout /t 2 /nobreak >nul
set /a ATTEMPTS+=1
netstat -an 2>nul | findstr ":%SERVER_PORT% " | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :server_ready
if %ATTEMPTS% GEQ 300 goto :server_timeout
goto :wait_loop

:server_timeout
echo.
echo ERROR: Server did not start within 10 minutes. Check the server window for errors.
pause
exit /b 1

:server_ready
echo    OK - Server is running
echo.
echo ============================================================================
echo    SUBTITLE TRANSLATOR IS RUNNING!
echo ============================================================================
echo.
echo    Server:  http://%SERVER_HOST%:%SERVER_PORT%
echo    Health:  http://%SERVER_HOST%:%SERVER_PORT%/health
echo.
echo    Next steps:
echo    1. chrome://extensions -^> Developer mode -^> Load unpacked -^> select the 'extension' folder
echo    2. Videos WITH subtitles: translations appear automatically
echo    3. Videos WITHOUT subtitles: click the extension icon -^> "Start Live Captions" ^(or Alt+L^)
echo.
start "" "http://%SERVER_HOST%:%SERVER_PORT%/health"
echo Press any key to close this window. The server keeps running in its own window.
pause >nul
