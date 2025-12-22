# VITA49 Pluto Streamer - Usage Guide

Complete guide for deploying and using the VITA49 streamer with ADALM-Pluto SDR.

## Quick Start (4 Steps)

```bash
# 1. Install Python library (first time only)
pip install -e .

# 2. Build and deploy to Pluto
make deploy

# 3. Configure from your PC
python src/vita49/config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6 --gain 40

# 4. Receive data on your PC
python examples/signal_processing_harness.py --port 4991
```

Done! Your Pluto is streaming VITA49 IQ data over the network.

---

## What You Get

This setup provides:
- **Pluto autonomy**: SDR runs independently on its ARM processor
- **Network configuration**: Control SDR via VITA49 packets (no SSH needed!)
- **Parallel receivers**: Multiple applications can receive the same stream
- **Minimal footprint**: 50 KB binary, 2 MB RAM, no Python required on Pluto
- **Easy deployment**: Copy one file and run

---

## Architecture Overview

```
┌──────────────────────────────────────┐
│   Pluto+ SDR (ARM Processor)         │
│   Running: vita49_streamer           │
│   • Port 4990: Config packets (in)   │
│   • Port 4991: IQ data (out)        │
└──────────────────────────────────────┘
         │              │
   Config │              │ Data Stream
         ▼              ▼
┌─────────────────────────────────────────┐
│        Your PC / Network                │
└─────────────────────────────────────────┘
   │           │           │           │
   ▼           ▼           ▼           ▼
Config      Plotter    Detector   Classifier
Client      (FFT)      (Energy)   (Custom ML)
```

---

## Deployment

### Option 1: Automated Deployment (Recommended)

**Linux/macOS:**
```bash
chmod +x scripts/deploy_to_pluto.sh
./scripts/deploy_to_pluto.sh pluto.local
```

**Windows (PowerShell):**
```powershell
.\scripts\deploy_to_pluto.bat pluto.local
```

**Windows (WSL):**
```bash
bash scripts/deploy_to_pluto.sh pluto.local
```

### Option 2: Make Deploy

```bash
# Default (assumes pluto.local)
make deploy

# Custom IP
make deploy PLUTO_IP=192.168.2.1
```

### Option 3: Manual Deployment

```bash
# Copy binary to Pluto
scp vita49_streamer root@pluto.local:/root/

# SSH to Pluto
ssh root@pluto.local
# Password: analog

# Make executable
chmod +x vita49_streamer

# Run
./vita49_streamer
```

---

## Running the Streamer

### On Pluto (Basic)

```bash
ssh root@pluto.local
./vita49_streamer
```

The streamer will:
- Listen for configuration on UDP port **4990**
- Stream IQ samples on UDP port **4991**
- Auto-discover receivers when they send config packets
- Maintain a list of active subscribers

**Output:**
```
VITA49 Streamer Started
Listening for config on port 4990
Ready to stream on port 4991
```

### Run in Background

```bash
ssh root@pluto.local './vita49_streamer &'
```

### Check if Running

```bash
ssh root@pluto.local 'ps | grep vita49'
```

### Stop the Streamer

```bash
ssh root@pluto.local 'killall vita49_streamer'
```

---

## Configuration from PC

Instead of SSH-ing to Pluto to change parameters, use the VITA49 config client from your PC.

### Basic Configuration

```bash
# Full configuration
python vita49_config_client.py --pluto pluto.local \
    --freq 5.8e9 --rate 20e6 --gain 30

# Quick frequency change
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# Adjust gain only
python vita49_config_client.py --pluto pluto.local --gain 50
```

### Configuration Options

```
--pluto, -p      Pluto hostname or IP (default: pluto.local)
--freq, -f       Center frequency in Hz (default: 2.4e9)
--rate, -r       Sample rate in Hz (default: 30e6)
--gain, -g       RX gain in dB (default: 20)
--port           Config port (default: 4990)
```

### How It Works

The config client:
1. Sends a VITA49 Context packet to Pluto's config port (4990)
2. Pluto receives the config and updates SDR parameters
3. Pluto adds your PC's IP to its subscriber list
4. Pluto starts streaming IQ data to your PC on port 4991

---

## Receiving Data on PC

### Example 1: Real-Time Spectrum Plotter

```bash
python tests/e2e/test_plotting_receiver.py --port 4991
```

Shows:
- Time domain I/Q waveforms
- Real-time FFT spectrum
- Waterfall spectrogram
- Stream statistics

### Example 2: Signal Processing Harness

```bash
python examples/signal_processing_harness.py --port 4991 --threshold -20
```

Performs:
- Energy detection
- Signal classification
- Event logging

### Example 3: Multiple Receivers in Parallel

All receivers can listen simultaneously:

```bash
# Terminal 1: Spectrum analyzer
python tests/e2e/test_plotting_receiver.py --port 4991

# Terminal 2: Energy detector
python examples/signal_processing_harness.py --port 4991

# Terminal 3: Your custom receiver
python your_receiver.py --port 4991
```

All receive the **same** IQ stream from Pluto!

---

## Common Use Cases

### WiFi Signal Analysis (2.4 GHz)

```bash
# Configure Pluto for WiFi band
python vita49_config_client.py --pluto pluto.local \
    --freq 2.437e9 --rate 20e6 --gain 30

# Run spectrum analyzer
python tests/e2e/test_plotting_receiver.py --port 4991
```

### FM Radio Reception (88-108 MHz)

```bash
# Configure for FM radio at 103.7 MHz
python vita49_config_client.py --pluto pluto.local \
    --freq 103.7e6 --rate 2e6 --gain 40

# Receive and visualize
python tests/e2e/test_plotting_receiver.py --port 4991
```

### ISM Band Monitoring (915 MHz)

```bash
python vita49_config_client.py --pluto pluto.local \
    --freq 915e6 --rate 10e6 --gain 35

python tests/e2e/test_plotting_receiver.py --port 4991
```

### Frequency Scanning

```bash
# Scan multiple frequencies
for freq in 2.4e9 2.45e9 2.5e9; do
    python vita49_config_client.py --pluto pluto.local --freq $freq
    sleep 5  # Collect 5 seconds at each frequency
done
```

---

## Auto-Start on Boot

Make the streamer start automatically when Pluto powers on.

### Method 1: Using rc.local

```bash
# SSH to Pluto
ssh root@pluto.local

# Add to startup script
cat >> /etc/rc.local << 'EOF'
# Start VITA49 streamer on boot
/root/vita49_streamer &
EOF

# Make executable
chmod +x /etc/rc.local

# Reboot to test
reboot
```

### Method 2: Using systemd (if available)

```bash
# Copy service file to Pluto
scp systemd/vita49.service root@pluto.local:/etc/systemd/system/

# On Pluto
ssh root@pluto.local
systemctl daemon-reload
systemctl enable vita49.service
systemctl start vita49.service

# Check status
systemctl status vita49.service

# View logs
journalctl -u vita49.service -f
```

### Verify Auto-Start

```bash
# Reboot Pluto
ssh root@pluto.local 'reboot'

# Wait 30 seconds, then check
ssh root@pluto.local 'ps | grep vita49'
```

---

## Network Configuration

### Multiple PCs Receiving from One Pluto

Pluto maintains a subscriber list. Any PC that sends a config packet gets added:

```bash
# PC 1
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# PC 2 (different machine)
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# Both PCs now receive the stream!
```

### Changing Destination IP

The Pluto automatically sends data to the IP address that sent the config packet.

**Option 1:** Send config from the PC you want to receive on
```bash
# Run this on the PC that should receive data
python vita49_config_client.py --pluto pluto.local --freq 2.4e9
```

**Option 2:** Restart streamer with fixed destination
```bash
ssh root@pluto.local 'killall vita49_streamer'
ssh root@pluto.local './vita49_streamer &'
# Then send config from desired PC
```

### Firewall Configuration

**Linux:**
```bash
sudo ufw allow 4991/udp  # Data port
sudo ufw allow 4990/udp  # Config port
```

**Windows:**
```powershell
New-NetFirewallRule -DisplayName "VITA49 Data" -Direction Inbound -Protocol UDP -LocalPort 4991 -Action Allow
New-NetFirewallRule -DisplayName "VITA49 Config" -Direction Inbound -Protocol UDP -LocalPort 4990 -Action Allow
```

### Increase UDP Buffer (for high sample rates)

**Linux:**
```bash
sudo sysctl -w net.core.rmem_max=26214400
sudo sysctl -w net.core.rmem_default=26214400
```

**macOS:**
```bash
sudo sysctl -w kern.ipc.maxsockbuf=8388608
sudo sysctl -w net.inet.udp.recvspace=2097152
```

---

## Performance Tuning

### Reduce CPU Usage on Pluto

```bash
# Lower sample rate
python vita49_config_client.py --pluto pluto.local --rate 10e6

# Note: Packet size is fixed in C version
```

### Reduce Network Bandwidth

```bash
# Lower sample rate reduces bandwidth
python vita49_config_client.py --pluto pluto.local --rate 10e6

# Sample rate vs bandwidth:
# 10 MSPS → ~80 Mbps
# 20 MSPS → ~160 Mbps
# 30 MSPS → ~240 Mbps
```

### Optimize for Weak Signals

```bash
# Increase gain (max ~73 dB)
python vita49_config_client.py --pluto pluto.local --gain 60

# Lower sample rate for better SNR
python vita49_config_client.py --pluto pluto.local --rate 5e6 --gain 50
```

### Optimize for Low Latency

The C implementation already has minimal latency (~1-2 ms). Network and buffering on the receiver side dominate latency.

---

## Specifications

### Sample Rate Range
- **Minimum:** 2.084 MSPS (AD9361 limit)
- **Maximum:** 61.44 MSPS (AD9361 limit)
- **Default:** 30 MSPS

### Frequency Range
- **Minimum:** 70 MHz
- **Maximum:** 6 GHz
- **Default:** 2.4 GHz

### Gain Range
- **Minimum:** 0 dB
- **Maximum:** ~73 dB (manual mode)
- **Default:** 20 dB

### Network
- **Data port:** 4991 (UDP)
- **Config port:** 4990 (UDP)
- **Bandwidth:** ~8 bits/sample × sample_rate
  - 30 MSPS = ~240 Mbps
  - 20 MSPS = ~160 Mbps
  - 10 MSPS = ~80 Mbps

### Performance (on Pluto ARM)
- **CPU usage:** 20-30% at 30 MSPS
- **RAM usage:** ~2 MB
- **Latency:** 1-2 ms (UDP + buffer)

---

## Troubleshooting

### Can't Connect to Pluto

```bash
# Check Pluto is reachable
ping pluto.local
ping 192.168.2.1

# Test SSH
ssh root@pluto.local
# Password: analog

# Check firewall
sudo ufw status  # Linux
```

### No Data Received on PC

```bash
# 1. Check streamer is running on Pluto
ssh root@pluto.local 'ps | grep vita49'

# 2. Send config to register as subscriber
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# 3. Check firewall allows UDP 4991
sudo ufw allow 4991/udp

# 4. Verify receiver is listening
netstat -an | grep 4991  # Linux/macOS
netstat -an | findstr 4991  # Windows
```

### Pluto Not Reconfiguring

```bash
# Restart the streamer
ssh root@pluto.local 'killall vita49_streamer'
ssh root@pluto.local './vita49_streamer &'

# Send config again
python vita49_config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6
```

### High Packet Loss

**Solutions:**
1. Increase UDP receive buffer (see Network Configuration)
2. Reduce sample rate
3. Use wired Ethernet instead of WiFi
4. Check CPU usage on receiver PC
5. Close other network-intensive applications

### Streamer Crashes on Pluto

```bash
# Check logs
ssh root@pluto.local 'dmesg | tail'

# Verify binary is correct architecture
file vita49_streamer  # Should show ARM

# Check libiio is installed
ssh root@pluto.local 'opkg list-installed | grep libiio'
```

---

## Advanced Usage

### Custom Receiver Implementation

Create your own receiver using the VITA49 library:

```python
#!/usr/bin/env python3
from vita49.stream_server import VITA49StreamClient
import numpy as np

class MyReceiver:
    def __init__(self, port=4991):
        self.client = VITA49StreamClient(port=port)
        self.client.on_samples(self.process_samples)
        self.client.on_context(self.process_context)

    def process_context(self, context_data):
        """Called when config packet arrives"""
        from vita49.packets import VRTContextPacket
        ctx = VRTContextPacket.decode(context_data)
        print(f"Config: {ctx.sample_rate_hz/1e6:.1f} MSPS @ {ctx.rf_reference_frequency_hz/1e9:.3f} GHz")

    def process_samples(self, packet, samples):
        """Called for each IQ data packet"""
        # Your DSP here!
        power_dbfs = 10 * np.log10(np.mean(np.abs(samples)**2))
        print(f"Power: {power_dbfs:.1f} dBFS")

    def start(self):
        self.client.start()

if __name__ == '__main__':
    receiver = MyReceiver()
    receiver.start()
```

### Remote Pluto Access

If your Pluto is on a different network:

```bash
# SSH tunnel for config port
ssh -L 4990:localhost:4990 root@remote-pluto-ip

# Configure through tunnel
python vita49_config_client.py --pluto localhost --freq 2.4e9

# For data, you'll need VPN or port forwarding
```

---

## FAQ

### Q: Does this work offline?

**A: YES!** The streamer has zero external dependencies except libiio (already on Pluto). No internet needed.

### Q: Can multiple people receive from one Pluto?

**A: YES!** Pluto maintains a subscriber list. Anyone who sends a config packet gets added and receives the stream.

### Q: What's the latency?

**A:** ~1-2 ms (UDP transmission + buffering). Very low latency for real-time applications.

### Q: Does it work with standard Pluto or only Pluto+?

**A:** Works with both! The firmware just needs to have libiio support.

### Q: Can I stream over WiFi?

**A:** Yes, but wired Ethernet is recommended for high sample rates (>10 MSPS) to avoid packet loss.

### Q: How do I update to a new version?

```bash
# Rebuild and redeploy
make deploy

# Or manually copy new binary
scp vita49_streamer root@pluto.local:/root/
```

---

## Next Steps

- **Build custom receivers:** See examples/ directory
- **Run tests:** See [docs/DEVELOPMENT.md](DEVELOPMENT.md)
- **Understand architecture:** See [docs/DEVELOPMENT.md](DEVELOPMENT.md)
- **Contribute:** See [CONTRIBUTING.md](../CONTRIBUTING.md)

---

## Additional Resources

- [BUILD.md](BUILD.md) - How to build the streamer
- [DEVELOPMENT.md](DEVELOPMENT.md) - Architecture and testing
- [Main README](../README.md) - Project overview
- [VITA 49.0 Specification](https://www.vita.com) - Protocol details
