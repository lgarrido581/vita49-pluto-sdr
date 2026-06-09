#!/bin/bash
# Deploy VITA49 streamer and diagnostic tools to Pluto
#
# Usage:
#   ./deploy_to_pluto.sh           # Deploy and run streamer
#   ./deploy_to_pluto.sh --diag    # Deploy and run diagnostic

# Go up a level to project root
cd "$(dirname "$0")/.."

# Check what to run
RUN_DIAG=false
if [ "$1" = "--diag" ] || [ "$1" = "-d" ]; then
    RUN_DIAG=true
fi

# Kill any existing processes on the Pluto
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -t root@pluto.local "killall vita49_streamer iio_buffer_diagnostic 2>/dev/null || true"

# Upload the vita49_streamer binary
if [ -f vita49_streamer ]; then
    echo "Uploading vita49_streamer..."
    cat vita49_streamer | ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@pluto.local "cat > vita49_streamer"
fi

# Upload the diagnostic binary if it exists
if [ -f iio_buffer_diagnostic ]; then
    echo "Uploading iio_buffer_diagnostic..."
    cat iio_buffer_diagnostic | ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@pluto.local "cat > iio_buffer_diagnostic"
fi

# Run commands on the Pluto
if [ "$RUN_DIAG" = true ]; then
    echo "Running diagnostic..."
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -tt root@pluto.local << 'EOF'
chmod 777 vita49_streamer iio_buffer_diagnostic 2>/dev/null
./iio_buffer_diagnostic --all-rates --memory-test
EOF
else
    echo "Running streamer..."
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -tt root@pluto.local << 'EOF'
chmod 777 vita49_streamer iio_buffer_diagnostic 2>/dev/null
./vita49_streamer
EOF
fi