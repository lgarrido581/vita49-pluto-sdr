@echo off
REM Start VITA49 Web UI - Windows
REM This script starts the backend server, frontend dev server, and opens the browser

setlocal enabledelayedexpansion

echo ========================================
echo VITA49 Pluto Web UI Startup
echo ========================================
echo.

REM Change to project root
cd /d "%~dp0\.."

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    echo Please install Python 3.8+ and ensure it's in your PATH
    pause
    exit /b 1
)

REM Check if Node.js is available
where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found in PATH
    echo Please install Node.js and ensure it's in your PATH
    pause
    exit /b 1
)

REM Configuration
set BACKEND_PORT=8001
set FRONTEND_PORT=3000
set PLUTO_IP=pluto.local

echo [1/4] Checking Python dependencies...
python -c "import vita49" 2>nul
if errorlevel 1 (
    echo WARNING: vita49 package not installed
    echo Installing in editable mode...
    pip install -e . >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Failed to install vita49 package
        pause
        exit /b 1
    )
)

echo [2/4] Starting backend server on port %BACKEND_PORT%...
start "VITA49 Backend" cmd /k "python -m vita49.web_server --host 0.0.0.0 --port %BACKEND_PORT%"

REM Wait for backend to start
timeout /t 3 /nobreak >nul

echo [3/4] Starting frontend dev server on port %FRONTEND_PORT%...
cd src\vita49\web
start "VITA49 Frontend" cmd /k "npm run dev"

REM Wait for frontend to start
echo Waiting for frontend server to start...
timeout /t 5 /nobreak >nul

echo [4/4] Opening browser...
timeout /t 2 /nobreak >nul
start http://localhost:%FRONTEND_PORT%

echo.
echo ========================================
echo Web UI Started Successfully!
echo ========================================
echo.
echo Backend:  http://localhost:%BACKEND_PORT%
echo Frontend: http://localhost:%FRONTEND_PORT%
echo.
echo Press any key to stop all servers...
pause >nul

REM Cleanup - kill both server windows
taskkill /FI "WindowTitle eq VITA49 Backend*" /F >nul 2>&1
taskkill /FI "WindowTitle eq VITA49 Frontend*" /F >nul 2>&1

echo Servers stopped.
