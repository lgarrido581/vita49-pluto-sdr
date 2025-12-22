#!/usr/bin/env python3
"""
VITA49 Web Server

FastAPI-based web server that provides:
- Real-time WebSocket streaming of VITA49 IQ data
- REST API for Pluto SDR configuration
- Packet inspection and statistics
- Multi-client support

Usage:
    python -m vita49.web_server --pluto-uri ip:192.168.2.1 --port 8000
"""

import asyncio
import logging
import json
import time
import numpy as np
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict
from collections import deque
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from .stream_server import VITA49StreamClient
from .packets import VRTSignalDataPacket, VRTContextPacket

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models for API
# =============================================================================

class PlutoConfig(BaseModel):
    """Pluto SDR configuration"""
    pluto_uri: str = "ip:192.168.2.1"
    center_freq_hz: float
    sample_rate_hz: float = 30e6
    bandwidth_hz: float = 20e6
    rx_gain_db: float = 20.0


class StreamControl(BaseModel):
    """Stream control commands"""
    action: str  # "start" or "stop"


class SpectrumConfig(BaseModel):
    """Spectrum display configuration"""
    fft_size: int = 1024
    update_rate_hz: float = 20.0
    averaging: int = 1


# =============================================================================
# WebSocket Connection Manager
# =============================================================================

class ConnectionManager:
    """Manages WebSocket connections and broadcasts"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        """Accept new WebSocket connection"""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info(f"Client connected. Total clients: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection"""
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info(f"Client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        if not self.active_connections:
            return

        json_message = json.dumps(message)
        disconnected = set()

        for connection in self.active_connections:
            try:
                await connection.send_text(json_message)
            except Exception as e:
                logger.error(f"Error sending to client: {e}")
                disconnected.add(connection)

        # Clean up disconnected clients
        if disconnected:
            async with self._lock:
                self.active_connections -= disconnected


# =============================================================================
# VITA49 Stream Handler
# =============================================================================

class VITA49WebHandler:
    """Handles VITA49 stream reception and processing for web clients"""

    def __init__(self, connection_manager: ConnectionManager):
        self.manager = connection_manager
        self.client: Optional[VITA49StreamClient] = None
        self.running = False

        # Stream configuration
        self.listen_port = 4991
        self.fft_size = 1024
        self.update_rate_hz = 20.0
        self.averaging = 4

        # Buffers
        self.sample_buffer = deque(maxlen=self.fft_size * 4)
        self.waterfall_buffer = deque(maxlen=100)
        self.packet_history = deque(maxlen=100)

        # Stream metadata from context packets
        self.metadata = {
            'sample_rate_hz': 30e6,
            'center_freq_hz': 2.4e9,
            'bandwidth_hz': 20e6,
            'gain_db': 20.0,
            'context_received': False
        }

        # Statistics
        self.stats = {
            'packets_received': 0,
            'samples_received': 0,
            'bytes_received': 0,
            'context_packets_received': 0,
            'start_time': None,
            'last_packet_time': None,
            'packet_rate_hz': 0.0,
            'throughput_mbps': 0.0
        }

        # Processing
        self._last_broadcast_time = 0.0
        self._broadcast_interval = 1.0 / self.update_rate_hz
        self._spectrum_avg_buffer = []

    def start(self, port: int = 4991):
        """Start VITA49 stream reception"""
        if self.running:
            logger.warning("Stream handler already running")
            return False

        self.listen_port = port
        self.client = VITA49StreamClient(port=port)
        self.client.on_samples(self._on_samples_received)
        self.client.on_context(self._on_context_received)

        if not self.client.start():
            logger.error("Failed to start VITA49 client")
            return False

        self.running = True
        self.stats['start_time'] = time.time()
        logger.info(f"VITA49 web handler started on port {port}")
        return True

    def stop(self):
        """Stop VITA49 stream reception"""
        if not self.running:
            return

        self.running = False
        if self.client:
            self.client.stop()
            self.client = None

        logger.info("VITA49 web handler stopped")

    def _on_context_received(self, context_data: bytes):
        """Handle received context packets"""
        try:
            context = VRTContextPacket.decode(context_data)

            # Update metadata
            if context.sample_rate_hz:
                self.metadata['sample_rate_hz'] = context.sample_rate_hz
            if context.rf_reference_frequency_hz:
                self.metadata['center_freq_hz'] = context.rf_reference_frequency_hz
            if context.bandwidth_hz:
                self.metadata['bandwidth_hz'] = context.bandwidth_hz
            if context.gain_db is not None:
                self.metadata['gain_db'] = context.gain_db

            self.metadata['context_received'] = True
            self.stats['context_packets_received'] += 1

            # Broadcast metadata update
            asyncio.create_task(self.manager.broadcast({
                'type': 'metadata',
                'data': self.metadata
            }))

            logger.info(f"Context packet received: {self.metadata['center_freq_hz']/1e9:.3f} GHz, "
                       f"{self.metadata['sample_rate_hz']/1e6:.1f} MSPS")

        except Exception as e:
            logger.error(f"Error parsing context packet: {e}")

    def _on_samples_received(self, packet: VRTSignalDataPacket, samples: np.ndarray):
        """Handle received IQ samples"""
        # Update buffers
        for s in samples:
            self.sample_buffer.append(s)

        # Update statistics
        self.stats['packets_received'] += 1
        self.stats['samples_received'] += len(samples)
        self.stats['last_packet_time'] = time.time()

        # Calculate rates
        elapsed = self.stats['last_packet_time'] - self.stats['start_time']
        if elapsed > 0:
            self.stats['packet_rate_hz'] = self.stats['packets_received'] / elapsed
            self.stats['throughput_mbps'] = (self.stats['samples_received'] * 8 * 8) / (elapsed * 1e6)

        # Store packet info for inspection
        packet_info = {
            'timestamp': packet.timestamp.to_time() if packet.timestamp else None,
            'stream_id': f"0x{packet.stream_id:08X}",
            'packet_count': packet.header.packet_count,
            'sample_count': len(samples),
            'type': 'DATA'
        }
        self.packet_history.append(packet_info)

        # Process and broadcast if enough time has elapsed
        current_time = time.time()
        if current_time - self._last_broadcast_time >= self._broadcast_interval:
            asyncio.create_task(self._process_and_broadcast())
            self._last_broadcast_time = current_time

    async def _process_and_broadcast(self):
        """Process samples and broadcast to clients"""
        if len(self.sample_buffer) < self.fft_size:
            return

        # Get samples
        samples = np.array(list(self.sample_buffer)[-self.fft_size:])

        # Compute FFT
        window = np.hanning(len(samples))
        spectrum = np.fft.fftshift(np.fft.fft(samples * window))
        spectrum_mag = np.abs(spectrum)
        spectrum_db = 20 * np.log10(spectrum_mag + 1e-10)

        # Apply averaging
        self._spectrum_avg_buffer.append(spectrum_db)
        if len(self._spectrum_avg_buffer) > self.averaging:
            self._spectrum_avg_buffer.pop(0)

        if len(self._spectrum_avg_buffer) > 0:
            spectrum_db_avg = np.mean(self._spectrum_avg_buffer, axis=0)
        else:
            spectrum_db_avg = spectrum_db

        # Compute frequency bins
        freq_bins = np.fft.fftshift(
            np.fft.fftfreq(len(samples), 1/self.metadata['sample_rate_hz'])
        ) / 1e6  # Convert to MHz

        # Calculate signal statistics
        signal_power_dbfs = 10 * np.log10(np.mean(np.abs(samples)**2) + 1e-10)
        noise_floor_db = float(np.percentile(spectrum_db_avg, 10))
        peak_power_db = float(np.percentile(spectrum_db_avg, 99))

        # Add to waterfall
        self.waterfall_buffer.append(spectrum_db_avg.tolist())

        # Broadcast spectrum data
        await self.manager.broadcast({
            'type': 'spectrum',
            'data': {
                'frequencies': freq_bins.tolist()[::4],  # Decimate for bandwidth
                'spectrum': spectrum_db_avg.tolist()[::4],
                'signal_power_dbfs': signal_power_dbfs,
                'noise_floor_db': noise_floor_db,
                'peak_power_db': peak_power_db,
                'time_domain_i': samples.real.tolist()[::8],  # More decimation for time domain
                'time_domain_q': samples.imag.tolist()[::8]
            }
        })

        # Broadcast waterfall periodically (every 5 spectrums to reduce bandwidth)
        if self.stats['packets_received'] % 5 == 0:
            await self.manager.broadcast({
                'type': 'waterfall',
                'data': {
                    'waterfall': list(self.waterfall_buffer)
                }
            })

    def get_stats(self) -> dict:
        """Get current statistics"""
        return {
            **self.stats,
            'elapsed_time_s': time.time() - self.stats['start_time'] if self.stats['start_time'] else 0
        }

    def get_metadata(self) -> dict:
        """Get stream metadata"""
        return self.metadata

    def get_recent_packets(self, count: int = 20) -> List[dict]:
        """Get recent packet history"""
        return list(self.packet_history)[-count:]


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="VITA49 Pluto Web UI",
    description="Web interface for ADALM-Pluto SDR with VITA49 streaming",
    version="1.0.0"
)

# CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
manager = ConnectionManager()
handler = VITA49WebHandler(manager)
current_config: Optional[PlutoConfig] = None


# =============================================================================
# REST API Endpoints
# =============================================================================

@app.get("/")
async def root():
    """Serve the main web interface"""
    return FileResponse("src/vita49/web/index.html")


@app.get("/api/status")
async def get_status():
    """Get current system status"""
    return JSONResponse({
        'streaming': handler.running,
        'config': asdict(current_config) if current_config else None,
        'metadata': handler.get_metadata(),
        'statistics': handler.get_stats(),
        'clients_connected': len(manager.active_connections)
    })


@app.post("/api/config")
async def set_config(config: PlutoConfig):
    """Configure Pluto SDR parameters"""
    global current_config

    try:
        # Store configuration
        current_config = config

        # In a real implementation, you would send a VITA49 context packet
        # to the Pluto streamer to reconfigure it. For now, we'll just
        # update our local state.

        # TODO: Send configuration to Pluto via config_client logic
        logger.info(f"Configuration updated: {config.center_freq_hz/1e9:.3f} GHz, "
                   f"{config.sample_rate_hz/1e6:.1f} MSPS, {config.rx_gain_db} dB")

        return JSONResponse({
            'success': True,
            'message': 'Configuration updated',
            'config': asdict(config)
        })

    except Exception as e:
        logger.error(f"Error setting configuration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stream/start")
async def start_stream(port: int = 4991):
    """Start VITA49 stream reception"""
    if handler.running:
        return JSONResponse({
            'success': False,
            'message': 'Stream already running'
        })

    if handler.start(port=port):
        return JSONResponse({
            'success': True,
            'message': f'Stream started on port {port}'
        })
    else:
        raise HTTPException(status_code=500, detail='Failed to start stream')


@app.post("/api/stream/stop")
async def stop_stream():
    """Stop VITA49 stream reception"""
    handler.stop()
    return JSONResponse({
        'success': True,
        'message': 'Stream stopped'
    })


@app.get("/api/packets")
async def get_packets(count: int = 20):
    """Get recent packet history"""
    return JSONResponse({
        'packets': handler.get_recent_packets(count)
    })


@app.post("/api/spectrum/config")
async def set_spectrum_config(config: SpectrumConfig):
    """Update spectrum display configuration"""
    handler.fft_size = config.fft_size
    handler.update_rate_hz = config.update_rate_hz
    handler.averaging = config.averaging
    handler._broadcast_interval = 1.0 / config.update_rate_hz

    return JSONResponse({
        'success': True,
        'message': 'Spectrum configuration updated',
        'config': config.dict()
    })


# =============================================================================
# WebSocket Endpoint
# =============================================================================

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time data streaming"""
    await manager.connect(websocket)

    try:
        # Send initial status
        await websocket.send_json({
            'type': 'status',
            'data': {
                'streaming': handler.running,
                'metadata': handler.get_metadata(),
                'statistics': handler.get_stats()
            }
        })

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Wait for messages from client (with timeout)
                message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0
                )

                # Handle client messages (e.g., requests for specific data)
                data = json.loads(message)
                if data.get('type') == 'ping':
                    await websocket.send_json({'type': 'pong'})

            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({'type': 'keepalive'})

    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await manager.disconnect(websocket)


# =============================================================================
# Startup/Shutdown Events
# =============================================================================

@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    logger.info("VITA49 Web Server starting up")
    # Could auto-start streaming here if desired


@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown"""
    logger.info("VITA49 Web Server shutting down")
    handler.stop()


# =============================================================================
# CLI Interface
# =============================================================================

def main():
    """Run the web server"""
    import argparse

    parser = argparse.ArgumentParser(
        description="VITA49 Web Server for Pluto SDR"
    )
    parser.add_argument(
        '--host',
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=8000,
        help="Web server port (default: 8000)"
    )
    parser.add_argument(
        '--vita49-port',
        type=int,
        default=4991,
        help="VITA49 UDP port to listen on (default: 4991)"
    )
    parser.add_argument(
        '--auto-start',
        action='store_true',
        help="Automatically start VITA49 stream on startup"
    )

    args = parser.parse_args()

    # Auto-start streaming if requested
    if args.auto_start:
        handler.start(port=args.vita49_port)

    # Run server
    logger.info(f"Starting VITA49 Web Server on http://{args.host}:{args.port}")
    logger.info(f"VITA49 stream port: {args.vita49_port}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
