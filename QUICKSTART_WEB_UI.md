# VITA49 Web UI - Quick Start Guide

Get your web UI running in 5 minutes!

## Prerequisites

- Python 3.8+
- Node.js 16+ (optional for development)
- ADALM-Pluto SDR with vita49_streamer running

## Installation

### 1. Install Backend Dependencies

```bash
pip install -r requirements-web.txt
```

This installs FastAPI, Uvicorn, and WebSockets.

### 2. Install Frontend Dependencies (Optional)

For development with hot-reload:

```bash
cd src/vita49/web
npm install
```

Skip this for production use - just use the built static files.

## Running the Web UI

### Option A: Development Mode (Recommended for Testing)

**Terminal 1 - Start Backend**:
```bash
python -m vita49.web_server --host 0.0.0.0 --port 8001 --auto-start
```

**Terminal 2 - Start Frontend**:
```bash
cd src/vita49/web
npm run dev
```

**Access**: http://localhost:3000

### Option B: Production Mode

1. Build frontend (one-time):
```bash
cd src/vita49/web
npm run build
```

2. Start backend:
```bash
python -m vita49.web_server --host 0.0.0.0 --port 8001
```

**Access**: http://localhost:8001

## Using the Web UI

1. **Check Connection**: Look for green "WebSocket Connected" in header
2. **Start Streaming**: Click the "Start Stream" button
3. **Configure SDR**:
   - Select a preset (e.g., "WiFi 2.4 GHz")
   - Or manually adjust frequency/gain sliders
   - Click "Apply Configuration"
4. **Watch the Plots**: Spectrum and waterfall will update in real-time!

## Quick Presets

Click these in the Control Panel for instant setup:

- **WiFi 2.4 GHz**: Monitor 2.4 GHz WiFi band
- **FM Radio**: Listen to FM radio (88-108 MHz)
- **GPS L1**: Capture GPS signals
- **LTE Band 7**: Monitor LTE cellular band

## What You'll See

- **Spectrum Analyzer**: Real-time FFT with peak/noise/SNR
- **Waterfall**: Color-coded spectrogram showing signal evolution
- **Packet Inspector**: VITA49 packet details and timestamps
- **Statistics**: Throughput, packet rates, sample counts

## Troubleshooting

### "Cannot connect to WebSocket"
- Ensure backend is running on port 8001
- Check firewall settings

### "No data in plots"
- Click "Start Stream" button
- Verify Pluto is streaming: `ssh root@pluto.local 'ps | grep vita49'`

### "Port 8001 already in use"
- Use different port: `python -m vita49.web_server --port 8080`

## Access from Other Devices

To access from phone/tablet on same network:

1. Find your PC's IP address: `ipconfig` (Windows) or `ifconfig` (Linux)
2. Start backend: `python -m vita49.web_server --host 0.0.0.0`
3. Access from other device: http://YOUR_PC_IP:8001

## Next Steps

- Read the complete guide: `docs/WEB_UI_GUIDE.md`
- Explore the API: `src/vita49/web/README.md`
- Check implementation details: `WEB_UI_SUMMARY.md`

## Support

- Issues: Open a GitHub issue
- Documentation: See `docs/` directory
- Questions: Check WEB_UI_GUIDE.md FAQ section

---

**Enjoy your web-based SDR interface!** 📡
