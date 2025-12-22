#!/bin/bash

# Kill any existing vita49_streamer process on the Pluto
ssh -t root@pluto.local "killall vita49_streamer 2>/dev/null || true"