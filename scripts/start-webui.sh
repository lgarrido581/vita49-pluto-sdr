#!/bin/bash
# Start VITA49 Web UI - Linux/macOS
# This script starts the backend server, frontend dev server, and opens the browser

set -e

echo "========================================"
echo "VITA49 Pluto Web UI Startup"
echo "========================================"
echo ""

# Change to project root
cd "$(dirname "$0")/.."

# Configuration
BACKEND_PORT=8001
FRONTEND_PORT=3000
PLUTO_IP="pluto.local"

# PIDs for cleanup
BACKEND_PID=""
FRONTEND_PID=""

# Cleanup function
cleanup() {
    echo ""
    echo "Stopping servers..."

    if [ ! -z "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
        echo "Backend stopped (PID: $BACKEND_PID)"
    fi

    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null || true
        # Also kill any child processes (npm spawns node)
        pkill -P $FRONTEND_PID 2>/dev/null || true
        echo "Frontend stopped (PID: $FRONTEND_PID)"
    fi

    echo "Cleanup complete."
    exit 0
}

# Register cleanup on exit
trap cleanup EXIT INT TERM

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found"
    echo "Please install Python 3.8+ and ensure it's in your PATH"
    exit 1
fi

# Check if Node.js is available
if ! command -v node &> /dev/null; then
    echo "ERROR: Node.js not found"
    echo "Please install Node.js and ensure it's in your PATH"
    exit 1
fi

echo "[1/4] Checking Python dependencies..."
python3 -c "import vita49" 2>/dev/null || {
    echo "WARNING: vita49 package not installed"
    echo "Installing in editable mode..."
    pip3 install -e . > /dev/null 2>&1 || {
        echo "ERROR: Failed to install vita49 package"
        exit 1
    }
}

echo "[2/4] Starting backend server on port $BACKEND_PORT..."
python3 -m vita49.web_server --host 0.0.0.0 --port $BACKEND_PORT > /tmp/vita49-backend.log 2>&1 &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID)"

# Wait for backend to start
sleep 3

# Check if backend is still running
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "ERROR: Backend failed to start. Check /tmp/vita49-backend.log"
    cat /tmp/vita49-backend.log
    exit 1
fi

echo "[3/4] Starting frontend dev server on port $FRONTEND_PORT..."
cd src/vita49/web

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "Installing frontend dependencies (first run only)..."
    npm install
fi

npm run dev > /tmp/vita49-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID)"
cd ../../..

# Wait for frontend to start
echo "Waiting for frontend server to start..."
sleep 5

# Check if frontend is still running
if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo "ERROR: Frontend failed to start. Check /tmp/vita49-frontend.log"
    cat /tmp/vita49-frontend.log
    exit 1
fi

echo "[4/4] Opening browser..."
sleep 2

# Open browser (cross-platform)
if command -v xdg-open &> /dev/null; then
    xdg-open "http://localhost:$FRONTEND_PORT" &
elif command -v open &> /dev/null; then
    open "http://localhost:$FRONTEND_PORT"
elif command -v start &> /dev/null; then
    start "http://localhost:$FRONTEND_PORT"
else
    echo "Could not detect how to open browser. Please open manually:"
    echo "http://localhost:$FRONTEND_PORT"
fi

echo ""
echo "========================================"
echo "Web UI Started Successfully!"
echo "========================================"
echo ""
echo "Backend:  http://localhost:$BACKEND_PORT"
echo "Frontend: http://localhost:$FRONTEND_PORT"
echo ""
echo "Logs:"
echo "  Backend:  /tmp/vita49-backend.log"
echo "  Frontend: /tmp/vita49-frontend.log"
echo ""
echo "Press Ctrl+C to stop all servers..."
echo ""

# Wait forever until Ctrl+C
while true; do
    # Check if servers are still running
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "ERROR: Backend crashed! Check /tmp/vita49-backend.log"
        exit 1
    fi

    if ! kill -0 $FRONTEND_PID 2>/dev/null; then
        echo "ERROR: Frontend crashed! Check /tmp/vita49-frontend.log"
        exit 1
    fi

    sleep 5
done
