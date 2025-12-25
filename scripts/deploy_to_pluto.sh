#!/bin/bash

#go up a level
cd ..

# Kill any existing vita49_streamer process on the Pluto
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -t root@pluto.local "killall vita49_streamer 2>/dev/null || true"

# Upload the new vita49_streamer binary
cat vita49_streamer | ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@pluto.local "cat > vita49_streamer"

# Run commands on the Pluto
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -tt root@pluto.local << 'EOF'
chmod 777 vita49_streamer
./vita49_streamer &
EOF