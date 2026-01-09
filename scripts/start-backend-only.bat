@echo off
REM Start VITA49 Backend Server Only - Windows
REM Use this for production mode (after running 'npm run build' in web frontend)

setlocal enabledelayedexpansion

echo ========================================
echo VITA49 Backend Server
echo ========================================
echo.

REM Change to project root
cd /d "%~dp0\.."

REM Configuration
set BACKEND_PORT=8001

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    pause
    exit /b 1
)

echo Starting backend server on http://0.0.0.0:%BACKEND_PORT%
echo.
echo Press Ctrl+C to stop the server
echo.

python -m vita49.web_server --host 0.0.0.0 --port %BACKEND_PORT%
