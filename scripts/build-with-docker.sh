#!/bin/bash
# Build VITA49 streamer using Docker (Linux/Mac/WSL script)
#
# Usage: ./build-with-docker.sh

set -e

echo "=========================================="
echo "Building VITA49 Streamer with Docker"
echo "=========================================="
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed"
    echo ""
    echo "Install from: https://www.docker.com/products/docker-desktop"
    exit 1
fi

# Change to project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "[1/3] Building Docker image..."
docker build -t pluto-builder -f docker/Dockerfile .

echo ""
echo "[2/3] Compiling ARM binary..."
docker run --rm -v "$(pwd)":/build pluto-builder

echo ""
echo "[3/3] Checking binaries..."
if [ -f vita49_streamer ]; then
    echo "✓ SUCCESS: Streamer binary created!"
    ls -lh vita49_streamer
    file vita49_streamer
else
    echo "✗ ERROR: vita49_streamer not found"
    exit 1
fi

if [ -f iio_buffer_diagnostic ]; then
    echo "✓ SUCCESS: Diagnostic binary created!"
    ls -lh iio_buffer_diagnostic
else
    echo "⚠ WARNING: iio_buffer_diagnostic not found"
fi

echo ""
echo "=========================================="
echo "Next steps:"
echo "=========================================="
echo ""
echo "Deploy to Pluto:"
echo "  ./scripts/deploy_to_pluto.sh           # Run streamer"
echo "  ./scripts/deploy_to_pluto.sh --diag    # Run diagnostic"
echo ""
echo "Or use make:"
echo "  make deploy-binary PLUTO_IP=pluto.local"
echo "  make deploy-all PLUTO_IP=pluto.local"
echo ""
