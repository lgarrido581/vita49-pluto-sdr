#!/bin/bash
#
# VITA 49 Pluto+ Deployment Script
#
# Deploys the embedded VITA 49 streamer to the Pluto+ ARM processor.
#
# Usage:
#   ./deploy_to_pluto.sh [pluto_ip] [dest_ip]
#
# Examples:
#   ./deploy_to_pluto.sh                        # Uses defaults
#   ./deploy_to_pluto.sh 192.168.2.1 192.168.2.100
#   ./deploy_to_pluto.sh pluto.local 10.0.0.50
#

set -e

# Defaults
PLUTO_IP="${1:-192.168.2.1}"
DEST_IP="${2:-192.168.2.100}"
PLUTO_USER="root"
PLUTO_PASS="analog"
DEPLOY_DIR="/root/vita49"

echo "=================================================="
echo "VITA 49 Pluto+ Deployment"
echo "=================================================="
echo "Pluto IP:    $PLUTO_IP"
echo "Stream Dest: $DEST_IP"
echo "Deploy Dir:  $DEPLOY_DIR"
echo "=================================================="

# Check if sshpass is available (optional, for non-interactive)
if command -v sshpass &> /dev/null; then
    SSH_CMD="sshpass -p $PLUTO_PASS ssh -o StrictHostKeyChecking=no"
    SCP_CMD="sshpass -p $PLUTO_PASS scp -o StrictHostKeyChecking=no"
else
    echo "Note: sshpass not found. You'll be prompted for password (default: analog)"
    SSH_CMD="ssh -o StrictHostKeyChecking=no"
    SCP_CMD="scp -o StrictHostKeyChecking=no"
fi

# Test connection
echo ""
echo "[1/5] Testing connection to Pluto+..."
$SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "echo 'Connection OK'" || {
    echo "ERROR: Cannot connect to Pluto+ at $PLUTO_IP"
    echo "Check that:"
    echo "  - Pluto+ is powered on and connected"
    echo "  - IP address is correct"
    echo "  - SSH is enabled on Pluto+"
    exit 1
}

# Check for Python and numpy
echo ""
echo "[2/5] Checking Pluto+ environment..."
$SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "python3 --version" || {
    echo "ERROR: Python3 not found on Pluto+"
    exit 1
}

$SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "python3 -c 'import numpy'" 2>/dev/null || {
    echo "WARNING: numpy not found. Attempting to install..."
    $SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "opkg update && opkg install python3-numpy" || {
        echo "ERROR: Could not install numpy. Please install manually:"
        echo "  ssh root@$PLUTO_IP"
        echo "  opkg update"
        echo "  opkg install python3-numpy"
        exit 1
    }
}

# Check for pyadi-iio
$SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "python3 -c 'import adi'" 2>/dev/null || {
    echo "WARNING: pyadi-iio not found. Attempting to install..."
    $SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "pip3 install pyadi-iio --break-system-packages" 2>/dev/null || {
        echo "NOTE: pyadi-iio install failed. Trying alternative..."
        # Try opkg if available
        $SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "opkg install python3-pyadi-iio" 2>/dev/null || {
            echo "WARNING: Could not install pyadi-iio automatically."
            echo "The streamer may still work if libiio is available."
        }
    }
}

# Create deployment directory
echo ""
echo "[3/5] Creating deployment directory..."
$SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "mkdir -p $DEPLOY_DIR"

# Copy files
echo ""
echo "[4/5] Copying files to Pluto+..."
$SCP_CMD vita49_embedded.py ${PLUTO_USER}@${PLUTO_IP}:${DEPLOY_DIR}/
$SCP_CMD run_vita49.sh ${PLUTO_USER}@${PLUTO_IP}:${DEPLOY_DIR}/ 2>/dev/null || true

# Create run script on Pluto+
echo ""
echo "[5/5] Creating run script..."
$SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "cat > ${DEPLOY_DIR}/start_streaming.sh << 'EOF'
#!/bin/sh
# VITA 49 Streaming startup script
# Edit DEST_IP to change streaming destination

DEST_IP=\"$DEST_IP\"
FREQ=\"2.4e9\"
RATE=\"30e6\"
GAIN=\"20\"
CHANNELS=\"0\"
PORT=\"4991\"

cd $DEPLOY_DIR
python3 vita49_embedded.py \\
    --uri local \\
    --dest \$DEST_IP \\
    --port \$PORT \\
    --freq \$FREQ \\
    --rate \$RATE \\
    --gain \$GAIN \\
    --channels \$CHANNELS

EOF"

$SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "chmod +x ${DEPLOY_DIR}/start_streaming.sh"

echo ""
echo "=================================================="
echo "Deployment Complete!"
echo "=================================================="
echo ""
echo "To start streaming manually:"
echo "  ssh root@$PLUTO_IP"
echo "  cd $DEPLOY_DIR"
echo "  ./start_streaming.sh"
echo ""
echo "Or run directly:"
echo "  ssh root@$PLUTO_IP 'cd $DEPLOY_DIR && python3 vita49_embedded.py --uri local --dest $DEST_IP'"
echo ""
echo "To receive on host ($DEST_IP):"
echo "  python3 signal_processing_harness.py --port 4991"
echo ""

# Optionally start streaming now
read -p "Start streaming now? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Starting streaming (Ctrl+C to stop)..."
    $SSH_CMD ${PLUTO_USER}@${PLUTO_IP} "cd $DEPLOY_DIR && ./start_streaming.sh"
fi
