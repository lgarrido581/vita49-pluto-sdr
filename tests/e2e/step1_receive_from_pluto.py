#!/usr/bin/env python3
"""
Step 1: Receive IQ samples from ADALM-Pluto+ using pyadi-iio

This script demonstrates direct reception from the Pluto+ SDR using pyadi-iio.
It receives samples and can optionally save them or pass them to the VITA49 streamer.

Usage:
    python test_e2e_step1_receive_from_pluto.py --uri ip:192.168.2.1 --freq 2.4e9
"""

import argparse
import logging
import time
import numpy as np
from typing import Optional, Callable

try:
    import adi
    HAS_ADI = True
except ImportError:
    HAS_ADI = False
    print("ERROR: pyadi-iio not installed. Install with: pip install pyadi-iio")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PlutoReceiver:
    """
    Simple receiver for ADALM-Pluto+ using pyadi-iio
    """

    def __init__(
        self,
        uri: str = "ip:192.168.2.1",
        center_freq_hz: float = 2.4e9,
        sample_rate_hz: float = 30e6,
        bandwidth_hz: float = 20e6,
        rx_gain_db: float = 20.0,
        buffer_size: int = 16384,
        rx_channel: int = 0
    ):
        if not HAS_ADI:
            raise RuntimeError("pyadi-iio not available")

        self.uri = uri
        self.center_freq_hz = center_freq_hz
        self.sample_rate_hz = sample_rate_hz
        self.bandwidth_hz = bandwidth_hz
        self.rx_gain_db = rx_gain_db
        self.buffer_size = buffer_size
        self.rx_channel = rx_channel

        self.sdr: Optional[adi.ad9361] = None
        self.connected = False

        # Statistics
        self.samples_received = 0
        self.buffers_received = 0
        self.start_time = 0.0

    def connect(self) -> bool:
        """Connect to Pluto+ and configure"""
        try:
            logger.info(f"Connecting to Pluto+ at {self.uri}")
            self.sdr = adi.ad9361(self.uri)

            # Configure RX
            logger.info(f"Configuring: {self.sample_rate_hz/1e6:.1f} MSPS @ {self.center_freq_hz/1e9:.3f} GHz")
            self.sdr.sample_rate = int(self.sample_rate_hz)
            self.sdr.rx_lo = int(self.center_freq_hz)
            self.sdr.rx_rf_bandwidth = int(self.bandwidth_hz)
            self.sdr.rx_buffer_size = self.buffer_size
            self.sdr.rx_enabled_channels = [self.rx_channel]

            # Set manual gain
            self.sdr.gain_control_mode_chan0 = "manual"
            self.sdr.rx_hardwaregain_chan0 = self.rx_gain_db

            self.connected = True
            self.start_time = time.time()

            logger.info("Connected successfully!")
            logger.info(f"  Sample Rate: {self.sdr.sample_rate/1e6:.1f} MSPS")
            logger.info(f"  RX LO: {self.sdr.rx_lo/1e9:.3f} GHz")
            logger.info(f"  Bandwidth: {self.sdr.rx_rf_bandwidth/1e6:.1f} MHz")
            logger.info(f"  Gain: {self.sdr.rx_hardwaregain_chan0} dB")
            logger.info(f"  Buffer Size: {self.sdr.rx_buffer_size} samples")

            return True

        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from Pluto+"""
        if self.sdr:
            try:
                self.sdr.rx_destroy_buffer()
            except:
                pass
            self.sdr = None
        self.connected = False
        logger.info("Disconnected")

    def receive_samples(self) -> Optional[np.ndarray]:
        """
        Receive one buffer of samples

        Returns:
            Complex64 numpy array of IQ samples, or None if error
        """
        if not self.connected or not self.sdr:
            logger.error("Not connected")
            return None

        try:
            samples = self.sdr.rx()

            # Convert to complex64
            if isinstance(samples, np.ndarray):
                samples = samples.astype(np.complex64)

            # Update statistics
            self.buffers_received += 1
            self.samples_received += len(samples)

            return samples

        except Exception as e:
            logger.error(f"Error receiving samples: {e}")
            return None

    def receive_continuous(
        self,
        callback: Callable[[np.ndarray], None],
        duration_seconds: Optional[float] = None
    ):
        """
        Receive samples continuously and call callback for each buffer

        Args:
            callback: Function to call with each buffer: callback(samples)
            duration_seconds: Optional duration to receive (None = forever)
        """
        if not self.connected:
            logger.error("Not connected")
            return

        logger.info("Starting continuous reception...")
        start_time = time.time()

        try:
            while True:
                # Check duration
                if duration_seconds and (time.time() - start_time) >= duration_seconds:
                    logger.info(f"Duration {duration_seconds}s reached, stopping")
                    break

                # Receive samples
                samples = self.receive_samples()
                if samples is not None:
                    callback(samples)

                # Print statistics periodically
                if self.buffers_received % 100 == 0:
                    elapsed = time.time() - self.start_time
                    msps = (self.samples_received / elapsed) / 1e6
                    logger.info(f"Received {self.buffers_received} buffers, "
                              f"{self.samples_received/1e6:.1f} Msamples, "
                              f"{msps:.1f} MSPS")

        except KeyboardInterrupt:
            logger.info("Stopped by user")

    def get_statistics(self) -> dict:
        """Get reception statistics"""
        elapsed = time.time() - self.start_time if self.start_time else 0
        return {
            'buffers_received': self.buffers_received,
            'samples_received': self.samples_received,
            'elapsed_time_s': elapsed,
            'avg_msps': (self.samples_received / elapsed / 1e6) if elapsed > 0 else 0
        }


def main():
    parser = argparse.ArgumentParser(
        description="Receive IQ samples from ADALM-Pluto+ using pyadi-iio"
    )
    parser.add_argument(
        '--uri', '-u',
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
        '--duration', '-d',
        type=float,
        default=None,
        help="Duration in seconds (default: run until Ctrl+C)"
    )
    parser.add_argument(
        '--save',
        type=str,
        default=None,
        help="Save samples to file (numpy format)"
    )

    args = parser.parse_args()

    if not HAS_ADI:
        print("ERROR: pyadi-iio is required but not installed")
        print("Install with: pip install pyadi-iio")
        return 1

    # Create receiver
    receiver = PlutoReceiver(
        uri=args.uri,
        center_freq_hz=args.freq,
        sample_rate_hz=args.rate,
        rx_gain_db=args.gain
    )

    # Connect
    if not receiver.connect():
        return 1

    # Buffer for saving
    saved_samples = [] if args.save else None

    # Callback to process samples
    def on_samples(samples):
        # Calculate power
        power_dbfs = 10 * np.log10(np.mean(np.abs(samples)**2) + 1e-10)

        # Show first few samples
        if receiver.buffers_received <= 3:
            logger.info(f"Sample buffer {receiver.buffers_received}:")
            logger.info(f"  Length: {len(samples)}")
            logger.info(f"  Power: {power_dbfs:.1f} dBFS")
            logger.info(f"  First sample: {samples[0]:.6f}")
            logger.info(f"  Mean abs: {np.mean(np.abs(samples)):.6f}")

        # Save if requested
        if saved_samples is not None:
            saved_samples.append(samples)

    try:
        # Receive
        receiver.receive_continuous(on_samples, duration_seconds=args.duration)

        # Print final statistics
        stats = receiver.get_statistics()
        print("\nReception Statistics:")
        print(f"  Buffers: {stats['buffers_received']}")
        print(f"  Samples: {stats['samples_received']:,}")
        print(f"  Duration: {stats['elapsed_time_s']:.1f} s")
        print(f"  Average: {stats['avg_msps']:.1f} MSPS")

        # Save if requested
        if args.save and saved_samples:
            all_samples = np.concatenate(saved_samples)
            np.save(args.save, all_samples)
            logger.info(f"Saved {len(all_samples):,} samples to {args.save}")

    finally:
        receiver.disconnect()

    return 0


if __name__ == '__main__':
    exit(main())
