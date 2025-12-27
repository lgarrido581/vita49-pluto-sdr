@echo off
REM Deploy linuxptp binaries to Pluto (Windows batch script)
REM
REM Usage: deploy-linuxptp-to-pluto.bat [pluto_ip]

setlocal

REM Set default values
set PLUTO_IP=%1
if "%PLUTO_IP%"=="" set PLUTO_IP=pluto.local
set PLUTO_USER=root
set PLUTO_PASS=analog

echo ==========================================
echo Deploying linuxptp to Pluto
echo ==========================================
echo Target: %PLUTO_USER%@%PLUTO_IP%
echo.

REM Change to project root
cd /d "%~dp0\.."

REM Check if binaries exist
if not exist "linuxptp-arm\ptp4l" (
    echo ERROR: ptp4l not found in linuxptp-arm\
    echo.
    echo Build it first with: .\scripts\build-linuxptp-docker.bat
    pause
    exit /b 1
)

if not exist "linuxptp-arm\phc2sys" (
    echo ERROR: phc2sys not found in linuxptp-arm\
    echo.
    echo Build it first with: .\scripts\build-linuxptp-docker.bat
    pause
    exit /b 1
)

echo [1/4] Checking for sshpass...
where sshpass >nul 2>&1
if %errorlevel% equ 0 (
    echo ✓ sshpass found - using password authentication
    set USE_SSHPASS=1
) else (
    echo ⚠ sshpass not found - you'll need to enter password manually
    echo.
    echo To install sshpass on Windows:
    echo   1. Install via Chocolatey: choco install sshpass
    echo   2. Or use WSL: wsl sshpass -p analog scp ...
    echo.
    set USE_SSHPASS=0
)

echo.
echo [2/4] Copying ptp4l to Pluto...
if "%USE_SSHPASS%"=="1" (
    sshpass -p %PLUTO_PASS% scp -o StrictHostKeyChecking=no linuxptp-arm\ptp4l %PLUTO_USER%@%PLUTO_IP%:/usr/sbin/
) else (
    echo Enter password when prompted (default: analog^)
    scp -o StrictHostKeyChecking=no linuxptp-arm\ptp4l %PLUTO_USER%@%PLUTO_IP%:/usr/sbin/
)
if %errorlevel% neq 0 (
    echo ERROR: Failed to copy ptp4l
    pause
    exit /b 1
)

echo.
echo [3/4] Copying phc2sys to Pluto...
if "%USE_SSHPASS%"=="1" (
    sshpass -p %PLUTO_PASS% scp -o StrictHostKeyChecking=no linuxptp-arm\phc2sys %PLUTO_USER%@%PLUTO_IP%:/usr/sbin/
) else (
    echo Enter password when prompted (default: analog^)
    scp -o StrictHostKeyChecking=no linuxptp-arm\phc2sys %PLUTO_USER%@%PLUTO_IP%:/usr/sbin/
)
if %errorlevel% neq 0 (
    echo ERROR: Failed to copy phc2sys
    pause
    exit /b 1
)

echo.
echo [4/4] Making binaries executable...
if "%USE_SSHPASS%"=="1" (
    sshpass -p %PLUTO_PASS% ssh -o StrictHostKeyChecking=no %PLUTO_USER%@%PLUTO_IP% "chmod +x /usr/sbin/ptp4l /usr/sbin/phc2sys"
) else (
    echo Enter password when prompted (default: analog^)
    ssh -o StrictHostKeyChecking=no %PLUTO_USER%@%PLUTO_IP% "chmod +x /usr/sbin/ptp4l /usr/sbin/phc2sys"
)

echo.
echo ==========================================
echo ✓ Deployment complete!
echo ==========================================
echo.
echo Next steps:
echo.
echo 1. SSH to Pluto:
echo    ssh %PLUTO_USER%@%PLUTO_IP%
echo.
echo 2. Test PTP binaries:
echo    ptp4l --version
echo    phc2sys --version
echo.
echo 3. Configure PTP slave mode:
echo    See PTP_SYNC_IMPLEMENTATION_GUIDE.md for configuration
echo.

pause
endlocal
