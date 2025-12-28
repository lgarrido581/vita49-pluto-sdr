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

REM Change to project root directory
cd /d "%~dp0\.."

echo [1/3] Building Docker image...
docker build -t pluto-builder -f docker/Dockerfile . || (
    echo ERROR: Failed to build Docker image
    pause
    exit /b 1
)

echo.
echo [2/3] Compiling ARM binary...
docker run --rm -v "%cd%":/build pluto-builder || (
    echo ERROR: Compilation failed
    pause
    exit /b 1
)

echo.
echo [3/3] Checking binaries...
if exist vita49_streamer (
    echo SUCCESS: Streamer binary created!
    dir vita49_streamer
) else (
    echo ERROR: vita49_streamer not found
    pause
    exit /b 1
)

if exist iio_buffer_diagnostic (
    echo SUCCESS: Diagnostic binary created!
    dir iio_buffer_diagnostic
) else (
    echo WARNING: iio_buffer_diagnostic not found
)

echo.
echo ==========================================
echo Next steps:
echo ==========================================
echo.
echo Deploy to Pluto:
echo   scripts\deploy_to_pluto.bat              Run streamer
echo   scripts\deploy_to_pluto.bat --diag       Run diagnostic
echo.
echo Or manually:
echo   scp vita49_streamer root@pluto.local:/root/
echo   scp iio_buffer_diagnostic root@pluto.local:/root/
echo.

pause
