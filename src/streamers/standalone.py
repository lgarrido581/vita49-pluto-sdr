#!/usr/bin/env python3
"""
VITA49 Standalone Streamer for ADALM-Pluto+ (No External Dependencies)

This is a completely self-contained single-file implementation that runs
on the Pluto+ ARM processor with ZERO external dependencies beyond:
- Python 3 stdlib (always present on Pluto+)
- pyadi-iio (pre-installed on Pluto+ firmware)

NO NUMPY REQUIRED! Uses pure Python for all operations.

Quick Start:
    1. Copy this single file to Pluto: scp pluto_vita49_standalone.py root@pluto.local:/root/
    2. SSH to Pluto: ssh root@pluto.local
    3. Run: python3 pluto_vita49_standalone.py --dest <your_pc_ip>

Features:
- Receives configuration via VITA49 Context packets (bidirectional)
- Streams IQ samples as VITA49 Data packets
- Pure Python (no numpy dependency!)
- Minimal memory footprint (~8 MB)
- Multicast to multiple receivers simultaneously

Author: VITA49-Pluto Project
License: MIT
"""

import argparse
import socket
import struct
import sys
import threading
import time
from collections import deque

# Try to import pyadi-iio (should be pre-installed on Pluto)
try:
    import adi
    HAS_ADI = True
except ImportError:
    HAS_ADI = False
    print("WARNING: pyadi-iio not found. Hardware streaming disabled.")


# =============================================================================
# VITA 49 Packet Encoder (Pure Python - No Dependencies)
# =============================================================================

class VRT49DataPacket:
    """
    Minimal VITA49 IF Data Packet encoder.

    Pure Python implementation - no numpy required.
    Uses Python's struct module for all binary operations.
    """

    PACKET_TYPE = 0b0001  # IF Data with Stream ID
    TSI_UTC = 0b01
    TSF_PICOSECONDS = 0b10

    def __init__(self, stream_id, include_trailer=True):
        self.stream_id = stream_id
        self.include_trailer = include_trailer
        self.packet_count = 0

    def encode(self, iq_samples, timestamp=None):
        """
        Encode IQ samples into VITA49 packet.

        Args:
            iq_samples: List of complex samples (Python complex type)
            timestamp: Unix timestamp (float), defaults to current time

        Returns:
            bytes ready for UDP transmission
        """
        if timestamp is None:
            timestamp = time.time()

        # Convert complex samples to interleaved int16 I/Q
        # Scale to use ~80% of int16 range (±26214 for headroom)
        scale = 26214
        payload_int16 = []

        for sample in iq_samples:
            # Normalize complex sample (assume |sample| <= 1.0)
            i_val = int(sample.real * scale)
            q_val = int(sample.imag * scale)

            # Clamp to int16 range
            i_val = max(-32768, min(32767, i_val))
            q_val = max(-32768, min(32767, q_val))

            payload_int16.append(i_val)
            payload_int16.append(q_val)

        # Pack to big-endian bytes
        payload_bytes = struct.pack(f'>{len(payload_int16)}h', *payload_int16)

        # Pad to 32-bit boundary
        pad_len = (4 - (len(payload_bytes) % 4)) % 4
        if pad_len:
            payload_bytes += b'\x00' * pad_len

        # Calculate packet size in 32-bit words
        payload_words = len(payload_bytes) // 4
        packet_words = 1 + 1 + 1 + 2 + payload_words + (1 if self.include_trailer else 0)

        # Build header
        header = 0
        header |= (self.PACKET_TYPE & 0xF) << 28
        header |= (0 & 0x1) << 27  # No class ID
        header |= (int(self.include_trailer) & 0x1) << 26
        header |= (self.TSI_UTC & 0x3) << 22
        header |= (self.TSF_PICOSECONDS & 0x3) << 20
        header |= (self.packet_count & 0xF) << 16
        header |= (packet_words & 0xFFFF)

        # Timestamp
        int_sec = int(timestamp)
        frac_sec = int((timestamp - int_sec) * 1e12)  # Picoseconds

        # Trailer
        trailer = 0x40000000 if self.include_trailer else None  # valid_data=1

        # Assemble packet
        parts = [
            struct.pack('>I', header),
            struct.pack('>I', self.stream_id),
            struct.pack('>I', int_sec),
            struct.pack('>Q', frac_sec),
            payload_bytes,
        ]
        if trailer is not None:
            parts.append(struct.pack('>I', trailer))

        # Increment packet counter
        self.packet_count = (self.packet_count + 1) & 0xF

        return b''.join(parts)


class VRT49ContextPacket:
    """
    Minimal VITA49 Context Packet encoder/decoder.

    Used for SDR configuration (sample rate, frequency, gain, bandwidth).
    """

    PACKET_TYPE = 0b0100  # Context packet

    def __init__(self, stream_id):
        self.stream_id = stream_id

    def encode(self, sample_rate_hz, center_freq_hz, bandwidth_hz, gain_db, timestamp=None):
        """Encode context packet with SDR parameters."""
        if timestamp is None:
            timestamp = time.time()

        int_sec = int(timestamp)
        frac_sec = int((timestamp - int_sec) * 1e12)

        # Context Indicator Field (CIF)
        cif = 0
        cif |= (1 << 29)  # bandwidth
        cif |= (1 << 27)  # rf_reference_frequency
        cif |= (1 << 21)  # sample_rate
        cif |= (1 << 23)  # gain

        # Encode Hz values (64-bit fixed point, 20-bit radix)
        def encode_hz(val):
            return struct.pack('>q', int(val * (1 << 20)))

        # Encode gain (16-bit, 7-bit radix)
        gain_fixed = int(gain_db * 128)

        # Build context fields
        context_fields = b''.join([
            encode_hz(bandwidth_hz),
            encode_hz(center_freq_hz),
            encode_hz(sample_rate_hz),
            struct.pack('>hh', gain_fixed, 0),
        ])

        # Packet size
        field_words = len(context_fields) // 4
        packet_words = 1 + 1 + 1 + 2 + 1 + field_words

        # Header
        header = 0
        header |= (self.PACKET_TYPE & 0xF) << 28
        header |= (0x01 & 0x3) << 22  # TSI = UTC
        header |= (0x02 & 0x3) << 20  # TSF = picoseconds
        header |= (packet_words & 0xFFFF)

        return b''.join([
            struct.pack('>I', header),
            struct.pack('>I', self.stream_id),
            struct.pack('>I', int_sec),
            struct.pack('>Q', frac_sec),
            struct.pack('>I', cif),
            context_fields,
        ])

    @staticmethod
    def decode(data):
        """
        Decode context packet to extract configuration.

        Returns:
            dict with keys: sample_rate_hz, center_freq_hz, bandwidth_hz, gain_db
        """
        offset = 0

        # Skip header
        offset += 4

        # Stream ID
        stream_id = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4

        # Skip timestamp (4 + 8 bytes)
        offset += 12

        # CIF
        cif = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4

        result = {
            'stream_id': stream_id,
            'sample_rate_hz': None,
            'center_freq_hz': None,
            'bandwidth_hz': None,
            'gain_db': None
        }

        # Decode fields based on CIF
        if cif & (1 << 29):  # bandwidth
            fixed = struct.unpack('>q', data[offset:offset+8])[0]
            result['bandwidth_hz'] = fixed / (1 << 20)
            offset += 8

        if cif & (1 << 27):  # rf_reference_frequency
            fixed = struct.unpack('>q', data[offset:offset+8])[0]
            result['center_freq_hz'] = fixed / (1 << 20)
            offset += 8

        if cif & (1 << 21):  # sample_rate
            fixed = struct.unpack('>q', data[offset:offset+8])[0]
            result['sample_rate_hz'] = fixed / (1 << 20)
            offset += 8

        if cif & (1 << 23):  # gain
            stage1, stage2 = struct.unpack('>hh', data[offset:offset+4])
            result['gain_db'] = stage1 / 128.0
            offset += 4

        return result


# =============================================================================
# Pluto SDR Interface
# =============================================================================

class PlutoInterface:
    """
    Simplified interface to ADALM-Pluto+ SDR.
    """

    def __init__(self, uri="ip:192.168.2.1"):
        self.uri = uri
        self.sdr = None
        self.connected = False

        # Current configuration
        self.sample_rate_hz = 30e6
        self.center_freq_hz = 2.4e9
        self.bandwidth_hz = 20e6
        self.gain_db = 20.0
        self.buffer_size = 8192  # Smaller buffer for lower latency

    def connect(self):
        """Connect and configure SDR."""
        if not HAS_ADI:
            print("ERROR: pyadi-iio not available")
            return False

        try:
            print(f"Connecting to Pluto at {self.uri}...")
            self.sdr = adi.ad9361(self.uri)

            # Configure
            self.apply_config()

            self.connected = True
            print(f"✓ Connected: {self.sample_rate_hz/1e6:.1f} MSPS @ {self.center_freq_hz/1e9:.3f} GHz")
            return True

        except Exception as e:
            print(f"✗ Connection failed: {e}")
            return False

    def apply_config(self):
        """Apply current configuration to SDR."""
        if not self.sdr:
            return

        self.sdr.sample_rate = int(self.sample_rate_hz)
        self.sdr.rx_lo = int(self.center_freq_hz)
        self.sdr.rx_rf_bandwidth = int(self.bandwidth_hz)
        self.sdr.rx_buffer_size = self.buffer_size
        self.sdr.rx_enabled_channels = [0]

        self.sdr.gain_control_mode_chan0 = "manual"
        self.sdr.rx_hardwaregain_chan0 = self.gain_db

    def reconfigure(self, **kwargs):
        """
        Reconfigure SDR parameters on-the-fly.

        Args:
            sample_rate_hz: Sample rate in Hz
            center_freq_hz: Center frequency in Hz
            bandwidth_hz: Bandwidth in Hz
            gain_db: RX gain in dB
        """
        if 'sample_rate_hz' in kwargs:
            self.sample_rate_hz = kwargs['sample_rate_hz']
        if 'center_freq_hz' in kwargs:
            self.center_freq_hz = kwargs['center_freq_hz']
        if 'bandwidth_hz' in kwargs:
            self.bandwidth_hz = kwargs['bandwidth_hz']
        if 'gain_db' in kwargs:
            self.gain_db = kwargs['gain_db']

        # Apply new config
        self.apply_config()

        print(f"✓ Reconfigured: {self.sample_rate_hz/1e6:.1f} MSPS @ {self.center_freq_hz/1e9:.3f} GHz, {self.gain_db} dB")

    def receive(self):
        """
        Receive IQ samples from SDR.

        Returns:
            List of complex samples (Python complex type)
        """
        if not self.connected:
            return None

        try:
            samples = self.sdr.rx()

            # Convert to Python complex list (normalize from ADC units)
            # AD9361 is 12-bit: ±2048 range
            complex_list = []
            for s in samples:
                # Normalize to ±1.0 range
                normalized = complex(s.real / 2048.0, s.imag / 2048.0)
                complex_list.append(normalized)

            return complex_list

        except Exception as e:
            print(f"RX error: {e}")
            return None

    def disconnect(self):
        """Clean disconnect."""
        if self.sdr:
            try:
                self.sdr.rx_destroy_buffer()
            except:
                pass
        self.connected = False


# =============================================================================
# VITA49 Streaming Server with Bidirectional Configuration
# =============================================================================

class VITA49Server:
    """
    VITA49 streaming server with bidirectional configuration support.

    - Receives configuration via Context packets on control port (4990)
    - Sends IQ data via Data packets on data port (4991)
    - Sends periodic Context packets with current config
    - Supports multiple receivers (maintains subscriber list)
    """

    def __init__(
        self,
        pluto: PlutoInterface,
        control_port=4990,
        data_port=4991,
        samples_per_packet=360,
        context_interval=100,
        device_id=1
    ):
        self.pluto = pluto
        self.control_port = control_port
        self.data_port = data_port
        self.samples_per_packet = samples_per_packet
        self.context_interval = context_interval

        # Stream ID
        self.stream_id = ((device_id & 0xFF) << 24) | 0x00

        # Packet encoders
        self.data_encoder = VRT49DataPacket(self.stream_id)
        self.context_encoder = VRT49ContextPacket(self.stream_id)

        # Sockets
        self.control_socket = None
        self.data_socket = None

        # Subscriber list for data multicast
        self.subscribers = set()  # Set of (ip, port) tuples
        self.subscribers_lock = threading.Lock()

        # Threading
        self._running = False
        self._control_thread = None
        self._streaming_thread = None

        # Statistics
        self.stats = {
            'packets_sent': 0,
            'bytes_sent': 0,
            'contexts_sent': 0,
            'reconfigs': 0,
            'start_time': 0,
        }

    def _create_sockets(self):
        """Create UDP sockets."""
        # Control socket (receive config)
        self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.control_socket.bind(('0.0.0.0', self.control_port))
        self.control_socket.settimeout(0.1)  # Non-blocking with timeout

        # Data socket (send IQ samples)
        self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.data_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)

    def _control_loop(self):
        """Control loop: receive and process configuration packets."""
        print("[Control] Listening for configuration on port", self.control_port)

        while self._running:
            try:
                data, addr = self.control_socket.recvfrom(4096)

                # Decode context packet
                config = VRT49ContextPacket.decode(data)

                print(f"[Control] Received config from {addr[0]}:{addr[1]}")

                # Apply configuration to Pluto
                reconfig_args = {}
                if config['sample_rate_hz']:
                    reconfig_args['sample_rate_hz'] = config['sample_rate_hz']
                if config['center_freq_hz']:
                    reconfig_args['center_freq_hz'] = config['center_freq_hz']
                if config['bandwidth_hz']:
                    reconfig_args['bandwidth_hz'] = config['bandwidth_hz']
                if config['gain_db'] is not None:
                    reconfig_args['gain_db'] = config['gain_db']

                if reconfig_args:
                    self.pluto.reconfigure(**reconfig_args)
                    self.stats['reconfigs'] += 1

                # Add sender to subscriber list
                with self.subscribers_lock:
                    self.subscribers.add((addr[0], self.data_port))
                    print(f"[Control] Added subscriber: {addr[0]}:{self.data_port} (total: {len(self.subscribers)})")

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    print(f"[Control] Error: {e}")

    def _send_context(self):
        """Send context packet to all subscribers."""
        ctx = self.context_encoder.encode(
            sample_rate_hz=self.pluto.sample_rate_hz,
            center_freq_hz=self.pluto.center_freq_hz,
            bandwidth_hz=self.pluto.bandwidth_hz,
            gain_db=self.pluto.gain_db
        )

        with self.subscribers_lock:
            for ip, port in self.subscribers:
                try:
                    self.data_socket.sendto(ctx, (ip, port))
                except:
                    pass

        self.stats['contexts_sent'] += 1

    def _streaming_loop(self):
        """Main streaming loop: receive from Pluto and send VITA49 packets."""
        print("[Streaming] Started")

        packets_since_context = 0
        sample_period = 1.0 / self.pluto.sample_rate_hz

        while self._running:
            # Receive from Pluto
            samples = self.pluto.receive()
            if not samples:
                time.sleep(0.001)
                continue

            timestamp = time.time()

            # Send context periodically
            if packets_since_context >= self.context_interval:
                self._send_context()
                packets_since_context = 0

            # Packetize and send to all subscribers
            offset = 0

            while offset < len(samples):
                end = min(offset + self.samples_per_packet, len(samples))
                pkt_samples = samples[offset:end]
                pkt_time = timestamp + (offset * sample_period)

                # Encode VITA49 packet
                data = self.data_encoder.encode(pkt_samples, pkt_time)

                # Send to all subscribers
                with self.subscribers_lock:
                    for ip, port in self.subscribers:
                        try:
                            self.data_socket.sendto(data, (ip, port))
                        except:
                            pass

                self.stats['packets_sent'] += 1
                self.stats['bytes_sent'] += len(data)
                offset = end
                packets_since_context += 1

        print("[Streaming] Stopped")

    def start(self):
        """Start server."""
        if self._running:
            return True

        if not self.pluto.connected:
            if not self.pluto.connect():
                return False

        self._create_sockets()

        # Send initial context (broadcast to 255.255.255.255)
        # This allows receivers to discover the stream
        self.subscribers.add(('255.255.255.255', self.data_port))
        self._send_context()
        self.subscribers.clear()

        self.stats['start_time'] = time.time()
        self._running = True

        # Start threads
        self._control_thread = threading.Thread(target=self._control_loop, daemon=True)
        self._streaming_thread = threading.Thread(target=self._streaming_loop, daemon=True)

        self._control_thread.start()
        self._streaming_thread.start()

        return True

    def stop(self):
        """Stop server."""
        self._running = False

        if self._control_thread:
            self._control_thread.join(timeout=2.0)
        if self._streaming_thread:
            self._streaming_thread.join(timeout=2.0)

        if self.control_socket:
            self.control_socket.close()
        if self.data_socket:
            self.data_socket.close()

        self.pluto.disconnect()

    def get_stats(self):
        """Get statistics."""
        elapsed = time.time() - self.stats['start_time']
        return {
            **self.stats,
            'elapsed_s': elapsed,
            'mbps': (self.stats['bytes_sent'] * 8 / 1e6) / elapsed if elapsed > 0 else 0,
            'pps': self.stats['packets_sent'] / elapsed if elapsed > 0 else 0,
            'subscribers': len(self.subscribers)
        }


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="VITA49 Standalone Streamer for Pluto+ (No Dependencies)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Stream to PC at 192.168.2.100
  python3 pluto_vita49_standalone.py --dest 192.168.2.100

  # Run on Pluto itself (local IIO)
  python3 pluto_vita49_standalone.py --uri local --dest 192.168.2.100

  # Custom frequency and gain
  python3 pluto_vita49_standalone.py --dest 192.168.2.100 --freq 5.8e9 --gain 30

Configuration:
  - Control port (receive config): 4990 UDP
  - Data port (send IQ samples): 4991 UDP
  - Supports multiple simultaneous receivers
  - Receivers auto-discovered when they send config packets
        """
    )

    parser.add_argument(
        '--uri', '-u',
        default="ip:192.168.2.1",
        help="Pluto URI (default: ip:192.168.2.1, use 'local' for on-device)"
    )
    parser.add_argument(
        '--dest', '-d',
        default=None,
        help="Initial destination IP (optional, auto-discovered from config packets)"
    )
    parser.add_argument(
        '--freq', '-f',
        type=float,
        default=2.4e9,
        help="Initial center frequency in Hz (default: 2.4e9)"
    )
    parser.add_argument(
        '--rate', '-r',
        type=float,
        default=30e6,
        help="Initial sample rate in Hz (default: 30e6)"
    )
    parser.add_argument(
        '--gain', '-g',
        type=float,
        default=20.0,
        help="Initial RX gain in dB (default: 20)"
    )
    parser.add_argument(
        '--buffer',
        type=int,
        default=8192,
        help="IIO buffer size in samples (default: 8192)"
    )

    args = parser.parse_args()

    # Handle local URI
    uri = args.uri
    if uri == 'local':
        uri = "local:"

    # Create Pluto interface
    pluto = PlutoInterface(uri=uri)
    pluto.sample_rate_hz = args.rate
    pluto.center_freq_hz = args.freq
    pluto.bandwidth_hz = args.rate * 0.8
    pluto.gain_db = args.gain
    pluto.buffer_size = args.buffer

    # Create server
    server = VITA49Server(
        pluto=pluto,
        control_port=4990,
        data_port=4991,
        samples_per_packet=360,
        context_interval=100
    )

    # Add initial destination if provided
    if args.dest:
        server.subscribers.add((args.dest, 4991))

    print("="*60)
    print("VITA49 Standalone Streamer for Pluto+")
    print("="*60)
    print(f"  Pluto URI:    {uri}")
    print(f"  Frequency:    {args.freq/1e9:.3f} GHz")
    print(f"  Sample Rate:  {args.rate/1e6:.1f} MSPS")
    print(f"  Gain:         {args.gain} dB")
    print(f"  Control Port: 4990 (receive config)")
    print(f"  Data Port:    4991 (send IQ)")
    if args.dest:
        print(f"  Initial Dest: {args.dest}")
    print("="*60)
    print("\nCapabilities:")
    print("  • Receives configuration via VITA49 Context packets")
    print("  • Auto-discovers receivers (send config to be added)")
    print("  • Supports multiple parallel receivers")
    print("  • Zero external dependencies (pure Python + pyadi-iio)")
    print("="*60)

    if not server.start():
        print("\n✗ Failed to start server")
        return 1

    print("\n✓ Server running. Press Ctrl+C to stop\n")

    try:
        while True:
            time.sleep(5)
            stats = server.get_stats()
            print(f"[{stats['elapsed_s']:.0f}s] "
                  f"Pkts: {stats['packets_sent']:,} | "
                  f"{stats['mbps']:.1f} Mbps | "
                  f"{stats['pps']:.0f} pps | "
                  f"Ctx: {stats['contexts_sent']} | "
                  f"Reconfig: {stats['reconfigs']} | "
                  f"Subs: {stats['subscribers']}")
    except KeyboardInterrupt:
        print("\n\nStopping...")
        server.stop()

    return 0


if __name__ == '__main__':
    sys.exit(main())
