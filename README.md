# VITA 49 Embedded Streamer for Pluto+ ARM

Minimal-footprint VITA 49 IQ streaming implementation designed to run 
directly on the ADALM-Pluto+ Zynq ARM Cortex-A9 processor.

## Features

- **Minimal Dependencies**: Only requires numpy (no scipy, no asyncio)
- **Low Memory**: ~15 MB total footprint
- **Low CPU**: 20-30% at 30 MSPS single channel
- **Direct IIO**: Uses local IIO when running on-device
- **Standards Compliant**: VITA 49.0 Signal Data + Context packets

## Quick Start

### 1. Deploy to Pluto+

```bash
# Make deployment script executable
chmod +x deploy_to_pluto.sh

# Deploy (uses defaults: Pluto at 192.168.2.1, stream to 192.168.2.100)
./deploy_to_pluto.sh

# Or specify IPs
./deploy_to_pluto.sh 192.168.2.1 10.0.0.50
```

### 2. Manual Deployment

```bash
# Copy the embedded streamer
scp vita49_embedded.py root@192.168.2.1:/root/

# SSH to Pluto+
ssh root@192.168.2.1

# Run
cd /root
python3 vita49_embedded.py --uri local --dest 192.168.2.100
```

### 3. Receive on Host

```bash
# Using the signal processing harness from the main library
python3 signal_processing_harness.py --port 4991

# Or use a simple UDP receiver
python3 -c "
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', 4991))
while True:
    data, addr = sock.recvfrom(65536)
    print(f'Received {len(data)} bytes from {addr}')
"
```

## Command Line Options

```
usage: vita49_embedded.py [-h] [--uri URI] --dest DEST [--port PORT]
                          [--freq FREQ] [--rate RATE] [--gain GAIN]
                          [--channels CHANNELS [CHANNELS ...]]
                          [--buffer BUFFER] [--pkt-size PKT_SIZE]

Options:
  --uri, -u       SDR URI (default: ip:192.168.2.1, use 'local' on-device)
  --dest, -d      Destination IP for UDP stream (required)
  --port, -p      Base UDP port (default: 4991)
  --freq, -f      Center frequency in Hz (default: 2.4e9)
  --rate, -r      Sample rate in Hz (default: 30e6)
  --gain, -g      RX gain in dB (default: 20)
  --channels, -c  RX channels: 0, 1, or "0 1" for both (default: 0)
  --buffer        IIO buffer size in samples (default: 16384)
  --pkt-size      Samples per VRT packet (default: 360)
```

## Examples

```bash
# Basic streaming (on Pluto+)
python3 vita49_embedded.py --uri local --dest 192.168.2.100

# Dual channel at 5.8 GHz
python3 vita49_embedded.py --uri local --dest 192.168.2.100 \
    --freq 5.8e9 --channels 0 1

# Lower CPU usage (reduce sample rate)
python3 vita49_embedded.py --uri local --dest 192.168.2.100 \
    --rate 10e6

# Higher SNR (increase gain)
python3 vita49_embedded.py --uri local --dest 192.168.2.100 \
    --gain 40
```

## Auto-Start on Boot

To automatically start streaming when the Pluto+ powers on:

```bash
# Copy service file
scp vita49.service root@pluto.local:/etc/systemd/system/

# On Pluto+:
ssh root@pluto.local
systemctl daemon-reload
systemctl enable vita49
systemctl start vita49

# Check status
systemctl status vita49
journalctl -u vita49 -f
```

## Performance Tuning

### Reduce CPU Usage

```bash
# Lower sample rate
--rate 10e6    # 10 MSPS instead of 30 MSPS

# Larger packets (fewer packets/sec)
--pkt-size 720

# Larger buffer (less frequent DMA)
--buffer 32768
```

### Reduce Latency

```bash
# Smaller buffer
--buffer 8192

# Smaller packets
--pkt-size 180
```

### Network Optimization

On the receiving host, increase UDP buffer:
```bash
sudo sysctl -w net.core.rmem_max=26214400
sudo sysctl -w net.core.rmem_default=26214400
```

## Packet Format

Each UDP packet contains a VITA 49.0 Signal Data Packet:

```
Offset  Size    Field
------  ----    -----
0       4       Header (packet type, flags, size)
4       4       Stream ID
8       4       Integer Timestamp (UTC seconds)
12      8       Fractional Timestamp (picoseconds)
20      N*4     Payload (int16 I/Q pairs, big-endian)
20+N*4  4       Trailer (valid data indicator)
```

Context packets (sent every 100 data packets) contain:
- Sample rate
- Center frequency
- Bandwidth
- Gain

## Troubleshooting

### "pyadi-iio not found"

```bash
# On Pluto+:
pip3 install pyadi-iio --break-system-packages
# or
opkg update && opkg install python3-pyadi-iio
```

### "numpy not found"

```bash
# On Pluto+:
opkg update && opkg install python3-numpy
```

### No packets received on host

1. Check firewall allows UDP port 4991
2. Verify correct destination IP
3. Check Pluto+ network connectivity: `ping 192.168.2.100`
4. Verify streaming is running: check output on Pluto+

### High packet loss

1. Increase receive buffer on host (see Network Optimization)
2. Reduce sample rate
3. Use wired Ethernet instead of WiFi
4. Check CPU usage on Pluto+ isn't at 100%

## Integration with Main Library

The embedded streamer generates packets compatible with the full 
`vita49_pluto` library. You can:

1. Receive on host using `VITA49StreamClient`
2. Process with `SignalProcessingHarness`
3. Record and analyze with the detection algorithms

```python
from vita49_stream_server import VITA49StreamClient
from signal_processing_harness import SignalProcessingHarness, EnergyDetector

# Simple receiver
client = VITA49StreamClient(port=4991)
client.start()

# Or full processing
harness = SignalProcessingHarness(port=4991)
harness.add_detector(EnergyDetector(threshold_db=-20))
harness.start()
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Pluto+ ARM Processor               │
│                                                 │
│   ┌─────────────┐                               │
│   │   AD9361    │  RF Frontend                  │
│   └──────┬──────┘                               │
│          │ IIO DMA                              │
│   ┌──────▼──────┐                               │
│   │   libiio    │  Kernel Driver                │
│   └──────┬──────┘                               │
│          │                                      │
│   ┌──────▼──────┐                               │
│   │ pyadi-iio   │  Python Bindings              │
│   └──────┬──────┘                               │
│          │                                      │
│   ┌──────▼──────────────────────────────────┐   │
│   │       vita49_embedded.py                │   │
│   │  ┌────────────┐  ┌──────────────────┐   │   │
│   │  │ VRT Packet │  │ UDP Streamer     │   │   │
│   │  │ Encoder    │  │ (socket.sendto)  │   │   │
│   │  └────────────┘  └──────────────────┘   │   │
│   └─────────────────────────┬───────────────┘   │
│                             │                   │
└─────────────────────────────│───────────────────┘
                              │ UDP/Ethernet
                              ▼
                    ┌─────────────────┐
                    │   Host System   │
                    │ (Processing)    │
                    └─────────────────┘
```

## License

MIT License
