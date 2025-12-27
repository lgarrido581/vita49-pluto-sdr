#!/bin/bash
# Deploy linuxptp binaries to Pluto (Linux/macOS/WSL script)
#
# Usage: ./deploy-linuxptp-to-pluto.sh [pluto_ip]

set -e

# Set default values
PLUTO_IP=${1:-pluto.local}
PLUTO_USER=root
PLUTO_PASS=analog

echo "=========================================="
echo "Deploying linuxptp to Pluto"
echo "=========================================="
echo "Target: $PLUTO_USER@$PLUTO_IP"
echo ""

# Change to project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Check if binaries exist
if [ ! -f "linuxptp-arm/ptp4l" ]; then
    echo "ERROR: ptp4l not found in linuxptp-arm/"
    echo ""
    echo "Build it first with: ./scripts/build-linuxptp-docker.sh"
    exit 1
fi

if [ ! -f "linuxptp-arm/phc2sys" ]; then
    echo "ERROR: phc2sys not found in linuxptp-arm/"
    echo ""
    echo "Build it first with: ./scripts/build-linuxptp-docker.sh"
    exit 1
fi

# Check for sshpass
echo "[1/4] Checking for sshpass..."
if command -v sshpass &> /dev/null; then
    echo "✓ sshpass found - using password authentication"
    USE_SSHPASS=1
else
    echo "⚠ sshpass not found - you'll need to enter password manually"
    echo ""
    echo "To install sshpass:"
    echo "  Ubuntu/Debian: sudo apt-get install sshpass"
    echo "  macOS: brew install hudochenkov/sshpass/sshpass"
    echo ""
    USE_SSHPASS=0
fi

# Copy ptp4l
echo ""
echo "[2/4] Copying ptp4l to Pluto..."
if [ "$USE_SSHPASS" -eq 1 ]; then
    sshpass -p "$PLUTO_PASS" scp -o StrictHostKeyChecking=no \
        linuxptp-arm/ptp4l "$PLUTO_USER@$PLUTO_IP:/usr/sbin/"
else
    echo "Enter password when prompted (default: analog)"
    scp -o StrictHostKeyChecking=no \
        linuxptp-arm/ptp4l "$PLUTO_USER@$PLUTO_IP:/usr/sbin/"
fi

# Copy phc2sys
echo ""
echo "[3/4] Copying phc2sys to Pluto..."
if [ "$USE_SSHPASS" -eq 1 ]; then
    sshpass -p "$PLUTO_PASS" scp -o StrictHostKeyChecking=no \
        linuxptp-arm/phc2sys "$PLUTO_USER@$PLUTO_IP:/usr/sbin/"
else
    echo "Enter password when prompted (default: analog)"
    scp -o StrictHostKeyChecking=no \
        linuxptp-arm/phc2sys "$PLUTO_USER@$PLUTO_IP:/usr/sbin/"
fi

# Make executable
echo ""
echo "[4/4] Making binaries executable..."
if [ "$USE_SSHPASS" -eq 1 ]; then
    sshpass -p "$PLUTO_PASS" ssh -o StrictHostKeyChecking=no \
        "$PLUTO_USER@$PLUTO_IP" "chmod +x /usr/sbin/ptp4l /usr/sbin/phc2sys"
else
    echo "Enter password when prompted (default: analog)"
    ssh -o StrictHostKeyChecking=no \
        "$PLUTO_USER@$PLUTO_IP" "chmod +x /usr/sbin/ptp4l /usr/sbin/phc2sys"
fi

echo ""
echo "=========================================="
echo "✓ Deployment complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. SSH to Pluto:"
echo "   ssh $PLUTO_USER@$PLUTO_IP"
echo ""
echo "2. Test PTP binaries:"
echo "   ptp4l --version"
echo "   phc2sys --version"
echo ""
echo "3. Configure PTP slave mode:"
echo "   See PTP_SYNC_IMPLEMENTATION_GUIDE.md for configuration"
echo ""
