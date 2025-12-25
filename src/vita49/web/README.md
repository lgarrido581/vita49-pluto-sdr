# VITA49 Pluto Web UI

A modern, real-time web interface for controlling and visualizing VITA49 data streams from ADALM-Pluto SDR.

## Features

### 🎛️ **Control Panel**
- Configure center frequency, sample rate, bandwidth, and RX gain
- Quick presets for common use cases (WiFi, FM Radio, GPS, LTE)
- Real-time stream control (start/stop)
- Connection status monitoring

### 📊 **Spectrum Analyzer**
- Real-time FFT visualization
- Auto-scaling spectrum display
- Signal statistics (peak power, noise floor, SNR)
- Frequency offset display

### 🌊 **Waterfall Display**
- Spectrogram visualization with color mapping
- Configurable history depth
- Real-time updates

### 📦 **Packet Inspector**
- View VITA49 packet details
- Filter by packet type (Data/Context)
- Timestamp and stream ID inspection
- Sample count tracking

### 📈 **Statistics Dashboard**
- Packets and samples received
- Throughput monitoring (Mbps)
- Packet rate tracking
- Elapsed time and configuration info

## Architecture

```
┌─────────────────────────────────────┐
│  React Frontend (Vite)              │
│  - Modern component-based UI        │
│  - Real-time Plotly.js charts       │
│  - WebSocket streaming client       │
└─────────────────────────────────────┘
         │ WebSocket + REST API
         ▼
┌─────────────────────────────────────┐
│  FastAPI Backend (Python)           │
│  - WebSocket handler                │
│  - REST API endpoints               │
│  - VITA49StreamClient integration   │
└─────────────────────────────────────┘
         │ UDP VITA49
         ▼
┌─────────────────────────────────────┐
│  Pluto SDR (vita49_streamer)        │
└─────────────────────────────────────┘
```

## Installation

### Prerequisites

- Node.js 16+ and npm
- Python 3.8+
- ADALM-Pluto SDR with vita49_streamer running

### Backend Setup

1. Install Python dependencies:
```bash
cd C:\git-repos\vita49-pluto
pip install fastapi uvicorn websockets
```

2. Start the backend server:
```bash
python -m vita49.web_server --host 0.0.0.0 --port 8001
```

The backend will:
- Listen for VITA49 UDP packets on port 4991
- Serve REST API on port 8001
- Provide WebSocket streaming on ws://localhost:8001/ws/stream

### Frontend Setup

1. Install Node.js dependencies:
```bash
cd src/vita49/web
npm install
```

2. Start the development server:
```bash
npm run dev
```

The frontend will be available at: http://localhost:3000

## Usage

### Quick Start

1. **Start the backend**:
```bash
python -m vita49.web_server --auto-start
```

2. **Start the frontend** (in another terminal):
```bash
cd src/vita49/web
npm run dev
```

3. **Open your browser** to http://localhost:3000

4. **Configure Pluto**:
   - Select a preset or manually set frequency/gain/sample rate
   - Click "Apply Configuration"
   - Click "Start Stream" to begin receiving data

### Configuration

The control panel allows you to configure:
- **Center Frequency**: 70 MHz - 6 GHz
- **Sample Rate**: 2.084 - 61.44 MSPS
- **Bandwidth**: 200 kHz - 56 MHz
- **RX Gain**: 0 - 73 dB

### Quick Presets

- **WiFi 2.4 GHz**: 2.437 GHz @ 20 MSPS
- **FM Radio**: 103.7 MHz @ 2 MSPS
- **GPS L1**: 1.57542 GHz @ 4 MSPS
- **LTE Band 7**: 2.6 GHz @ 30 MSPS

## API Reference

### REST Endpoints

#### `GET /api/status`
Get current system status including streaming state, metadata, and statistics.

**Response**:
```json
{
  "streaming": true,
  "metadata": {
    "center_freq_hz": 2400000000,
    "sample_rate_hz": 30000000,
    "bandwidth_hz": 20000000,
    "gain_db": 20.0
  },
  "statistics": {
    "packets_received": 1234,
    "samples_received": 443520,
    "throughput_mbps": 240.5
  }
}
```

#### `POST /api/config`
Configure Pluto SDR parameters.

**Request Body**:
```json
{
  "pluto_uri": "ip:pluto.local",
  "center_freq_hz": 2400000000,
  "sample_rate_hz": 30000000,
  "bandwidth_hz": 20000000,
  "rx_gain_db": 20.0
}
```

#### `POST /api/stream/start`
Start VITA49 stream reception.

**Query Parameters**:
- `port` (optional): UDP port to listen on (default: 4991)

#### `POST /api/stream/stop`
Stop VITA49 stream reception.

#### `GET /api/packets`
Get recent packet history.

**Query Parameters**:
- `count` (optional): Number of packets to retrieve (default: 20)

### WebSocket Messages

Connect to `ws://localhost:8001/ws/stream`

**Incoming Message Types**:

1. **spectrum** - Real-time spectrum data
```json
{
  "type": "spectrum",
  "data": {
    "frequencies": [-15.0, -14.9, ..., 15.0],
    "spectrum": [-80.5, -79.2, ..., -65.3],
    "signal_power_dbfs": -45.2,
    "noise_floor_db": -85.3,
    "peak_power_db": -42.1
  }
}
```

2. **waterfall** - Waterfall/spectrogram data
```json
{
  "type": "waterfall",
  "data": {
    "waterfall": [
      [-80.1, -79.5, ...],
      [-81.2, -80.8, ...],
      ...
    ]
  }
}
```

3. **metadata** - Stream metadata updates
```json
{
  "type": "metadata",
  "data": {
    "sample_rate_hz": 30000000,
    "center_freq_hz": 2400000000,
    "bandwidth_hz": 20000000,
    "gain_db": 20.0
  }
}
```

## Building for Production

1. Build the frontend:
```bash
cd src/vita49/web
npm run build
```

This creates optimized static files in `src/vita49/web/dist/`

2. Update the backend to serve static files:
```python
# In web_server.py, add:
app.mount("/", StaticFiles(directory="src/vita49/web/dist", html=True), name="static")
```

3. Run the production server:
```bash
python -m vita49.web_server --host 0.0.0.0 --port 8001
```

Access the application at: http://localhost:8001

## Development

### Project Structure

```
src/vita49/web/
├── src/
│   ├── components/        # React components
│   │   ├── ControlPanel.jsx
│   │   ├── SpectrumPlot.jsx
│   │   ├── Waterfall.jsx
│   │   ├── PacketInspector.jsx
│   │   └── Statistics.jsx
│   ├── hooks/            # Custom React hooks
│   │   └── useWebSocket.js
│   ├── App.jsx           # Main application
│   ├── main.jsx          # Entry point
│   └── index.css         # Global styles
├── index.html            # HTML template
├── package.json          # Dependencies
├── vite.config.js        # Vite configuration
└── README.md             # This file
```

### Adding New Features

1. **New Component**: Create in `src/components/`
2. **New Hook**: Create in `src/hooks/`
3. **Styling**: Add CSS module or inline styles
4. **Backend Endpoint**: Add to `web_server.py`

### Tech Stack

- **Frontend**:
  - React 18
  - Vite (build tool)
  - Plotly.js (charts)
  - Lucide React (icons)

- **Backend**:
  - FastAPI (web framework)
  - Uvicorn (ASGI server)
  - WebSockets (real-time communication)
  - VITA49 packet library (existing codebase)

## Troubleshooting

### WebSocket Connection Failed

- Ensure backend is running on port 8001
- Check firewall settings
- Verify CORS configuration in `web_server.py`

### No Data Displayed

- Verify VITA49 stream is running (check backend logs)
- Ensure Pluto is configured and streaming
- Check that UDP port 4991 is accessible

### Plots Not Updating

- Check browser console for errors
- Verify WebSocket connection status (indicator in header)
- Refresh the page

### Performance Issues

- Reduce update rate in spectrum config
- Decrease FFT size
- Limit waterfall history lines

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

MIT License - see LICENSE file for details.

## Support

For issues and questions:
- Open an issue on GitHub
- Check the main project documentation
- Review the VITA49 specification

---

**Built with ❤️ for the SDR community**
