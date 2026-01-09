#!/bin/bash
# Start VITA49 Backend Server Only - Linux/macOS
# Use this for production mode (after running 'npm run build' in web frontend)

echo "========================================"
echo "VITA49 Backend Server"
echo "========================================"
echo ""

# Change to project root
cd "$(dirname "$0")/.."

# Configuration
BACKEND_PORT=8001

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found"
    exit 1
fi

echo "Starting backend server on http://0.0.0.0:$BACKEND_PORT"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

python3 -m vita49.web_server --host 0.0.0.0 --port $BACKEND_PORT
