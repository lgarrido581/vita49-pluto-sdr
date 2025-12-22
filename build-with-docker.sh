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

echo "[1/3] Building Docker image..."
docker build -t pluto-builder .

echo ""
echo "[2/3] Compiling ARM binary..."
docker run --rm -v "$(pwd)":/build pluto-builder

echo ""
echo "[3/3] Checking binary..."
if [ -f vita49_streamer ]; then
    echo "✓ SUCCESS: Binary created!"
    ls -lh vita49_streamer
    file vita49_streamer
    echo ""
    echo "=========================================="
    echo "Next steps:"
    echo "=========================================="
    echo ""
    echo "Deploy to Pluto:"
    echo "  scp vita49_streamer root@pluto.local:/root/"
    echo ""
    echo "Or use automated deploy:"
    echo "  make deploy-binary PLUTO_IP=pluto.local"
    echo ""
else
    echo "✗ ERROR: Binary not found"
    exit 1
fi
