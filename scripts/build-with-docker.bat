@echo off
REM Build VITA49 streamer using Docker (Windows batch script)
REM
REM Usage: build-with-docker.bat

echo ==========================================
echo Building VITA49 Streamer with Docker
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

echo [1/3] Building Docker image...
docker build -t pluto-builder . || (
    echo ERROR: Failed to build Docker image
    pause
    exit /b 1
)

echo.
echo [2/3] Compiling ARM binary...
docker run --rm -v %cd%:/build pluto-builder || (
    echo ERROR: Compilation failed
    pause
    exit /b 1
)

echo.
echo [3/3] Checking binary...
if exist vita49_streamer (
    echo SUCCESS: Binary created!
    dir vita49_streamer
    echo.
    echo ==========================================
    echo Next steps:
    echo ==========================================
    echo.
    echo Deploy to Pluto with one of:
    echo   1. scp vita49_streamer root@pluto.local:/root/
    echo   2. Use WinSCP GUI
    echo   3. Use deploy-to-pluto.bat
    echo.
) else (
    echo ERROR: Binary not found
    pause
    exit /b 1
)

pause
