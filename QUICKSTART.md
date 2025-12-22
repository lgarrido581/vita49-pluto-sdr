# VITA49 Pluto Quick Start Guide

## TL;DR - Get Running in 30 Seconds

```bash
# 1. Deploy to Pluto (one file, no dependencies!)
./deploy_to_pluto.sh pluto.local

# 2. Configure and start streaming from your PC
python vita49_config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6 --gain 40

# 3. Run receivers in parallel
python test_e2e_step3_plotting_receiver.py --port 4991
```

Done! Pluto is now streaming VITA49 IQ data to your PC over the network.

---

## What You Get

This setup gives you:
- **Pluto autonomy**: SDR runs independently on its ARM processor
- **Network configuration**: Control SDR via VITA49 packets (no SSH needed!)
- **Parallel receivers**: Run multiple algorithms simultaneously on same stream
- **Zero dependencies**: Single Python file, uses only stdlib + pyadi-iio (pre-installed)
- **Easy deployment**: Copy one file and run

## Architecture

```
┌──────────────────────────────────────┐
│   Pluto+ SDR (ARM Processor)         │
│   Running: pluto_vita49_standalone.py│
│   • Listens for config on port 4990  │
│   • Streams IQ data on port 4991     │
└──────────────────────────────────────┘
         │              │
         │ Config       │ Data
         ▼              ▼
┌─────────────────────────────────────────┐
│        Your PC / Network                │
└─────────────────────────────────────────┘
   │           │           │           │
   ▼           ▼           ▼           ▼
Config      Plotter    Detector   Classifier
Client      (FFT)      (Energy)   (ML)
```

---

## Installation & Deployment

### Option 1: Quick Deploy (Recommended)

**Linux/Mac:**
```bash
chmod +x deploy_to_pluto.sh
./deploy_to_pluto.sh pluto.local
```

**Windows (with WSL):**
```bash
bash deploy_to_pluto.sh pluto.local
```

**Windows (native with PuTTY):**
```batch
deploy_to_pluto.bat pluto.local
```

### Option 2: Manual Deploy

```bash
# Copy single file to Pluto
scp pluto_vita49_standalone.py root@pluto.local:/root/

# SSH to Pluto and run
ssh root@pluto.local
python3 /root/pluto_vita49_standalone.py --dest <your_pc_ip>
```

**Default password:** `analog`

---

## Usage

### Step 1: Start Pluto Streamer

On Pluto (via SSH or startup script):
```bash
python3 /root/pluto_vita49_standalone.py --dest 192.168.2.100
```

Or run remotely from PC:
```bash
ssh root@pluto.local 'python3 /root/pluto_vita49_standalone.py --dest 192.168.2.100' &
```

The Pluto will:
- Listen for configuration on UDP port **4990**
- Stream IQ samples on UDP port **4991**
- Auto-discover receivers when they send config packets

### Step 2: Configure Pluto from PC

Instead of SSH-ing to change parameters, use VITA49 config client:

```bash
# Full configuration
python vita49_config_client.py --pluto pluto.local \
    --freq 5.8e9 --rate 20e6 --gain 30

# Quick frequency change
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# Adjust gain only
python vita49_config_client.py --pluto pluto.local --gain 50
```

Pluto reconfigures instantly and starts streaming to your PC!

### Step 3: Run Receivers

All receivers listen to the same VITA49 stream on port 4991.

#### Plotting Receiver (Visualization)
```bash
python test_e2e_step3_plotting_receiver.py --port 4991
```

Shows real-time:
- Time domain I/Q
- FFT spectrum
- Waterfall spectrogram
- Signal statistics

#### Custom Receivers (Your Algorithms)

Create your own receiver based on the framework:

```python
from vita49_stream_server import VITA49StreamClient

class MyDetector:
    def __init__(self):
        self.client = VITA49StreamClient(port=4991)
        self.client.on_samples(self.process_samples)

    def process_samples(self, packet, samples):
        # Your algorithm here!
        energy = np.mean(np.abs(samples)**2)
        if energy > threshold:
            print(f"Detection! Energy: {energy:.2f}")

    def start(self):
        self.client.start()

# Run it
detector = MyDetector()
detector.start()
```

#### Run Multiple Receivers in Parallel

```bash
# Terminal 1: Plotting
python test_e2e_step3_plotting_receiver.py --port 4991

# Terminal 2: Energy detector
python my_detector.py --port 4991

# Terminal 3: ML classifier
python my_classifier.py --port 4991
```

All receive the **same** IQ stream simultaneously!

---

## FAQ

### Q: Does this work offline?

**A: YES!** The `pluto_vita49_standalone.py` has ZERO external dependencies except:
- Python 3 (included with Pluto firmware)
- pyadi-iio (pre-installed on Pluto+)

No internet needed on Pluto. Just copy the file and run.

### Q: Do I need numpy on Pluto?

**A: NO!** The standalone version uses pure Python for all operations. No numpy required on Pluto.

(The original `vita49_embedded.py` needs numpy, but the new `pluto_vita49_standalone.py` doesn't)

### Q: Can I pre-configure Pluto to start on boot?

**A: YES!** Add to `/etc/rc.local` on Pluto:

```bash
ssh root@pluto.local
echo "python3 /root/pluto_vita49_standalone.py --dest 192.168.2.100 &" >> /etc/rc.local
```

Now Pluto auto-starts streaming on boot!

### Q: How do I change the IP address my Pluto streams to?

**Option 1:** Send config packet from new PC (auto-discovered)
```bash
python vita49_config_client.py --pluto pluto.local --freq 2.4e9
```

**Option 2:** Restart Pluto streamer with new `--dest`
```bash
ssh root@pluto.local 'killall python3'
ssh root@pluto.local 'python3 /root/pluto_vita49_standalone.py --dest 192.168.2.200' &
```

### Q: What's the sample rate / latency?

- **Sample rates:** 2.084 MSPS to 61.44 MSPS (AD9361 limits)
- **Latency:** ~1-2 ms (UDP + buffer latency)
- **Packet size:** 360 samples/packet (adjustable with `--pkt-size`)
- **Network bandwidth:** ~240 Mbps at 30 MSPS (int16 IQ)

### Q: Can multiple people receive from one Pluto?

**A: YES!** Pluto maintains a subscriber list. Anyone who sends a config packet gets added:

```bash
# PC 1
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# PC 2 (different machine)
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# Both PCs now receive the stream!
```

### Q: What if my Pluto doesn't have pyadi-iio?

Most Pluto+ firmware includes it. If not:
```bash
ssh root@pluto.local
pip3 install pyadi-iio --break-system-packages
```

Or use the original `vita49_embedded.py` which checks for dependencies.

---

## Files in This Repo

### For Pluto (Deploy These)

| File | Description | Dependencies |
|------|-------------|--------------|
| `pluto_vita49_standalone.py` | **NEW!** Single-file streamer, no deps | pyadi-iio only |
| `vita49_embedded.py` | Original streamer (uses numpy) | numpy, pyadi-iio |

**Recommendation:** Use `pluto_vita49_standalone.py` for easiest deployment.

### For Your PC (Host Side)

| File | Purpose |
|------|---------|
| `vita49_config_client.py` | Configure Pluto remotely |
| `test_e2e_step3_plotting_receiver.py` | Visualize IQ stream |
| `vita49_stream_server.py` | Client library for receivers |
| `vita49_packets.py` | VITA49 packet encode/decode |

### Deployment Helpers

| File | Purpose |
|------|---------|
| `deploy_to_pluto.sh` | Linux/Mac deploy script |
| `deploy_to_pluto.bat` | Windows deploy script |
| `QUICKSTART.md` | This guide |

---

## Examples

### Example 1: FM Radio Receiver at 103.7 MHz

```bash
# 1. Configure for FM
python vita49_config_client.py --pluto pluto.local \
    --freq 103.7e6 --rate 2e6 --gain 40

# 2. Receive and demodulate
python test_e2e_step3_plotting_receiver.py --port 4991
```

### Example 2: WiFi Signal Analysis at 2.4 GHz

```bash
# 1. Configure for WiFi band
python vita49_config_client.py --pluto pluto.local \
    --freq 2.437e9 --rate 20e6 --gain 30

# 2. Run spectrum analyzer
python test_e2e_step3_plotting_receiver.py --port 4991
```

### Example 3: Scan Multiple Frequencies

```bash
# Configure Pluto to scan
for freq in 2.4e9 2.45e9 2.5e9; do
    python vita49_config_client.py --pluto pluto.local --freq $freq
    sleep 5  # Collect 5 seconds at each frequency
done
```

### Example 4: Parallel Detector + Plotter

```bash
# Terminal 1: Real-time plot
python test_e2e_step3_plotting_receiver.py --port 4991 &

# Terminal 2: Energy detector (your custom code)
python my_energy_detector.py --port 4991 &

# Both run simultaneously on same stream!
```

---

## Troubleshooting

### Can't connect to Pluto

```bash
# Check Pluto is reachable
ping pluto.local

# Try IP directly
ping 192.168.2.1

# Test SSH
ssh root@pluto.local
# Password: analog
```

### No data received on PC

```bash
# 1. Check Pluto is running
ssh root@pluto.local 'ps | grep vita49'

# 2. Send config to register as subscriber
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# 3. Check firewall (allow UDP 4991)
sudo ufw allow 4991/udp  # Linux
```

### Pluto not reconfiguring

```bash
# Restart the streamer
ssh root@pluto.local 'killall python3'
ssh root@pluto.local 'python3 /root/pluto_vita49_standalone.py --dest YOUR_PC_IP' &

# Send config again
python vita49_config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6
```

---

## Advanced: Build Your Own Receiver

See the receiver framework template:

```python
#!/usr/bin/env python3
"""
Custom VITA49 Receiver Template
"""
import numpy as np
from vita49_stream_server import VITA49StreamClient

class MyReceiver:
    def __init__(self, port=4991):
        self.client = VITA49StreamClient(port=port)
        self.client.on_samples(self.process_samples)
        self.client.on_context(self.process_context)

    def process_context(self, context_data):
        """Called when config/context packet arrives"""
        from vita49_packets import VRTContextPacket
        ctx = VRTContextPacket.decode(context_data)
        print(f"Pluto config: {ctx.sample_rate_hz/1e6:.1f} MSPS @ {ctx.rf_reference_frequency_hz/1e9:.3f} GHz")

    def process_samples(self, packet, samples):
        """Called for each IQ data packet"""
        # Your DSP here!
        # samples is numpy array of complex64

        # Example: Compute power
        power_dbfs = 10 * np.log10(np.mean(np.abs(samples)**2))

        # Example: Peak frequency
        spectrum = np.fft.fft(samples)
        peak_bin = np.argmax(np.abs(spectrum))

        print(f"Power: {power_dbfs:.1f} dBFS, Peak bin: {peak_bin}")

    def start(self):
        self.client.start()

if __name__ == '__main__':
    receiver = MyReceiver()
    receiver.start()
```

---

## Support

- Check `test_*.py` scripts for working examples
- See `vita49_packets.py` for packet format details
- Read VITA 49.0 spec for advanced features

**Need help?** Open an issue on GitHub with:
- Pluto firmware version (`ssh root@pluto.local 'cat /etc/version'`)
- Python version (`python3 --version`)
- Error messages

---

## License

MIT - Do whatever you want with this!
