#!/usr/bin/env python3
"""
VITA 49 IQ Streaming Server for ADALM-Pluto+ SDR

This module provides a VITA 49-compliant UDP streaming interface that sits
on top of the Analog Devices libiio/pyadi-iio stack. It allows the Pluto+
to stream IQ data using the open VITA 49 (VRT) standard.

Architecture:
    Pluto+ ARM Processor
    ├── libiio (kernel driver)
    ├── pyadi-iio (Python interface)
    └── VITA49StreamServer (this module)
            ├── VRT Signal Data Packets (IQ samples)
            ├── VRT Context Packets (metadata)
            └── UDP multicast/unicast transport

Features:
- Configurable sample rate, center frequency, bandwidth
- Multi-channel support (2R2T on Pluto+)
- UDP unicast or multicast streaming
- Context packet generation for receiver sync
- Stream ID management for multi-stream environments
- Circular buffer for flow control
- Statistics and monitoring

Usage:
    # Basic streaming server
    server = VITA49StreamServer(
        uri="ip:192.168.2.1",
        center_freq_hz=2.4e9,
        sample_rate_hz=30e6,
        destination="192.168.2.100",
        port=4991
    )
    server.start()

    # ... streaming in background ...

    server.stop()

Author: Pluto+ Radar Emulator Project
License: MIT
"""

import asyncio
import logging
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Callable, Dict, Tuple
import numpy as np

try:
    import adi
    HAS_ADI = True
except ImportError:
    HAS_ADI = False
    print("Warning: pyadi-iio not available. Running in simulation mode.")

from vita49_packets import (
    VRTSignalDataPacket,
    VRTContextPacket,
    VRTTimestamp,
    VRTHeader,
    VRTTrailer,
    PacketType,
    TSI,
    TSF,
    create_stream_id,
    calculate_max_samples_per_packet
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StreamMode(Enum):
    """Streaming mode selection"""
    UNICAST = "unicast"
    MULTICAST = "multicast"
    BROADCAST = "broadcast"


class GainMode(Enum):
    """Gain control mode"""
    MANUAL = "manual"
    AGC_SLOW = "slow_attack"
    AGC_FAST = "fast_attack"
    AGC_HYBRID = "hybrid"


@dataclass
class StreamConfig:
    """Configuration for a single VRT stream"""
    stream_id: int
    channel: int
    enabled: bool = True
    destination: str = "127.0.0.1"
    port: int = 4991
    mode: StreamMode = StreamMode.UNICAST

    # VRT packet settings
    samples_per_packet: int = 360  # Standard VRT payload size
    context_interval_packets: int = 100  # Send context every N packets


@dataclass
class SDRConfig:
    """SDR hardware configuration"""
    uri: str = "ip:192.168.2.1"
    center_freq_hz: float = 2.4e9
    sample_rate_hz: float = 30e6
    bandwidth_hz: float = 20e6
    rx_gain_db: float = 20.0
    tx_gain_db: float = -10.0
    gain_mode: GainMode = GainMode.MANUAL
    rx_channels: List[int] = field(default_factory=lambda: [0])
    buffer_size: int = 32768


@dataclass
class StreamStatistics:
    """Statistics for monitoring stream health"""
    packets_sent: int = 0
    bytes_sent: int = 0
    samples_sent: int = 0
    packets_dropped: int = 0
    overruns: int = 0
    underruns: int = 0
    context_packets_sent: int = 0
    start_time: float = 0.0
    last_packet_time: float = 0.0

    @property
    def elapsed_time(self) -> float:
        return time.time() - self.start_time if self.start_time else 0.0

    @property
    def packets_per_second(self) -> float:
        elapsed = self.elapsed_time
        return self.packets_sent / elapsed if elapsed > 0 else 0.0

    @property
    def mbps(self) -> float:
        elapsed = self.elapsed_time
        return (self.bytes_sent * 8 / 1e6) / elapsed if elapsed > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            'packets_sent': self.packets_sent,
            'bytes_sent': self.bytes_sent,
            'samples_sent': self.samples_sent,
            'packets_dropped': self.packets_dropped,
            'overruns': self.overruns,
            'underruns': self.underruns,
            'context_packets_sent': self.context_packets_sent,
            'elapsed_time_s': self.elapsed_time,
            'packets_per_second': self.packets_per_second,
            'throughput_mbps': self.mbps
        }


class PlutoSDRInterface:
    """
    Interface to ADALM-Pluto+ SDR via pyadi-iio

    Handles all libiio interactions including:
    - Device connection and configuration
    - Buffer management
    - Sample acquisition
    - Gain control
    """

    def __init__(self, config: SDRConfig):
        self.config = config
        self.sdr = None
        self.connected = False
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Connect to SDR and configure"""
        try:
            if not HAS_ADI:
                logger.warning("pyadi-iio not available, using simulation mode")
                self.connected = False
                return False

            logger.info(f"Connecting to SDR at {self.config.uri}")
            self.sdr = adi.ad9361(self.config.uri)

            # Configure RX
            self.sdr.sample_rate = int(self.config.sample_rate_hz)
            self.sdr.rx_lo = int(self.config.center_freq_hz)
            self.sdr.rx_rf_bandwidth = int(self.config.bandwidth_hz)
            self.sdr.rx_buffer_size = self.config.buffer_size
            self.sdr.rx_enabled_channels = self.config.rx_channels

            # Configure gain
            for ch in self.config.rx_channels:
                gain_attr = f'gain_control_mode_chan{ch}'
                if hasattr(self.sdr, gain_attr):
                    setattr(self.sdr, gain_attr, self.config.gain_mode.value)

                if self.config.gain_mode == GainMode.MANUAL:
                    hw_gain_attr = f'rx_hardwaregain_chan{ch}'
                    if hasattr(self.sdr, hw_gain_attr):
                        setattr(self.sdr, hw_gain_attr, self.config.rx_gain_db)

            self.connected = True
            logger.info(f"Connected to SDR: {self.config.sample_rate_hz/1e6:.1f} MSPS @ "
                       f"{self.config.center_freq_hz/1e9:.3f} GHz")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to SDR: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from SDR"""
        if self.sdr:
            try:
                self.sdr.rx_destroy_buffer()
            except:
                pass
            self.sdr = None
        self.connected = False
        logger.info("Disconnected from SDR")

    def receive(self) -> Optional[List[np.ndarray]]:
        """
        Receive samples from all enabled channels.

        Returns:
            List of complex64 numpy arrays, one per channel.
            Returns None if not connected.
        """
        if not self.connected or not self.sdr:
            return None

        try:
            with self._lock:
                data = self.sdr.rx()

            # Handle single vs multi-channel
            if isinstance(data, np.ndarray):
                return [data.astype(np.complex64)]
            else:
                return [ch.astype(np.complex64) for ch in data]

        except Exception as e:
            logger.error(f"RX error: {e}")
            return None

    def get_current_config(self) -> dict:
        """Get current SDR configuration"""
        if not self.connected or not self.sdr:
            return {}

        return {
            'sample_rate_hz': self.sdr.sample_rate,
            'rx_lo_hz': self.sdr.rx_lo,
            'rx_bandwidth_hz': self.sdr.rx_rf_bandwidth,
            'rx_channels': list(self.sdr.rx_enabled_channels),
            'buffer_size': self.sdr.rx_buffer_size
        }


class SimulatedSDRInterface:
    """
    Simulated SDR interface for testing without hardware.
    Generates synthetic IQ data with configurable signals.
    """

    def __init__(self, config: SDRConfig):
        self.config = config
        self.connected = False
        self._phase = 0.0
        self._sample_count = 0

    def connect(self) -> bool:
        self.connected = True
        logger.info("Connected to simulated SDR")
        return True

    def disconnect(self):
        self.connected = False

    def receive(self) -> Optional[List[np.ndarray]]:
        if not self.connected:
            return None

        # Generate test signal: tone + noise
        n = self.config.buffer_size
        fs = self.config.sample_rate_hz
        f_tone = 1e6  # 1 MHz IF tone

        t = (self._sample_count + np.arange(n)) / fs
        self._sample_count += n

        # Generate for each channel
        channels = []
        for ch in self.config.rx_channels:
            # Tone with slight frequency offset per channel
            phase_offset = ch * np.pi / 4
            signal = 0.7 * np.exp(1j * (2 * np.pi * f_tone * t + phase_offset))
            # Add noise
            noise = 0.1 * (np.random.randn(n) + 1j * np.random.randn(n))
            channels.append((signal + noise).astype(np.complex64))

        # Simulate realistic sample rate timing
        time.sleep(n / fs)

        return channels

    def get_current_config(self) -> dict:
        return {
            'sample_rate_hz': self.config.sample_rate_hz,
            'rx_lo_hz': self.config.center_freq_hz,
            'rx_bandwidth_hz': self.config.bandwidth_hz,
            'rx_channels': self.config.rx_channels,
            'buffer_size': self.config.buffer_size,
            'simulated': True
        }


class VITA49StreamServer:
    """
    VITA 49 IQ Streaming Server

    Streams IQ data from ADALM-Pluto+ SDR using VITA 49 (VRT) packet format
    over UDP. Supports multiple channels and destinations.
    """

    def __init__(
        self,
        uri: str = "ip:192.168.2.1",
        center_freq_hz: float = 2.4e9,
        sample_rate_hz: float = 30e6,
        bandwidth_hz: float = 20e6,
        rx_gain_db: float = 20.0,
        destination: str = "127.0.0.1",
        port: int = 4991,
        rx_channels: List[int] = None,
        device_id: int = 1,
        samples_per_packet: int = 360,
        context_interval: int = 100,
        use_simulation: bool = False
    ):
        """
        Initialize VITA 49 streaming server.

        Args:
            uri: SDR URI (e.g., "ip:192.168.2.1" or "ip:pluto.local")
            center_freq_hz: RX center frequency in Hz
            sample_rate_hz: Sample rate in Hz
            bandwidth_hz: RF bandwidth in Hz
            rx_gain_db: RX gain in dB (manual gain mode)
            destination: UDP destination IP address
            port: UDP destination port (base port, incremented per channel)
            rx_channels: List of RX channel indices to stream
            device_id: Device ID for stream ID generation
            samples_per_packet: Complex IQ samples per VRT packet
            context_interval: Send context packet every N data packets
            use_simulation: Use simulated SDR (for testing)
        """
        # SDR configuration
        self.sdr_config = SDRConfig(
            uri=uri,
            center_freq_hz=center_freq_hz,
            sample_rate_hz=sample_rate_hz,
            bandwidth_hz=bandwidth_hz,
            rx_gain_db=rx_gain_db,
            rx_channels=rx_channels or [0]
        )

        # Stream configuration
        self.device_id = device_id
        self.streams: Dict[int, StreamConfig] = {}
        for i, ch in enumerate(self.sdr_config.rx_channels):
            stream_id = create_stream_id(channel=ch, device_id=device_id)
            self.streams[ch] = StreamConfig(
                stream_id=stream_id,
                channel=ch,
                destination=destination,
                port=port + i,  # Increment port per channel
                samples_per_packet=samples_per_packet,
                context_interval_packets=context_interval
            )

        # Statistics per stream
        self.stats: Dict[int, StreamStatistics] = {
            ch: StreamStatistics() for ch in self.sdr_config.rx_channels
        }

        # SDR interface
        self.use_simulation = use_simulation or not HAS_ADI
        if self.use_simulation:
            self.sdr = SimulatedSDRInterface(self.sdr_config)
        else:
            self.sdr = PlutoSDRInterface(self.sdr_config)

        # UDP sockets
        self.sockets: Dict[int, socket.socket] = {}

        # Threading
        self._running = False
        self._stream_thread: Optional[threading.Thread] = None
        self._packet_counters: Dict[int, int] = {ch: 0 for ch in self.sdr_config.rx_channels}

        # Callbacks
        self._on_packet_sent: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

    def _create_sockets(self):
        """Create UDP sockets for each stream"""
        for ch, stream in self.streams.items():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)  # 1MB buffer

            if stream.mode == StreamMode.MULTICAST:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            elif stream.mode == StreamMode.BROADCAST:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            self.sockets[ch] = sock
            logger.info(f"Created socket for channel {ch}: {stream.destination}:{stream.port}")

    def _close_sockets(self):
        """Close all UDP sockets"""
        for sock in self.sockets.values():
            try:
                sock.close()
            except:
                pass
        self.sockets.clear()

    def _send_context_packet(self, channel: int, timestamp: float):
        """Send a VRT context packet for a channel"""
        stream = self.streams[channel]

        context = VRTContextPacket(
            stream_id=stream.stream_id,
            timestamp=VRTTimestamp.from_time(timestamp),
            bandwidth_hz=self.sdr_config.bandwidth_hz,
            rf_reference_frequency_hz=self.sdr_config.center_freq_hz,
            sample_rate_hz=self.sdr_config.sample_rate_hz,
            gain_db=self.sdr_config.rx_gain_db
        )

        try:
            data = context.encode()
            self.sockets[channel].sendto(data, (stream.destination, stream.port))
            self.stats[channel].context_packets_sent += 1
        except Exception as e:
            logger.error(f"Failed to send context packet: {e}")

    def _send_data_packet(
        self,
        channel: int,
        samples: np.ndarray,
        timestamp: float
    ) -> bool:
        """Send a VRT signal data packet"""
        stream = self.streams[channel]
        stats = self.stats[channel]

        # Create VRT packet
        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=samples,
            stream_id=stream.stream_id,
            sample_rate=self.sdr_config.sample_rate_hz,
            timestamp=timestamp,
            packet_count=self._packet_counters[channel]
        )

        try:
            data = packet.encode()
            self.sockets[channel].sendto(data, (stream.destination, stream.port))

            # Update statistics
            stats.packets_sent += 1
            stats.bytes_sent += len(data)
            stats.samples_sent += len(samples)
            stats.last_packet_time = time.time()

            # Increment packet counter (4-bit, wraps at 16)
            self._packet_counters[channel] = (self._packet_counters[channel] + 1) & 0xF

            if self._on_packet_sent:
                self._on_packet_sent(channel, len(data))

            return True

        except Exception as e:
            stats.packets_dropped += 1
            if self._on_error:
                self._on_error(channel, str(e))
            return False

    def _stream_loop(self):
        """Main streaming loop - runs in background thread"""
        logger.info("Starting stream loop")

        samples_per_packet = list(self.streams.values())[0].samples_per_packet
        context_interval = list(self.streams.values())[0].context_interval_packets
        packets_since_context = 0

        # Track time for accurate timestamps
        stream_start_time = time.time()

        while self._running:
            try:
                # Receive samples from SDR
                channel_data = self.sdr.receive()

                if channel_data is None:
                    time.sleep(0.001)
                    continue

                # Get timestamp for this buffer
                buffer_timestamp = time.time()

                # Process each channel
                for ch_idx, (ch, samples) in enumerate(
                    zip(self.sdr_config.rx_channels, channel_data)
                ):
                    if ch not in self.streams or not self.streams[ch].enabled:
                        continue

                    # Send context packet periodically
                    if packets_since_context >= context_interval:
                        self._send_context_packet(ch, buffer_timestamp)
                        packets_since_context = 0

                    # Packetize and send
                    offset = 0
                    sample_period = 1.0 / self.sdr_config.sample_rate_hz

                    while offset < len(samples):
                        # Get samples for this packet
                        end = min(offset + samples_per_packet, len(samples))
                        packet_samples = samples[offset:end]

                        # Calculate precise timestamp for this packet
                        packet_timestamp = buffer_timestamp + (offset * sample_period)

                        # Send packet
                        self._send_data_packet(ch, packet_samples, packet_timestamp)
                        offset = end
                        packets_since_context += 1

            except Exception as e:
                logger.error(f"Stream loop error: {e}")
                if not self._running:
                    break
                time.sleep(0.01)

        logger.info("Stream loop stopped")

    def start(self) -> bool:
        """Start streaming"""
        if self._running:
            logger.warning("Server already running")
            return True

        # Connect to SDR
        if not self.sdr.connect():
            if not self.use_simulation:
                logger.error("Failed to connect to SDR")
                return False

        # Create sockets
        self._create_sockets()

        # Reset statistics
        now = time.time()
        for ch in self.stats:
            self.stats[ch] = StreamStatistics(start_time=now)

        # Start streaming thread
        self._running = True
        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()

        logger.info("VITA 49 streaming server started")
        return True

    def stop(self):
        """Stop streaming"""
        if not self._running:
            return

        self._running = False

        if self._stream_thread:
            self._stream_thread.join(timeout=2.0)
            self._stream_thread = None

        self._close_sockets()
        self.sdr.disconnect()

        logger.info("VITA 49 streaming server stopped")

    def get_statistics(self) -> Dict[int, dict]:
        """Get streaming statistics for all channels"""
        return {ch: stats.to_dict() for ch, stats in self.stats.items()}

    def set_center_frequency(self, freq_hz: float):
        """Update center frequency (requires restart)"""
        self.sdr_config.center_freq_hz = freq_hz
        logger.info(f"Center frequency set to {freq_hz/1e9:.3f} GHz")

    def set_sample_rate(self, rate_hz: float):
        """Update sample rate (requires restart)"""
        self.sdr_config.sample_rate_hz = rate_hz
        logger.info(f"Sample rate set to {rate_hz/1e6:.1f} MSPS")

    def set_gain(self, gain_db: float):
        """Update RX gain"""
        self.sdr_config.rx_gain_db = gain_db
        if self.sdr.connected and hasattr(self.sdr.sdr, 'rx_hardwaregain_chan0'):
            for ch in self.sdr_config.rx_channels:
                setattr(self.sdr.sdr, f'rx_hardwaregain_chan{ch}', gain_db)
        logger.info(f"RX gain set to {gain_db} dB")

    def set_destination(self, channel: int, destination: str, port: int):
        """Update stream destination"""
        if channel in self.streams:
            self.streams[channel].destination = destination
            self.streams[channel].port = port
            logger.info(f"Channel {channel} destination: {destination}:{port}")

    def on_packet_sent(self, callback: Callable[[int, int], None]):
        """Set callback for packet sent events: callback(channel, bytes)"""
        self._on_packet_sent = callback

    def on_error(self, callback: Callable[[int, str], None]):
        """Set callback for error events: callback(channel, error_message)"""
        self._on_error = callback


class VITA49StreamClient:
    """
    VITA 49 IQ Stream Receiver Client

    Receives VRT packets over UDP and extracts IQ samples.
    Useful for testing the streaming server.
    """

    def __init__(
        self,
        listen_address: str = "0.0.0.0",
        port: int = 4991,
        buffer_size: int = 65536
    ):
        self.listen_address = listen_address
        self.port = port
        self.buffer_size = buffer_size
        self.socket: Optional[socket.socket] = None
        self._running = False
        self._receive_thread: Optional[threading.Thread] = None

        # Received data
        self.packets_received = 0
        self.samples_received = 0
        self.last_context: Optional[VRTContextPacket] = None
        self._sample_buffer: deque = deque(maxlen=1000000)  # ~1M samples

        # Callbacks
        self._on_samples: Optional[Callable] = None
        self._on_context: Optional[Callable] = None

    def start(self) -> bool:
        """Start receiving"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self.socket.bind((self.listen_address, self.port))
            self.socket.settimeout(0.5)

            self._running = True
            self._receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._receive_thread.start()

            logger.info(f"VITA 49 client listening on {self.listen_address}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"Failed to start client: {e}")
            return False

    def stop(self):
        """Stop receiving"""
        self._running = False
        if self._receive_thread:
            self._receive_thread.join(timeout=2.0)
        if self.socket:
            self.socket.close()
            self.socket = None

    def _receive_loop(self):
        """Background receive loop"""
        while self._running:
            try:
                data, addr = self.socket.recvfrom(self.buffer_size)

                # Parse header to determine packet type
                header = VRTHeader.decode(data[:4])

                if header.packet_type in (PacketType.IF_DATA_WITH_STREAM_ID,
                                          PacketType.IF_DATA_WITHOUT_STREAM_ID):
                    # Signal data packet
                    packet = VRTSignalDataPacket.decode(data)
                    iq_samples = packet.to_iq_samples()

                    self.packets_received += 1
                    self.samples_received += len(iq_samples)

                    # Store samples
                    for s in iq_samples:
                        self._sample_buffer.append(s)

                    if self._on_samples:
                        self._on_samples(packet, iq_samples)

                elif header.packet_type == PacketType.CONTEXT:
                    # Context packet - parse manually for now
                    # (full context parsing would require more implementation)
                    if self._on_context:
                        self._on_context(data)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"Receive error: {e}")

    def get_samples(self, count: int) -> np.ndarray:
        """Get samples from buffer"""
        samples = []
        for _ in range(min(count, len(self._sample_buffer))):
            samples.append(self._sample_buffer.popleft())
        return np.array(samples, dtype=np.complex64)

    def on_samples(self, callback: Callable):
        """Set callback for received samples: callback(packet, iq_samples)"""
        self._on_samples = callback

    def on_context(self, callback: Callable):
        """Set callback for context packets: callback(raw_data)"""
        self._on_context = callback


# =============================================================================
# CLI Interface
# =============================================================================

def main():
    """Command-line interface for VITA 49 streaming"""
    import argparse

    parser = argparse.ArgumentParser(
        description="VITA 49 IQ Streaming Server for ADALM-Pluto+ SDR"
    )
    parser.add_argument(
        '--uri', '-u',
        default="ip:192.168.2.1",
        help="SDR URI (default: ip:192.168.2.1)"
    )
    parser.add_argument(
        '--freq', '-f',
        type=float,
        default=2.4e9,
        help="Center frequency in Hz (default: 2.4e9)"
    )
    parser.add_argument(
        '--rate', '-r',
        type=float,
        default=30e6,
        help="Sample rate in Hz (default: 30e6)"
    )
    parser.add_argument(
        '--gain', '-g',
        type=float,
        default=20.0,
        help="RX gain in dB (default: 20)"
    )
    parser.add_argument(
        '--dest', '-d',
        default="127.0.0.1",
        help="Destination IP address (default: 127.0.0.1)"
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=4991,
        help="Destination UDP port (default: 4991)"
    )
    parser.add_argument(
        '--channels', '-c',
        type=int,
        nargs='+',
        default=[0],
        help="RX channels to stream (default: 0)"
    )
    parser.add_argument(
        '--simulate', '-s',
        action='store_true',
        help="Use simulated SDR for testing"
    )
    parser.add_argument(
        '--client',
        action='store_true',
        help="Run as client (receiver) instead of server"
    )

    args = parser.parse_args()

    if args.client:
        # Run as receiver client
        print(f"Starting VITA 49 client on port {args.port}")
        client = VITA49StreamClient(port=args.port)

        def on_samples(packet, samples):
            print(f"Received {len(samples)} samples, "
                  f"stream_id=0x{packet.stream_id:08X}, "
                  f"ts={packet.timestamp.to_time():.6f}")

        client.on_samples(on_samples)
        client.start()

        try:
            while True:
                time.sleep(1)
                print(f"Total: {client.packets_received} packets, "
                      f"{client.samples_received} samples")
        except KeyboardInterrupt:
            print("\nStopping client...")
            client.stop()

    else:
        # Run as streaming server
        print(f"Starting VITA 49 streaming server")
        print(f"  SDR URI: {args.uri}")
        print(f"  Frequency: {args.freq/1e9:.3f} GHz")
        print(f"  Sample rate: {args.rate/1e6:.1f} MSPS")
        print(f"  Destination: {args.dest}:{args.port}")
        print(f"  Channels: {args.channels}")
        print(f"  Simulation: {args.simulate}")

        server = VITA49StreamServer(
            uri=args.uri,
            center_freq_hz=args.freq,
            sample_rate_hz=args.rate,
            rx_gain_db=args.gain,
            destination=args.dest,
            port=args.port,
            rx_channels=args.channels,
            use_simulation=args.simulate
        )

        if not server.start():
            print("Failed to start server")
            return 1

        try:
            while True:
                time.sleep(5)
                stats = server.get_statistics()
                for ch, s in stats.items():
                    print(f"Channel {ch}: {s['packets_sent']} pkts, "
                          f"{s['throughput_mbps']:.2f} Mbps, "
                          f"{s['packets_per_second']:.1f} pps")
        except KeyboardInterrupt:
            print("\nStopping server...")
            server.stop()

    return 0


if __name__ == '__main__':
    exit(main())
