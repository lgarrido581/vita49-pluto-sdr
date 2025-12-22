#!/usr/bin/env python3
"""
Step 2: VITA49 Re-Streamer for Pluto+ IQ Data

This script receives IQ samples from Pluto+ via pyadi-iio and re-streams them
as VITA49 UDP packets. This allows you to test the VITA49 protocol with real
SDR data without needing to run VITA49 on the embedded Pluto+ ARM.

Architecture:
    Pluto+ SDR → pyadi-iio → This Script → VITA49 UDP → Receiver App

Usage:
    python test_e2e_step2_vita49_restreamer.py --pluto-uri ip:192.168.2.1 \
        --dest 127.0.0.1 --port 4991 --freq 2.4e9
"""

import argparse
import logging
import signal
import sys
import threading
import time
import numpy as np
from typing import Optional

try:
    import adi
    HAS_ADI = True
except ImportError:
    HAS_ADI = False
    print("ERROR: pyadi-iio not installed. Install with: pip install pyadi-iio")

from vita49_packets import (
    VRTSignalDataPacket,
    VRTContextPacket,
    VRTTimestamp,
    create_stream_id
)
import socket

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VITA49Restreamer:
    """
    Receives samples from Pluto+ and re-streams as VITA49 packets
    """

    def __init__(
        self,
        pluto_uri: str = "ip:192.168.2.1",
        center_freq_hz: float = 2.4e9,
        sample_rate_hz: float = 30e6,
        bandwidth_hz: float = 20e6,
        rx_gain_db: float = 20.0,
        buffer_size: int = 16384,
        destination: str = "127.0.0.1",
        port: int = 4991,
        samples_per_packet: int = 360,
        context_interval: int = 100
    ):
        self.pluto_uri = pluto_uri
        self.center_freq_hz = center_freq_hz
        self.sample_rate_hz = sample_rate_hz
        self.bandwidth_hz = bandwidth_hz
        self.rx_gain_db = rx_gain_db
        self.buffer_size = buffer_size

        self.destination = destination
        self.port = port
        self.samples_per_packet = samples_per_packet
        self.context_interval = context_interval

        # Pluto SDR
        self.sdr: Optional[adi.ad9361] = None
        self.connected = False

        # VITA49 UDP socket
        self.socket: Optional[socket.socket] = None

        # Stream ID
        self.stream_id = create_stream_id(channel=0, device_id=1)

        # Threading
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Statistics
        self.stats = {
            'sdr_buffers_received': 0,
            'sdr_samples_received': 0,
            'vita49_packets_sent': 0,
            'vita49_bytes_sent': 0,
            'context_packets_sent': 0,
            'start_time': 0.0
        }
        self._packet_counter = 0

    def connect_pluto(self) -> bool:
        """Connect to Pluto+ SDR"""
        try:
            logger.info(f"Connecting to Pluto+ at {self.pluto_uri}")
            self.sdr = adi.ad9361(self.pluto_uri)

            # Auto-adjust bandwidth to match sample rate if too wide
            # AD9361 requires bandwidth <= sample_rate
            if self.bandwidth_hz > self.sample_rate_hz:
                self.bandwidth_hz = self.sample_rate_hz * 0.8
                logger.info(f"Auto-adjusted bandwidth to {self.bandwidth_hz/1e6:.1f} MHz (0.8 × sample rate)")

            # Configure RX - order matters!
            # Set sample rate first as it affects valid ranges for other parameters
            self.sdr.sample_rate = int(self.sample_rate_hz)
            self.sdr.rx_lo = int(self.center_freq_hz)
            self.sdr.rx_rf_bandwidth = int(self.bandwidth_hz)
            self.sdr.rx_buffer_size = self.buffer_size
            self.sdr.rx_enabled_channels = [0]

            # Manual gain
            self.sdr.gain_control_mode_chan0 = "manual"
            self.sdr.rx_hardwaregain_chan0 = self.rx_gain_db

            self.connected = True

            # Log actual values (may differ from requested due to hardware constraints)
            logger.info("Pluto+ connected successfully!")
            logger.info(f"  Sample Rate: {self.sdr.sample_rate/1e6:.3f} MSPS (requested: {self.sample_rate_hz/1e6:.3f})")
            logger.info(f"  RX LO: {self.sdr.rx_lo/1e6:.3f} MHz (requested: {self.center_freq_hz/1e6:.3f})")
            logger.info(f"  Bandwidth: {self.sdr.rx_rf_bandwidth/1e6:.3f} MHz (requested: {self.bandwidth_hz/1e6:.3f})")
            logger.info(f"  Gain: {self.sdr.rx_hardwaregain_chan0} dB (requested: {self.rx_gain_db})")
            logger.info(f"  Buffer Size: {self.sdr.rx_buffer_size} samples")

            return True

        except Exception as e:
            logger.error(f"Failed to connect to Pluto+: {e}")
            return False

    def create_socket(self) -> bool:
        """Create UDP socket for VITA49 streaming"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
            logger.info(f"VITA49 streaming to {self.destination}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"Failed to create socket: {e}")
            return False

    def send_context_packet(self, timestamp: float):
        """Send VITA49 context packet"""
        context = VRTContextPacket(
            stream_id=self.stream_id,
            timestamp=VRTTimestamp.from_time(timestamp),
            bandwidth_hz=self.bandwidth_hz,
            rf_reference_frequency_hz=self.center_freq_hz,
            sample_rate_hz=self.sample_rate_hz,
            gain_db=self.rx_gain_db
        )

        try:
            data = context.encode()
            self.socket.sendto(data, (self.destination, self.port))
            self.stats['context_packets_sent'] += 1
            logger.debug("Sent context packet")

        except Exception as e:
            logger.error(f"Failed to send context packet: {e}")

    def send_data_packet(self, samples: np.ndarray, timestamp: float):
        """Send VITA49 signal data packet"""
        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=samples,
            stream_id=self.stream_id,
            sample_rate=self.sample_rate_hz,
            timestamp=timestamp,
            packet_count=self._packet_counter
        )

        try:
            data = packet.encode()
            self.socket.sendto(data, (self.destination, self.port))

            # Update stats
            self.stats['vita49_packets_sent'] += 1
            self.stats['vita49_bytes_sent'] += len(data)

            # Increment counter (4-bit, wraps at 16)
            self._packet_counter = (self._packet_counter + 1) & 0xF

        except Exception as e:
            logger.error(f"Failed to send data packet: {e}")

    def _streaming_loop(self):
        """Main streaming loop"""
        logger.info("Streaming loop started")

        packets_since_context = 0

        while self._running:
            try:
                # Receive from Pluto+
                samples = self.sdr.rx()
                if samples is None:
                    continue

                # Convert to complex64 and normalize
                # pyadi-iio returns samples in ADC units (~±2048 for 12-bit)
                # Normalize to ±1.0 range for VITA49 encoding
                samples = samples.astype(np.complex64)
                samples = samples / 2048.0  # Normalize to ±1.0 range

                # Update stats
                self.stats['sdr_buffers_received'] += 1
                self.stats['sdr_samples_received'] += len(samples)

                # Get timestamp
                timestamp = time.time()

                # Send context packet periodically
                if packets_since_context >= self.context_interval:
                    self.send_context_packet(timestamp)
                    packets_since_context = 0

                # Packetize and send
                offset = 0
                sample_period = 1.0 / self.sample_rate_hz

                while offset < len(samples):
                    # Get samples for this packet
                    end = min(offset + self.samples_per_packet, len(samples))
                    packet_samples = samples[offset:end]

                    # Calculate precise timestamp
                    packet_timestamp = timestamp + (offset * sample_period)

                    # Send VITA49 packet
                    self.send_data_packet(packet_samples, packet_timestamp)

                    offset = end
                    packets_since_context += 1

                # Print statistics periodically
                if self.stats['sdr_buffers_received'] % 100 == 0:
                    elapsed = time.time() - self.stats['start_time']
                    mbps = (self.stats['vita49_bytes_sent'] * 8 / 1e6) / elapsed if elapsed > 0 else 0
                    logger.info(f"SDR buffers: {self.stats['sdr_buffers_received']}, "
                              f"VITA49 packets: {self.stats['vita49_packets_sent']}, "
                              f"Throughput: {mbps:.2f} Mbps")

            except Exception as e:
                logger.error(f"Streaming error: {e}")
                if not self._running:
                    break

        logger.info("Streaming loop stopped")

    def start(self) -> bool:
        """Start streaming"""
        if self._running:
            logger.warning("Already running")
            return True

        # Connect to Pluto+
        if not self.connect_pluto():
            return False

        # Create socket
        if not self.create_socket():
            return False

        # Reset stats
        self.stats['start_time'] = time.time()
        self.stats['sdr_buffers_received'] = 0
        self.stats['sdr_samples_received'] = 0
        self.stats['vita49_packets_sent'] = 0
        self.stats['vita49_bytes_sent'] = 0
        self.stats['context_packets_sent'] = 0

        # Start thread
        self._running = True
        self._thread = threading.Thread(target=self._streaming_loop, daemon=True)
        self._thread.start()

        logger.info("VITA49 re-streamer started successfully!")
        return True

    def stop(self):
        """Stop streaming"""
        if not self._running:
            return

        logger.info("Stopping...")
        self._running = False

        if self._thread:
            self._thread.join(timeout=2.0)

        if self.socket:
            self.socket.close()
            self.socket = None

        if self.sdr:
            try:
                self.sdr.rx_destroy_buffer()
            except:
                pass
            self.sdr = None

        logger.info("Stopped")

    def get_statistics(self) -> dict:
        """Get streaming statistics"""
        elapsed = time.time() - self.stats['start_time']
        stats = self.stats.copy()
        stats['elapsed_time_s'] = elapsed
        stats['vita49_mbps'] = (stats['vita49_bytes_sent'] * 8 / 1e6) / elapsed if elapsed > 0 else 0
        stats['sdr_msps'] = (stats['sdr_samples_received'] / 1e6) / elapsed if elapsed > 0 else 0
        return stats


def main():
    parser = argparse.ArgumentParser(
        description="VITA49 Re-Streamer for Pluto+ IQ Data"
    )
    parser.add_argument(
        '--pluto-uri', '-u',
        default="ip:192.168.2.1",
        help="Pluto+ URI (default: ip:192.168.2.1)"
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
        help="VITA49 destination IP (default: 127.0.0.1)"
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=4991,
        help="VITA49 destination port (default: 4991)"
    )
    parser.add_argument(
        '--pkt-size',
        type=int,
        default=360,
        help="Samples per VITA49 packet (default: 360)"
    )

    args = parser.parse_args()

    if not HAS_ADI:
        print("ERROR: pyadi-iio is required but not installed")
        print("Install with: pip install pyadi-iio")
        return 1

    # Create re-streamer
    restreamer = VITA49Restreamer(
        pluto_uri=args.pluto_uri,
        center_freq_hz=args.freq,
        sample_rate_hz=args.rate,
        rx_gain_db=args.gain,
        destination=args.dest,
        port=args.port,
        samples_per_packet=args.pkt_size
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\nStopping re-streamer...")
        restreamer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Start
    if not restreamer.start():
        print("Failed to start re-streamer")
        return 1

    print("\n" + "="*60)
    print("VITA49 Re-Streamer Running")
    print("="*60)
    print(f"  Pluto+ URI: {args.pluto_uri}")
    print(f"  Frequency: {args.freq/1e9:.3f} GHz")
    print(f"  Sample Rate: {args.rate/1e6:.1f} MSPS")
    print(f"  Gain: {args.gain} dB")
    print(f"  VITA49 Destination: {args.dest}:{args.port}")
    print(f"  Samples/Packet: {args.pkt_size}")
    print("="*60)
    print("Press Ctrl+C to stop\n")

    # Run and print stats
    try:
        while True:
            time.sleep(5)
            stats = restreamer.get_statistics()
            print(f"[Stats] SDR: {stats['sdr_buffers_received']} buffers, "
                  f"{stats['sdr_msps']:.1f} MSPS | "
                  f"VITA49: {stats['vita49_packets_sent']} packets, "
                  f"{stats['vita49_mbps']:.2f} Mbps | "
                  f"Context: {stats['context_packets_sent']}")

    except KeyboardInterrupt:
        pass

    restreamer.stop()
    return 0


if __name__ == '__main__':
    exit(main())
