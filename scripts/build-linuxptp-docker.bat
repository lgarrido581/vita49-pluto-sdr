@echo off
REM Build linuxptp for ARM using Docker (Windows batch script)
REM
REM Usage: build-linuxptp-docker.bat

echo ==========================================
echo Building linuxptp for ARM with Docker
echo ==========================================
echo.

REM Check if Docker is available
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Docker is not installed or not in PATH
    echo.
    echo Please install Docker Desktop from:
    echo https://www.docker.com/products/docker-desktop
    echo.
    pause
    exit /b 1
)

REM Change to project root directory
cd /d "%~dp0\.."

REM Create output directory if it doesn't exist
if not exist "linuxptp-arm" mkdir linuxptp-arm

echo [1/3] Building Docker image...
docker build -t linuxptp-builder -f docker/Dockerfile.linuxptp . || (
    echo ERROR: Failed to build Docker image
    pause
    exit /b 1
)

echo.
echo [2/3] Compiling linuxptp for ARM...
docker run --rm -v "%cd%\linuxptp-arm":/output linuxptp-builder || (
    echo ERROR: Compilation failed
    pause
    exit /b 1
)

echo.
echo [3/3] Checking binaries...
if exist "linuxptp-arm\ptp4l" (
    if exist "linuxptp-arm\phc2sys" (
        echo ==========================================
        echo SUCCESS: Binaries created!
        echo ==========================================
        dir linuxptp-arm\ptp4l
        dir linuxptp-arm\phc2sys
        echo.
        echo Next steps:
        echo   1. Deploy to Pluto: .\scripts\deploy-linuxptp-to-pluto.bat
        echo   2. Or use SCP manually
        echo.
    ) else (
        echo ERROR: phc2sys not found
        pause
        exit /b 1
    )
) else (
    echo ERROR: ptp4l not found
    pause
    exit /b 1
)

pause
