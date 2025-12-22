@echo off
REM Quick Deploy Script for VITA49 Pluto Streamer (Windows)
REM
REM Usage: deploy_to_pluto.bat [pluto_ip]
REM Default: pluto.local
REM
REM Requires: PuTTY's pscp.exe and plink.exe in PATH
REM   Or use WSL and run the .sh version instead

setlocal

set PLUTO_IP=%1
if "%PLUTO_IP%"=="" set PLUTO_IP=pluto.local

set PLUTO_USER=root
set SCRIPT_NAME=pluto_vita49_standalone.py

echo ==========================================
echo VITA49 Pluto Deployment Script (Windows)
echo ==========================================
echo Target: %PLUTO_USER%@%PLUTO_IP%
echo.

if not exist "%SCRIPT_NAME%" (
    echo ERROR: %SCRIPT_NAME% not found in current directory
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

echo [1/2] Copying %SCRIPT_NAME% to Pluto...
pscp -pw analog "%SCRIPT_NAME%" "%PLUTO_USER%@%PLUTO_IP%:/root/"
if %errorlevel% neq 0 (
    echo ERROR: Failed to copy file to Pluto
    echo Make sure SSH is enabled and password is correct
    exit /b 1
)

echo [2/2] Making script executable...
plink -pw analog "%PLUTO_USER%@%PLUTO_IP%" "chmod +x /root/%SCRIPT_NAME%"

echo.
echo ==========================================
echo Deployment Complete!
echo ==========================================
echo.
echo To run on Pluto, SSH and execute:
echo   ssh %PLUTO_USER%@%PLUTO_IP%
echo   python3 /root/%SCRIPT_NAME% --dest YOUR_PC_IP
echo.

endlocal
