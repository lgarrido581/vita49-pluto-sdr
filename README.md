# VITA49 Pluto Streamer

**Turn your ADALM-Pluto SDR into a networked VITA49 streaming server**

Stream IQ samples from Pluto to your PC over Ethernet/WiFi using the VITA49 standard protocol. The streamer runs directly on Pluto's ARM processor with minimal footprint, while your applications run on a host PC.

## Features

- **Minimal Footprint**: 50 KB binary, 2 MB RAM, 20-30% CPU on Pluto ARM
- **No Dependencies**: Only requires libiio (already on Pluto firmware)
- **Network Control**: Configure Pluto remotely via VITA49 packets - no SSH needed
- **Multiple Receivers**: Unlimited simultaneous receivers on the same stream
- **Standards Compliant**: Full VITA 49.0 implementation (Signal Data + Context packets)
- **Cross-Platform**: Build on Linux, macOS, Windows (via WSL/Docker)
- **🆕 MTU Optimization**: Automatically sizes packets to prevent IP fragmentation (standard/jumbo frames)

## Quick Start

### Step 1: Build the Streamer

**Using Docker (Recommended - Works on All Platforms):**

```bash
# Windows
.\scripts\build-with-docker.bat

# Linux/macOS
./scripts/build-with-docker.sh
```

**Or Build Natively (Linux Only):**
```bash
make cross
```

### Step 2: Deploy to Pluto

**Copy the binary to Pluto:**
```bash
# Using SCP (if available)
scp vita49_streamer root@pluto.local:/root/
# or following:
scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null vita49_streamer root@pluto.local:/root/

# Or use WinSCP/MobaXterm for GUI file transfer
# Connect to pluto.local, user: root, password: analog
```

**Start the streamer on Pluto:**
```bash
# SSH to Pluto
ssh root@pluto.local
# Password: analog

# Make executable and run in background
chmod +x vita49_streamer

# Run with default settings (Standard Ethernet MTU 1500)
./vita49_streamer &

# OR: Run with jumbo frames for maximum performance
./vita49_streamer --jumbo &

# OR: Run with custom MTU (e.g., for PPPoE)
./vita49_streamer --mtu 1492 &

# Verify it's running
ps | grep vita49
```

**Output when streamer starts (with MTU optimization):**
```
========================================
VITA49 Standalone Streamer for Pluto
========================================
MTU: 1500 bytes
Samples per packet: 364
VITA49 packet size: 1480 bytes
UDP datagram size: 1480 bytes
✓ Packet fits in MTU (efficiency: 98.7%)

IIO context created
Control port: 4990
Data port: 4991
```

### Step 3: Install Python Library (First Time Only)

Install the VITA49 Python library in development mode:

```bash
# From the project root directory
pip install -e .
```

This makes the `vita49` package available to all scripts and examples.

### Step 4: Configure Pluto from Your PC

**Why this is needed:** The config client tells Pluto:
- What frequency/sample rate/gain to use
- **Your PC's IP address** (so Pluto knows where to send data)
- Registers you as a subscriber to receive the stream

```bash
python src/vita49/config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6 --gain 40
```

**What happens:**
- Sends VITA49 Context packet to Pluto's port 4990
- Pluto reconfigures the AD9361 SDR
- Pluto adds your PC to subscriber list
- Pluto starts streaming IQ data to your PC on port 4991

### Step 5: Receive and Visualize Data

```bash
python tests/e2e/step3_plotting_receiver.py --port 4991
```

**Now you'll see:**
- Real-time FFT spectrum
- Time domain I/Q waveforms
- Waterfall spectrogram
- Stream statistics

Done! Your Pluto is streaming VITA49 IQ data over the network.

## Architecture

```
┌──────────────────────────────────────┐
│   Pluto+ SDR (ARM Processor)         │
│   Running: vita49_streamer (50 KB)   │
│   • Port 4990: Config packets        │
│   • Port 4991: IQ data stream        │
└──────────────────────────────────────┘
         │              │
   Config │              │ IQ Stream
         ▼              ▼
┌─────────────────────────────────────────┐
│        Your PC / Network                │
└─────────────────────────────────────────┘
   │           │           │           │
   ▼           ▼           ▼           ▼
Config      Plotter    Detector   Your App
Client      (FFT)      (Energy)   (Custom)
```

## What's Included

| Component | Description |
|-----------|-------------|
| **C Streamer** | Minimal streamer for Pluto ARM (recommended) |
| **Python Library** | VITA49 packet encoder/decoder for PC |
| **Config Client** | Remote configuration tool |
| **Test Suite** | End-to-end tests and examples |
| **Examples** | Plotting receiver, signal processing harness |
| **Build Scripts** | Cross-platform build and deployment |

## Web UI

A modern browser-based interface is available for real-time spectrum visualization and control.

### Running the Web UI

**IMPORTANT: You need to run TWO servers simultaneously:**

**Terminal 1 - Backend Server (FastAPI):**
```bash
python -m vita49.web_server --host 0.0.0.0 --port 8001
```

**Terminal 2 - Frontend Dev Server (Vite):**
```bash
cd src/vita49/web
npm install  # First time only
npm run dev
```

**Access the UI:**
- Open browser to http://localhost:3000
- The Vite dev server (port 3000) proxies API calls to the backend (port 8001)

**Common Error:**
If you see `ECONNREFUSED` errors in the browser console, it means the backend server (port 8001) is not running. Make sure both servers are running in separate terminals.

See **[QUICKSTART_WEB_UI.md](QUICKSTART_WEB_UI.md)** for detailed setup instructions.

## Documentation

- **[Quick Start Guide](docs/USAGE.md)** - Deploy and use the streamer
- **[Build Guide](docs/BUILD.md)** - Build for all platforms (Linux/macOS/Windows)
- **[Development Guide](docs/DEVELOPMENT.md)** - Architecture, testing, contributing
- **[Packet Optimization Guide](docs/PACKET_OPTIMIZATION.md)** - MTU optimization and performance tuning
- **[Web UI Quick Start](QUICKSTART_WEB_UI.md)** - Browser-based interface setup

## Installation

### Prerequisites

**On your PC (for building):**
- Python 3.7+ with pip
- ARM cross-compiler OR Docker OR WSL
- make
- SSH/SCP client

**On Pluto (runtime):**
- libiio (pre-installed on Pluto+ firmware)
- Nothing else!

### Python Library Setup

After cloning the repository, install the VITA49 Python library:

```bash
# Install in editable/development mode
pip install -e .

# Or install with testing dependencies
pip install -e ".[dev]"
```

This allows you to:
- Run all example scripts and receivers
- Import `vita49` from anywhere
- Modify the library code without reinstalling

### Build Methods

**Method 1: Docker (Recommended - All Platforms)**

Works on Windows, macOS, and Linux without installing toolchains:

```bash
# Windows
.\scripts\build-with-docker.bat

# Linux/macOS/WSL
./scripts/build-with-docker.sh
```

**Method 2: Native Toolchain (Linux)**

```bash
sudo apt-get install gcc-arm-linux-gnueabihf libiio-dev make
make deploy
```

**Method 3: WSL (Windows Alternative)**

```powershell
wsl --install
# Then follow Linux instructions in WSL
```

See **[docs/BUILD.md](docs/BUILD.md)** for detailed instructions.

## Usage Examples

### WiFi Signal Analysis (2.4 GHz)

```bash
python vita49_config_client.py --pluto pluto.local --freq 2.437e9 --rate 20e6 --gain 30
python tests/e2e/test_plotting_receiver.py --port 4991
```

### FM Radio Reception (103.7 MHz)

```bash
python vita49_config_client.py --pluto pluto.local --freq 103.7e6 --rate 2e6 --gain 40
python tests/e2e/test_plotting_receiver.py --port 4991
```

### Multiple Receivers in Parallel

```bash
# Terminal 1: Spectrum plot
python tests/e2e/test_plotting_receiver.py --port 4991

# Terminal 2: Energy detector
python examples/signal_processing_harness.py --port 4991

# Terminal 3: Your custom app
python your_app.py --port 4991
```

All receive the **same** stream simultaneously!

## Custom Receiver Example

```python
from vita49.stream_server import VITA49StreamClient
import numpy as np

class MyReceiver:
    def __init__(self):
        self.client = VITA49StreamClient(port=4991)
        self.client.on_samples(self.process_samples)

    def process_samples(self, packet, samples):
        # Your DSP here!
        power_dbfs = 10 * np.log10(np.mean(np.abs(samples)**2))
        print(f"Power: {power_dbfs:.1f} dBFS")

    def start(self):
        self.client.start()

# Run it
receiver = MyReceiver()
receiver.start()
```

## Performance

Tested on ADALM-Pluto:

| Metric | Value |
|--------|-------|
| **Binary Size** | 50 KB (300x smaller than Python) |
| **RAM Usage** | 2 MB (vs 15 MB for Python) |
| **CPU Usage** | 20-30% at 30 MSPS |
| **Latency** | 1-2 ms (UDP + buffering) |
| **Sample Rate** | 2-61 MSPS (AD9361 range) |
| **Network Bandwidth** | ~240 Mbps @ 30 MSPS |

## MTU Optimization

The streamer automatically optimizes UDP packet sizes to prevent IP fragmentation and maximize throughput.

### Command-Line Options

```bash
./vita49_streamer              # Standard Ethernet (MTU 1500)
./vita49_streamer --jumbo      # Jumbo frames (MTU 9000)
./vita49_streamer --mtu 1492   # Custom MTU (e.g., PPPoE)
./vita49_streamer --help       # Show all options
```

### Performance by MTU

| MTU Type | MTU (bytes) | Samples/Packet | Efficiency | Packets/sec @ 30 MSPS |
|----------|-------------|----------------|------------|-----------------------|
| Standard | 1500 | 364 | 98.7% | 82,418 |
| Jumbo | 9000 | 2244 | 99.9% | 13,369 |

**Benefits of Jumbo Frames:**
- 6x fewer packets per second (reduced CPU overhead)
- 20x less packet overhead (99.9% vs 98.7% efficiency)
- No IP fragmentation (improved reliability)
- Lower latency (fewer interrupt handlers)

**Requirements for Jumbo Frames:**
- All network equipment must support MTU 9000
- Configure network interface: `ip link set eth0 mtu 9000`
- Test path support: `ping -M do -s 8972 <destination>`

See [docs/PACKET_OPTIMIZATION.md](docs/PACKET_OPTIMIZATION.md) for detailed information.

## Repository Structure

```
vita49-pluto/
├── src/
│   ├── pluto_vita49_streamer.c    # C streamer (for Pluto)
│   ├── vita49/                     # Python VITA49 library
│   └── streamers/                  # Alternative Python streamers
├── examples/                       # Example receivers
├── tests/                          # Test suite
├── scripts/                        # Build and deployment scripts
├── docs/                           # Documentation
│   ├── BUILD.md
│   ├── USAGE.md
│   └── DEVELOPMENT.md
├── Makefile                        # Build system
└── README.md                       # This file
```

## Auto-Start on Boot

Make Pluto start streaming automatically:

```bash
ssh root@pluto.local
cat >> /etc/rc.local << 'EOF'
/root/vita49_streamer &
EOF
chmod +x /etc/rc.local
```

Now Pluto streams on boot!

## Troubleshooting

### Can't connect to Pluto

```bash
ping pluto.local
ping 192.168.2.1
ssh root@pluto.local  # Password: analog
```

### No data received on PC

```bash
# Check streamer is running
ssh root@pluto.local 'ps | grep vita49'

# Send config to register as subscriber
python vita49_config_client.py --pluto pluto.local --freq 2.4e9

# Check firewall
sudo ufw allow 4991/udp  # Linux
```

See **[docs/USAGE.md](docs/USAGE.md#troubleshooting)** for more help.

## Development

### Running Tests

```bash
# Unit tests
pytest tests/ -v

# End-to-end test (requires Pluto hardware)
python tests/e2e/test_full_pipeline.py --pluto-uri ip:192.168.2.1
```

### Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new features
4. Update documentation
5. Submit a pull request

See **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** for development guidelines.

## Specifications

| Parameter | Range |
|-----------|-------|
| **Frequency** | 70 MHz - 6 GHz |
| **Sample Rate** | 2.084 - 61.44 MSPS |
| **Gain** | 0 - 73 dB |
| **Ports** | 4990 (config), 4991 (data) |
| **Protocol** | VITA 49.0 |
| **Transport** | UDP over Ethernet/WiFi |

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with [libiio](https://wiki.analog.com/resources/tools-software/linux-software/libiio)
- VITA 49.0 standard by [VITA](https://www.vita.com)
- Designed for [ADALM-Pluto SDR](https://www.analog.com/en/design-center/evaluation-hardware-and-software/evaluation-boards-kits/adalm-pluto.html)

## Support

- **Documentation**: See [docs/](docs/) directory
- **Issues**: Open an issue on GitHub
- **Questions**: Check [docs/USAGE.md](docs/USAGE.md) FAQ section

---

**Ready to stream?** Follow the [Quick Start Guide](docs/USAGE.md)
