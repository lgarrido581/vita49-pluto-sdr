@echo off
REM Deploy VITA49 Pluto Streamer Binaries (Windows)
REM
REM Usage:
REM   deploy_to_pluto.bat              Deploy and run streamer
REM   deploy_to_pluto.bat --diag       Deploy and run diagnostic
REM   deploy_to_pluto.bat [pluto_ip]   Deploy to specific IP
REM
REM Requires: PuTTY's pscp.exe and plink.exe in PATH
REM   Or use WSL and run the .sh version instead

setlocal

set RUN_DIAG=0
set PLUTO_IP=pluto.local

REM Parse arguments
:parse_args
if "%1"=="" goto done_args
if "%1"=="--diag" (
    set RUN_DIAG=1
    shift
    goto parse_args
)
if "%1"=="-d" (
    set RUN_DIAG=1
    shift
    goto parse_args
)
set PLUTO_IP=%1
shift
goto parse_args
:done_args

set PLUTO_USER=root

echo ==========================================
echo VITA49 Pluto Deployment Script (Windows)
echo ==========================================
echo Target: %PLUTO_USER%@%PLUTO_IP%
echo.

REM Go to project root
cd /d "%~dp0\.."

REM Check if binaries exist
if not exist "vita49_streamer" (
    echo ERROR: vita49_streamer not found
    echo Run build-with-docker.bat first
    exit /b 1
)

REM Check if pscp is available
where pscp >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pscp.exe not found in PATH
    echo.
    echo Please install PuTTY tools or use WSL with deploy_to_pluto.sh
    echo Download from: https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html
    exit /b 1
)

echo [1/3] Stopping existing processes...
plink -pw analog "%PLUTO_USER%@%PLUTO_IP%" "killall vita49_streamer iio_buffer_diagnostic 2>/dev/null; exit 0"

echo [2/3] Copying binaries to Pluto...
pscp -scp -pw analog "vita49_streamer" "%PLUTO_USER%@%PLUTO_IP%:/root/"
if exist "iio_buffer_diagnostic" (
    pscp -scp -pw analog "iio_buffer_diagnostic" "%PLUTO_USER%@%PLUTO_IP%:/root/"
)

echo [3/3] Setting permissions...
plink -pw analog "%PLUTO_USER%@%PLUTO_IP%" "chmod +x /root/vita49_streamer /root/iio_buffer_diagnostic 2>/dev/null; exit 0"

echo.
echo ==========================================
echo Deployment Complete!
echo ==========================================
echo.

if %RUN_DIAG%==1 (
    echo Running diagnostic tool...
    plink -pw analog "%PLUTO_USER%@%PLUTO_IP%" "/root/iio_buffer_diagnostic --all-rates --memory-test"
) else (
    echo To run on Pluto:
    echo   ssh %PLUTO_USER%@%PLUTO_IP%
    echo   ./vita49_streamer
    echo.
    echo Or run diagnostic:
    echo   deploy_to_pluto.bat --diag
)
echo.

endlocal
