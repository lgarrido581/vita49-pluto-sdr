#!/bin/bash

#go up a level
cd ..

# Kill any existing vita49_streamer process on the Pluto
ssh -t root@pluto.local "killall vita49_streamer 2>/dev/null || true"

# Upload the new vita49_streamer binary
cat vita49_streamer | ssh root@pluto.local "cat > vita49_streamer"

# Run commands on the Pluto
ssh -tt root@pluto.local << 'EOF'
chmod 777 vita49_streamer
./vita49_streamer &
exit
EOF