# VITA49 Pluto Streamer

**Turn your ADALM-Pluto SDR into a networked VITA49 streaming server**

Stream IQ samples from Pluto to your PC over Ethernet/WiFi using the VITA49 standard protocol. The streamer runs directly on Pluto's ARM processor with minimal footprint, while your applications run on a host PC.

## Features

- **Ready to Use**: Pre-compiled binary included - no build required
- **Web Interface**: Modern browser-based spectrum analyzer and control panel
- **Minimal Footprint**: 50 KB binary, 2 MB RAM, 20-30% CPU on Pluto ARM
- **Network Control**: Configure Pluto remotely via VITA49 packets - no SSH needed
- **Multiple Receivers**: Unlimited simultaneous receivers on the same stream
- **Standards Compliant**: Full VITA 49.0 implementation (Signal Data + Context packets)

## Quick Start (3 Steps)

### Prerequisites

- ADALM-Pluto SDR connected via USB/Ethernet
- Python 3.7+ with pip
- Node.js and npm (for Web UI)

### Step 1: Install Python Library

```bash
# Clone the repository
git clone <repository-url>
cd vita49-pluto

# Install Python package
pip install -e .
```

### Step 2: Deploy to Pluto

The pre-compiled streamer binary is included. Just deploy it:

```bash
# Windows
.\scripts\deploy_to_pluto.bat

# Linux/macOS
./scripts/deploy_to_pluto.sh
```

This automatically copies the binary to Pluto and starts it. The streamer will:
- Listen on port 4990 for configuration
- Stream IQ data on port 4991
- Run in background on Pluto

### Step 3: Start Web UI

```bash
# Windows
.\scripts\start-webui.bat

# Linux/macOS
./scripts/start-webui.sh
```

This automatically:
- Starts the backend server (port 8001)
- Starts the frontend dev server (port 3000)
- Opens your browser to http://localhost:3000

**You're done!** Use the web interface to configure frequency, sample rate, gain, and view real-time spectrum.

---

## Web Interface Features

- **Real-time FFT Spectrum**: Live frequency domain visualization
- **Waterfall Display**: Time-frequency spectrogram
- **Configuration Panel**: Adjust frequency, sample rate, gain, bandwidth
- **Stream Statistics**: Monitor data rate, packet counts, performance
- **Multiple Receivers**: Configure and manage multiple subscribers

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

## Advanced Usage

### Command Line Receiver

For command-line spectrum visualization:

```bash
python tests/e2e/step3_plotting_receiver.py --port 4991
```

Shows real-time FFT spectrum, waterfall, and I/Q plots.

### Multiple Receivers

Multiple applications can receive the same stream simultaneously:

```bash
# Terminal 1: Web UI
.\scripts\start-webui.bat

# Terminal 2: Command-line plotter
python tests/e2e/step3_plotting_receiver.py --port 4991

# Terminal 3: Custom processing
python examples/signal_processing_harness.py --port 4991
```

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

## Building from Source (Optional)

The pre-compiled binary works for most users. To rebuild:

```bash
# Using Docker (all platforms)
.\scripts\build-with-docker.bat  # Windows
./scripts/build-with-docker.sh   # Linux/macOS

# Or native toolchain (Linux only)
make cross
```

See **[docs/BUILD.md](docs/BUILD.md)** for detailed build instructions.

## Documentation

- **[Web UI Guide](docs/QUICKSTART_WEB_UI.md)** - Detailed Web UI setup and troubleshooting
- **[Usage Guide](docs/USAGE.md)** - Advanced usage, troubleshooting, configuration
- **[Build Guide](docs/BUILD.md)** - Building from source (for developers)
- **[Development Guide](docs/DEVELOPMENT.md)** - Architecture, testing, contributing
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** - Common issues and solutions

## Performance

| Metric | Value |
|--------|-------|
| **Binary Size** | 50 KB |
| **RAM Usage** | 2 MB on Pluto ARM |
| **CPU Usage** | 20-30% at 30 MSPS |
| **Sample Rate** | 2-61 MSPS |
| **Bandwidth** | ~240 Mbps @ 30 MSPS |

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

- **Issues**: Open an issue on GitHub
- **Questions**: See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- **Development**: See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
