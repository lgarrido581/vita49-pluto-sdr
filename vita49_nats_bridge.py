#!/usr/bin/env python3
"""
VITA 49 NATS Bridge for Pluto+ Radar Emulator

This module provides a NATS-enabled VITA 49 streaming interface that integrates
with the existing radar emulator architecture. It allows the VITA 49 stream to be:

1. Controlled via NATS commands (start/stop/configure)
2. Monitored via NATS telemetry
3. Integrated with the web UI through the existing NATS infrastructure

Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │                     Pluto+ Radar System                         │
    │                                                                 │
    │   ┌───────────────┐    NATS     ┌──────────────────────────┐   │
    │   │   Web UI      │◄───────────►│  VITA49NATSBridge        │   │
    │   │   FastAPI     │             │  ├── Start/Stop control  │   │
    │   └───────────────┘             │  ├── Config updates      │   │
    │                                 │  ├── Statistics pub      │   │
    │                                 │  └── Stream management   │   │
    │                                 └──────────────────────────┘   │
    │                                           │                     │
    │                                           ▼                     │
    │                                 ┌──────────────────────────┐   │
    │                                 │  VITA49StreamServer      │   │
    │                                 │  ├── libiio interface    │   │
    │                                 │  ├── VRT packet encode   │   │
    │                                 │  └── UDP streaming       │   │
    │                                 └──────────────────────────┘   │
    │                                           │                     │
    │                                           ▼                     │
    │                                      UDP/Ethernet               │
    │                                           │                     │
    └───────────────────────────────────────────│─────────────────────┘
                                                ▼
                                    ┌─────────────────────┐
                                    │  Signal Processing  │
                                    │  Host / Receiver    │
                                    └─────────────────────┘

NATS Subjects:
    vita49.cmd.start        - Start streaming
    vita49.cmd.stop         - Stop streaming
    vita49.cmd.configure    - Update configuration
    vita49.status           - Stream status updates
    vita49.stats            - Periodic statistics
    vita49.error            - Error notifications

Author: Pluto+ Radar Emulator Project
License: MIT
"""

import asyncio
import json
import logging
import signal
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, Dict, Any, List

try:
    from nats.aio.client import Client as NATS
    from nats.aio.errors import ErrTimeout
    HAS_NATS = True
except ImportError:
    HAS_NATS = False
    print("Warning: nats-py not installed. NATS features disabled.")

from vita49_stream_server import (
    VITA49StreamServer,
    SDRConfig,
    StreamConfig,
    GainMode,
    StreamMode
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VITA49State(str, Enum):
    """Stream state machine states"""
    IDLE = "idle"
    STARTING = "starting"
    STREAMING = "streaming"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class VITA49Config:
    """Configuration model for NATS interface"""
    # SDR settings
    uri: str = "ip:192.168.2.1"
    center_freq_hz: float = 2.4e9
    sample_rate_hz: float = 30e6
    bandwidth_hz: float = 20e6
    rx_gain_db: float = 20.0
    gain_mode: str = "manual"
    rx_channels: List[int] = None

    # Stream settings
    destination: str = "127.0.0.1"
    port: int = 4991
    device_id: int = 1
    samples_per_packet: int = 360
    context_interval: int = 100

    # Simulation mode
    use_simulation: bool = False

    def __post_init__(self):
        if self.rx_channels is None:
            self.rx_channels = [0]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'VITA49Config':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class VITA49Subjects:
    """NATS subject definitions"""
    # Commands
    CMD_START = "vita49.cmd.start"
    CMD_STOP = "vita49.cmd.stop"
    CMD_CONFIGURE = "vita49.cmd.configure"
    CMD_GET_STATUS = "vita49.cmd.get_status"
    CMD_GET_CONFIG = "vita49.cmd.get_config"

    # Events
    STATUS = "vita49.status"
    STATS = "vita49.stats"
    ERROR = "vita49.error"
    CONFIG_UPDATED = "vita49.config_updated"


class VITA49NATSBridge:
    """
    NATS Bridge for VITA 49 Streaming

    Provides NATS-based control and monitoring for the VITA 49 stream server.
    Integrates with the existing radar emulator NATS infrastructure.
    """

    def __init__(
        self,
        nats_server: str = "nats://localhost:4222",
        config: Optional[VITA49Config] = None,
        stats_interval: float = 5.0
    ):
        """
        Initialize NATS bridge.

        Args:
            nats_server: NATS server URL
            config: Initial VITA 49 configuration
            stats_interval: Statistics publishing interval in seconds
        """
        self.nats_server = nats_server
        self.config = config or VITA49Config()
        self.stats_interval = stats_interval

        # NATS client
        self.nc: Optional[NATS] = None

        # Stream server
        self.server: Optional[VITA49StreamServer] = None

        # State
        self.state = VITA49State.IDLE
        self._running = False
        self._stats_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """Connect to NATS server"""
        if not HAS_NATS:
            logger.error("NATS library not available")
            return False

        try:
            self.nc = NATS()
            await self.nc.connect(
                servers=[self.nats_server],
                reconnect_time_wait=2,
                max_reconnect_attempts=-1,  # Infinite reconnect
                error_cb=self._on_nats_error,
                disconnected_cb=self._on_nats_disconnect,
                reconnected_cb=self._on_nats_reconnect
            )

            logger.info(f"Connected to NATS: {self.nats_server}")

            # Subscribe to command subjects
            await self.nc.subscribe(VITA49Subjects.CMD_START, cb=self._handle_start)
            await self.nc.subscribe(VITA49Subjects.CMD_STOP, cb=self._handle_stop)
            await self.nc.subscribe(VITA49Subjects.CMD_CONFIGURE, cb=self._handle_configure)
            await self.nc.subscribe(VITA49Subjects.CMD_GET_STATUS, cb=self._handle_get_status)
            await self.nc.subscribe(VITA49Subjects.CMD_GET_CONFIG, cb=self._handle_get_config)

            logger.info("Subscribed to VITA 49 command subjects")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to NATS: {e}")
            return False

    async def disconnect(self):
        """Disconnect from NATS"""
        if self.nc and self.nc.is_connected:
            await self.nc.drain()
            await self.nc.close()
        self.nc = None
        logger.info("Disconnected from NATS")

    async def _on_nats_error(self, e):
        logger.error(f"NATS error: {e}")

    async def _on_nats_disconnect(self):
        logger.warning("NATS disconnected")

    async def _on_nats_reconnect(self):
        logger.info("NATS reconnected")

    async def _publish(self, subject: str, data: dict):
        """Publish JSON message to NATS"""
        if self.nc and self.nc.is_connected:
            try:
                await self.nc.publish(subject, json.dumps(data).encode())
            except Exception as e:
                logger.error(f"Failed to publish to {subject}: {e}")

    async def _publish_status(self):
        """Publish current status"""
        status = {
            'state': self.state.value,
            'streaming': self.state == VITA49State.STREAMING,
            'timestamp': time.time(),
            'config': self.config.to_dict()
        }
        await self._publish(VITA49Subjects.STATUS, status)

    async def _publish_error(self, error: str):
        """Publish error notification"""
        await self._publish(VITA49Subjects.ERROR, {
            'error': error,
            'state': self.state.value,
            'timestamp': time.time()
        })

    async def _stats_loop(self):
        """Periodically publish statistics"""
        while self._running:
            try:
                await asyncio.sleep(self.stats_interval)

                if self.server and self.state == VITA49State.STREAMING:
                    stats = self.server.get_statistics()
                    await self._publish(VITA49Subjects.STATS, {
                        'channels': stats,
                        'state': self.state.value,
                        'timestamp': time.time()
                    })

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Stats loop error: {e}")

    async def _handle_start(self, msg):
        """Handle start command"""
        logger.info("Received start command")

        if self.state == VITA49State.STREAMING:
            await self._reply(msg, {'success': True, 'message': 'Already streaming'})
            return

        try:
            self.state = VITA49State.STARTING
            await self._publish_status()

            # Create and start server
            self.server = VITA49StreamServer(
                uri=self.config.uri,
                center_freq_hz=self.config.center_freq_hz,
                sample_rate_hz=self.config.sample_rate_hz,
                bandwidth_hz=self.config.bandwidth_hz,
                rx_gain_db=self.config.rx_gain_db,
                destination=self.config.destination,
                port=self.config.port,
                rx_channels=self.config.rx_channels,
                device_id=self.config.device_id,
                samples_per_packet=self.config.samples_per_packet,
                context_interval=self.config.context_interval,
                use_simulation=self.config.use_simulation
            )

            if self.server.start():
                self.state = VITA49State.STREAMING
                await self._publish_status()
                await self._reply(msg, {'success': True, 'message': 'Streaming started'})
                logger.info("VITA 49 streaming started")
            else:
                self.state = VITA49State.ERROR
                await self._publish_status()
                await self._publish_error("Failed to start streaming")
                await self._reply(msg, {'success': False, 'error': 'Failed to start'})

        except Exception as e:
            self.state = VITA49State.ERROR
            await self._publish_error(str(e))
            await self._reply(msg, {'success': False, 'error': str(e)})
            logger.error(f"Start error: {e}")

    async def _handle_stop(self, msg):
        """Handle stop command"""
        logger.info("Received stop command")

        if self.state == VITA49State.IDLE:
            await self._reply(msg, {'success': True, 'message': 'Already stopped'})
            return

        try:
            self.state = VITA49State.STOPPING
            await self._publish_status()

            if self.server:
                self.server.stop()
                self.server = None

            self.state = VITA49State.IDLE
            await self._publish_status()
            await self._reply(msg, {'success': True, 'message': 'Streaming stopped'})
            logger.info("VITA 49 streaming stopped")

        except Exception as e:
            self.state = VITA49State.ERROR
            await self._publish_error(str(e))
            await self._reply(msg, {'success': False, 'error': str(e)})
            logger.error(f"Stop error: {e}")

    async def _handle_configure(self, msg):
        """Handle configuration update"""
        try:
            data = json.loads(msg.data.decode())
            logger.info(f"Received configure command: {data}")

            # Update config
            for key, value in data.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)

            # If streaming, apply hot-swappable settings
            if self.server and self.state == VITA49State.STREAMING:
                if 'rx_gain_db' in data:
                    self.server.set_gain(data['rx_gain_db'])
                if 'destination' in data or 'port' in data:
                    for ch in self.config.rx_channels:
                        self.server.set_destination(
                            ch,
                            self.config.destination,
                            self.config.port
                        )

            await self._publish(VITA49Subjects.CONFIG_UPDATED, self.config.to_dict())
            await self._reply(msg, {
                'success': True,
                'config': self.config.to_dict()
            })
            logger.info("Configuration updated")

        except Exception as e:
            await self._reply(msg, {'success': False, 'error': str(e)})
            logger.error(f"Configure error: {e}")

    async def _handle_get_status(self, msg):
        """Handle status request"""
        status = {
            'state': self.state.value,
            'streaming': self.state == VITA49State.STREAMING,
            'timestamp': time.time()
        }

        if self.server and self.state == VITA49State.STREAMING:
            status['statistics'] = self.server.get_statistics()

        await self._reply(msg, status)

    async def _handle_get_config(self, msg):
        """Handle config request"""
        await self._reply(msg, self.config.to_dict())

    async def _reply(self, msg, data: dict):
        """Reply to a request message"""
        if msg.reply and self.nc and self.nc.is_connected:
            try:
                await self.nc.publish(msg.reply, json.dumps(data).encode())
            except Exception as e:
                logger.error(f"Failed to reply: {e}")

    async def run(self):
        """Main run loop"""
        if not await self.connect():
            return

        self._running = True

        # Start stats publishing task
        self._stats_task = asyncio.create_task(self._stats_loop())

        # Publish initial status
        await self._publish_status()

        logger.info("VITA 49 NATS Bridge running")

        try:
            # Keep running until stopped
            while self._running:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("Shutting down...")

        finally:
            # Cleanup
            self._running = False

            if self._stats_task:
                self._stats_task.cancel()
                try:
                    await self._stats_task
                except asyncio.CancelledError:
                    pass

            if self.server:
                self.server.stop()

            await self.disconnect()

    def stop(self):
        """Signal the bridge to stop"""
        self._running = False


# =============================================================================
# Standalone VITA 49 Engine (without NATS)
# =============================================================================

class VITA49Engine:
    """
    Standalone VITA 49 Streaming Engine

    For use without NATS - provides direct API control of streaming.
    Useful for simple integrations or testing.
    """

    def __init__(self, config: Optional[VITA49Config] = None):
        self.config = config or VITA49Config()
        self.server: Optional[VITA49StreamServer] = None
        self.state = VITA49State.IDLE

    def configure(self, **kwargs) -> 'VITA49Engine':
        """Update configuration (fluent interface)"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        return self

    def start(self) -> bool:
        """Start streaming"""
        if self.state == VITA49State.STREAMING:
            return True

        self.server = VITA49StreamServer(
            uri=self.config.uri,
            center_freq_hz=self.config.center_freq_hz,
            sample_rate_hz=self.config.sample_rate_hz,
            bandwidth_hz=self.config.bandwidth_hz,
            rx_gain_db=self.config.rx_gain_db,
            destination=self.config.destination,
            port=self.config.port,
            rx_channels=self.config.rx_channels,
            device_id=self.config.device_id,
            samples_per_packet=self.config.samples_per_packet,
            context_interval=self.config.context_interval,
            use_simulation=self.config.use_simulation
        )

        if self.server.start():
            self.state = VITA49State.STREAMING
            return True
        else:
            self.state = VITA49State.ERROR
            return False

    def stop(self):
        """Stop streaming"""
        if self.server:
            self.server.stop()
            self.server = None
        self.state = VITA49State.IDLE

    def get_statistics(self) -> Dict[int, dict]:
        """Get streaming statistics"""
        if self.server:
            return self.server.get_statistics()
        return {}

    @property
    def is_streaming(self) -> bool:
        return self.state == VITA49State.STREAMING


# =============================================================================
# CLI Interface
# =============================================================================

async def main():
    """Command-line interface for VITA 49 NATS Bridge"""
    import argparse

    parser = argparse.ArgumentParser(
        description="VITA 49 NATS Bridge for Pluto+ SDR"
    )
    parser.add_argument(
        '--nats', '-n',
        default="nats://localhost:4222",
        help="NATS server URL"
    )
    parser.add_argument(
        '--uri', '-u',
        default="ip:192.168.2.1",
        help="SDR URI"
    )
    parser.add_argument(
        '--freq', '-f',
        type=float,
        default=2.4e9,
        help="Center frequency in Hz"
    )
    parser.add_argument(
        '--rate', '-r',
        type=float,
        default=30e6,
        help="Sample rate in Hz"
    )
    parser.add_argument(
        '--dest', '-d',
        default="127.0.0.1",
        help="Stream destination IP"
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=4991,
        help="Stream destination port"
    )
    parser.add_argument(
        '--simulate', '-s',
        action='store_true',
        help="Use simulated SDR"
    )
    parser.add_argument(
        '--autostart',
        action='store_true',
        help="Start streaming immediately"
    )
    parser.add_argument(
        '--standalone',
        action='store_true',
        help="Run without NATS (standalone mode)"
    )

    args = parser.parse_args()

    # Create config
    config = VITA49Config(
        uri=args.uri,
        center_freq_hz=args.freq,
        sample_rate_hz=args.rate,
        destination=args.dest,
        port=args.port,
        use_simulation=args.simulate
    )

    if args.standalone:
        # Run without NATS
        print("Running in standalone mode (no NATS)")
        engine = VITA49Engine(config)

        if args.autostart or True:  # Always start in standalone mode
            print("Starting VITA 49 streaming...")
            if engine.start():
                print("Streaming started successfully")
            else:
                print("Failed to start streaming")
                return 1

        try:
            while True:
                await asyncio.sleep(5)
                if engine.is_streaming:
                    stats = engine.get_statistics()
                    for ch, s in stats.items():
                        print(f"Channel {ch}: {s['packets_sent']} pkts, "
                              f"{s['throughput_mbps']:.2f} Mbps")
        except KeyboardInterrupt:
            print("\nStopping...")
            engine.stop()

    else:
        # Run with NATS
        bridge = VITA49NATSBridge(
            nats_server=args.nats,
            config=config
        )

        # Handle shutdown signals
        loop = asyncio.get_event_loop()

        def signal_handler():
            bridge.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        if args.autostart:
            # Start streaming after connecting
            async def autostart():
                await asyncio.sleep(1)  # Wait for subscriptions
                # Simulate start command
                if bridge.nc and bridge.nc.is_connected:
                    await bridge.nc.publish(
                        VITA49Subjects.CMD_START,
                        b'{}'
                    )

            asyncio.create_task(autostart())

        await bridge.run()

    return 0


if __name__ == '__main__':
    asyncio.run(main())
