#!/bin/bash
PLUTO_IP=pluto.local
PLUTO_USER=root
PLUTO_PASS=analog  # Use sshpass or key auth for security

# Host MTU (replace enp1s0 with your Pluto interface)
HOST_MTU=$(ip link show enp1s0 | grep mtu | awk '{print $5}')
echo "Host MTU: $HOST_MTU"

# Pluto MTU (vian SSH)
PLUTO_MTU=$(sshpass -p "$PLUTO_PASS" ssh $PLUTO_USER@$PLUTO_IP "ip link show usb0 | grep mtu | awk '{print \$5}'")
echo "Pluto MTU: $PLUTO_MTU"

# Path MTU test for 9000
if ping -M do -s 8972 -c 1 $PLUTO_IP > /dev/null 2>&1; then
  echo "Path supports MTU 9000+"
else
  echo "Path MTU < 9000 (mismatch likely)"
fi