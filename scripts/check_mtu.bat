@echo off
setlocal enabledelayedexpansion

:: ===== CONFIGURE THESE VALUES =====
set PLUTO_IP=pluto.local
set PLUTO_PASS=analog
set INTERFACE_NAME=Ethernet 2
set TARGET_MTU=9000
:: ==================================

echo.
echo Checking MTU for Pluto+ at %PLUTO_IP% via interface "%INTERFACE_NAME%"
echo.

:: 1. Get Host (Windows) MTU
echo Host (Windows) MTU:
for /f "tokens=1*" %%a in ('netsh interface ipv4 show subinterfaces ^| find "%INTERFACE_NAME%"') do (
    set LINE=%%a %%b
    for /f "tokens=1" %%x in ("!LINE!") do set HOST_MTU=%%x
)
if defined HOST_MTU (
    echo   %INTERFACE_NAME%: %HOST_MTU% bytes
) else (
    echo   ERROR: Interface "%INTERFACE_NAME%" not found or no MTU listed.
    echo   Run "netsh interface ipv4 show subinterfaces" to check names.
    goto :end
)

:: 2. Pluto+ MTU via SSH - safer parsing
echo.
echo Pluto+ MTU (via SSH):
for /f "delims=" %%i in ('plink -ssh -pw %PLUTO_PASS% -batch root@%PLUTO_IP% "ip link show eth0" ^| findstr /i "mtu"') do set PLUTO_LINE=%%i
echo   %PLUTO_LINE%
for /f "tokens=5" %%a in ("%PLUTO_LINE%") do set PLUTO_MTU=%%a

if defined PLUTO_MTU (
    echo   Parsed Pluto MTU: %PLUTO_MTU% bytes
) else (
    echo   ERROR: Could not retrieve Pluto MTU (check connectivity/password/plink).
)

:: 3. Path MTU test
echo.
echo Testing if path supports MTU %TARGET_MTU% (payload !TARGET_MTU!-28 bytes):
set /a PAYLOAD=%TARGET_MTU% - 28
ping -f -l %PAYLOAD% -n 1 %PLUTO_IP% > nul 2>&1
if errorlevel 1 (
    echo   Path does NOT support %TARGET_MTU% (drops/fragmentation - mismatch confirmed)
) else (
    echo   Path supports at least %TARGET_MTU% (success!)
)

:: Summary
echo.
echo Summary:
if defined HOST_MTU if defined PLUTO_MTU (
    if %HOST_MTU%==%PLUTO_MTU% (
        echo   MTUs match (%HOST_MTU% bytes) - good for jumbo if path test passed!
    ) else (
        echo   MTU MISMATCH: Host %HOST_MTU% vs Pluto %PLUTO_MTU% - set Pluto to 9000.
    )
)

:end
echo.
pause