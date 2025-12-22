# VITA49 Pluto Web UI - Complete Guide

Complete guide for setting up and using the web-based interface for VITA49 Pluto SDR streaming.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Features](#features)
- [Advanced Usage](#advanced-usage)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)

## Overview

The VITA49 Pluto Web UI provides a modern, browser-based interface for:
- Configuring your ADALM-Pluto SDR remotely
- Visualizing real-time spectrum and waterfall displays
- Inspecting VITA49 packets
- Monitoring stream statistics
- All without requiring local Python/matplotlib installations

### Architecture

```
┌──────────────────────┐
│   Web Browser        │
│   (Any Device)       │
│   - Spectrum plots   │
│   - Waterfall        │
│   - Controls         │
└──────────────────────┘
         │ HTTP/WebSocket
         ▼
┌──────────────────────┐
│   Backend Server     │
│   (FastAPI/Python)   │
│   - Port 8000        │
└──────────────────────┘
         │ UDP VITA49
         ▼
┌──────────────────────┐
│   Pluto SDR          │
│   (vita49_streamer)  │
│   - Port 4991        │
└──────────────────────┘
```

## Prerequisites

### Hardware
- ADALM-Pluto SDR with vita49_streamer running
- Network connection to Pluto (Ethernet or USB)

### Software - Backend Server
- Python 3.8 or higher
- pip (Python package manager)

### Software - Frontend Development (Optional)
- Node.js 16+ and npm (only needed for development)

### Network
- Pluto accessible at ip:192.168.2.1 (or custom URI)
- Port 8000 available for web server
- Port 4991 available for VITA49 UDP stream

## Installation

### Step 1: Install Python Dependencies

Install the web UI backend dependencies:

```bash
cd C:\git-repos\vita49-pluto
pip install -r requirements-web.txt
```

This installs:
- FastAPI (web framework)
- Uvicorn (ASGI server)
- WebSockets (real-time communication)

### Step 2: Install Frontend Dependencies (Development Only)

If you want to develop or modify the frontend:

```bash
cd src/vita49/web
npm install
```

For production use, you can skip this step and use the pre-built static files.

### Step 3: Verify Pluto Connection

Ensure your Pluto is running vita49_streamer:

```bash
# SSH to Pluto
ssh root@pluto.local
# Password: analog

# Check if streamer is running
ps | grep vita49

# If not running, start it
./vita49_streamer &
```

## Quick Start

### Option 1: Development Mode (Fastest for Testing)

Best for development and testing. Runs frontend dev server with hot reload.

**Terminal 1 - Backend**:
```bash
cd C:\git-repos\vita49-pluto
python -m vita49.web_server --host 0.0.0.0 --port 8000 --auto-start
```

**Terminal 2 - Frontend**:
```bash
cd src/vita49/web
npm run dev
```

**Access**: http://localhost:3000

### Option 2: Production Mode (Best for Regular Use)

Best for regular use. Serves optimized static files from backend.

1. Build the frontend (one-time):
```bash
cd src/vita49/web
npm run build
```

2. Start the backend:
```bash
cd C:\git-repos\vita49-pluto
python -m vita49.web_server --host 0.0.0.0 --port 8000 --auto-start
```

**Access**: http://localhost:8000

### Option 3: Quick Demo (No Frontend Build)

Use the built-in API for basic testing:

```bash
python -m vita49.web_server --auto-start
```

**Access API**: http://localhost:8000/api/status

## Features

### 1. Control Panel

**Configure SDR Parameters**:
- Center Frequency: 70 MHz - 6 GHz
- RX Gain: 0 - 73 dB
- Sample Rate: 2.084 - 61.44 MSPS
- Bandwidth: 200 kHz - 56 MHz

**Quick Presets**:
- WiFi 2.4 GHz (2.437 GHz @ 20 MSPS)
- FM Radio (103.7 MHz @ 2 MSPS)
- GPS L1 (1.57542 GHz @ 4 MSPS)
- LTE Band 7 (2.6 GHz @ 30 MSPS)

**Stream Control**:
- Start/Stop streaming
- View connection status
- See client count

### 2. Spectrum Analyzer

Real-time FFT visualization showing:
- Frequency spectrum with auto-scaling
- Peak power level
- Noise floor estimation
- Signal-to-Noise Ratio (SNR)
- Frequency offset from center

### 3. Waterfall Display

Spectrogram visualization:
- Color-coded power levels
- Configurable history depth
- Time evolution of spectrum
- Viridis colormap (blue→purple→red→yellow)

### 4. Packet Inspector

Monitor VITA49 packets:
- Timestamp of each packet
- Packet type (Data/Context)
- Stream ID
- Packet count
- Sample count per packet
- Filter by packet type
- Auto-scroll option

### 5. Statistics Dashboard

Track performance:
- Total packets received
- Total samples received
- Current throughput (Mbps)
- Packet rate (packets/second)
- Elapsed time
- Stream configuration

## Advanced Usage

### Custom Configuration

Edit backend configuration:

```python
# In web_server.py
handler = VITA49WebHandler(manager)
handler.fft_size = 2048        # Increase FFT resolution
handler.update_rate_hz = 30.0  # Faster updates
handler.averaging = 8          # More spectrum averaging
```

### API Integration

Use the REST API from other applications:

```python
import requests

# Get status
status = requests.get('http://localhost:8000/api/status').json()

# Configure Pluto
config = {
    'pluto_uri': 'ip:192.168.2.1',
    'center_freq_hz': 2.4e9,
    'sample_rate_hz': 30e6,
    'bandwidth_hz': 20e6,
    'rx_gain_db': 30.0
}
requests.post('http://localhost:8000/api/config', json=config)

# Start stream
requests.post('http://localhost:8000/api/stream/start')
```

### WebSocket Client

Connect from JavaScript or Python:

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/stream')

ws.onmessage = (event) => {
  const message = JSON.parse(event.data)

  if (message.type === 'spectrum') {
    console.log('Spectrum data:', message.data)
  }
}
```

### Custom Pluto URI

If your Pluto is at a different address:

```bash
python -m vita49.web_server --pluto-uri ip:192.168.1.100
```

Or configure via the web UI control panel.

## Deployment

### Local Network Access

Allow access from other devices on your network:

```bash
python -m vita49.web_server --host 0.0.0.0 --port 8000
```

Access from other devices: http://YOUR_PC_IP:8000

### Firewall Configuration

**Windows**:
```powershell
netsh advfirewall firewall add rule name="VITA49 Web UI" dir=in action=allow protocol=TCP localport=8000
```

**Linux**:
```bash
sudo ufw allow 8000/tcp
```

### Run as Service (Linux)

Create systemd service:

```bash
sudo nano /etc/systemd/system/vita49-web.service
```

```ini
[Unit]
Description=VITA49 Pluto Web UI
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/vita49-pluto
ExecStart=/usr/bin/python3 -m vita49.web_server --host 0.0.0.0 --port 8000 --auto-start
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable vita49-web
sudo systemctl start vita49-web
```

### Docker Deployment (Future)

A Dockerfile will be provided for containerized deployment.

## Troubleshooting

### Backend Won't Start

**Error**: "Address already in use"
- Solution: Port 8000 is taken. Use a different port:
  ```bash
  python -m vita49.web_server --port 8080
  ```

**Error**: "Module 'fastapi' not found"
- Solution: Install dependencies:
  ```bash
  pip install -r requirements-web.txt
  ```

### No Data in Plots

**Symptom**: Plots show "Waiting for data..."

1. Check backend is receiving packets:
   - Look for "Received packet" messages in backend logs
   - Check VITA49 client is running on port 4991

2. Verify Pluto is streaming:
   ```bash
   ssh root@pluto.local
   ps | grep vita49
   ```

3. Check firewall isn't blocking UDP port 4991

4. Restart the stream:
   - Click "Stop Stream" then "Start Stream" in web UI

### WebSocket Connection Failed

**Symptom**: Red "Disconnected" indicator in header

1. Check backend is running
2. Verify URL is correct (ws://localhost:8000/ws/stream)
3. Clear browser cache and reload
4. Check browser console for errors (F12)

### Plots Are Slow/Laggy

1. Reduce FFT size:
   ```javascript
   // In SpectrumPlot.jsx
   const [fftSize, setFftSize] = useState(512)  // Instead of 1024
   ```

2. Decrease update rate:
   ```python
   # In web_server.py
   handler.update_rate_hz = 10.0  # Instead of 20
   ```

3. Reduce waterfall history:
   ```python
   self.waterfall_buffer = deque(maxlen=50)  # Instead of 100
   ```

### Pluto Not Responding to Config Changes

The current implementation stores config but doesn't send to Pluto yet. To actually configure Pluto:

1. Use the existing config_client:
   ```bash
   python src/vita49/config_client.py --pluto pluto.local --freq 2.4e9 --gain 30
   ```

2. Or implement the TODO in web_server.py to send VITA49 context packets

### High CPU Usage

Backend CPU usage is proportional to:
- Sample rate (higher = more data)
- FFT size (larger = more computation)
- Number of connected clients

Optimize by reducing these parameters.

## Performance Tips

### Best Settings for Different Use Cases

**WiFi Monitoring (2.4 GHz)**:
- Frequency: 2.437 GHz
- Rate: 20 MSPS
- FFT: 1024
- Update: 20 Hz

**FM Radio**:
- Frequency: 88-108 MHz
- Rate: 2 MSPS
- FFT: 2048 (higher resolution)
- Update: 10 Hz

**Wideband Scanning**:
- Rate: 61.44 MSPS (max)
- FFT: 512 (lower for speed)
- Update: 30 Hz
- Averaging: 2

**High Resolution**:
- FFT: 4096
- Update: 5 Hz
- Averaging: 8

## Next Steps

- Explore the packet inspector to understand VITA49 packet structure
- Try different frequency presets
- Monitor statistics to understand stream health
- Integrate with your own applications via the REST API
- Contribute improvements to the project!

## Support

- GitHub Issues: Report bugs and request features
- Documentation: Check docs/ directory
- VITA49 Spec: Reference the VITA 49.0 standard

---

**Happy SDR exploration!** 📡
