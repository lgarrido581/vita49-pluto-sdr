#!/bin/bash
# Build linuxptp for ARM using Docker (Linux/macOS/WSL script)
#
# Usage: ./build-linuxptp-docker.sh

set -e

echo "=========================================="
echo "Building linuxptp for ARM with Docker"
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

# Create output directory
mkdir -p linuxptp-arm

echo "[1/3] Building Docker image..."
docker build -t linuxptp-builder -f docker/Dockerfile.linuxptp .

echo ""
echo "[2/3] Compiling linuxptp for ARM..."
docker run --rm -v "$(pwd)/linuxptp-arm":/output linuxptp-builder

echo ""
echo "[3/3] Checking binaries..."
if [ -f "linuxptp-arm/ptp4l" ] && [ -f "linuxptp-arm/phc2sys" ]; then
    echo "=========================================="
    echo "✓ SUCCESS: Binaries created!"
    echo "=========================================="
    ls -lh linuxptp-arm/ptp4l
    ls -lh linuxptp-arm/phc2sys
    echo ""
    file linuxptp-arm/ptp4l
    echo ""
    echo "=========================================="
    echo "Next steps:"
    echo "=========================================="
    echo ""
    echo "Deploy to Pluto:"
    echo "  ./scripts/deploy-linuxptp-to-pluto.sh pluto.local"
    echo ""
    echo "Or manually:"
    echo "  scp linuxptp-arm/ptp4l root@pluto.local:/usr/sbin/"
    echo "  scp linuxptp-arm/phc2sys root@pluto.local:/usr/sbin/"
    echo ""
else
    echo "✗ ERROR: Binaries not found"
    exit 1
fi
