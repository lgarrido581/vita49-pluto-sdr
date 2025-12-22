#!/usr/bin/env python3
"""
Example: Running Multiple VITA49 Receivers in Parallel

This demonstrates how to run multiple signal processing algorithms
simultaneously on the same VITA49 stream from Pluto.

Architecture:
    Pluto (VITA49) → Network → Multiple Receivers (parallel)
                                - Energy Detector
                                - Spectrum Analyzer
                                - Signal Classifier
                                - Data Logger

Usage:
    python example_parallel_receivers.py --port 4991
"""

import argparse
import numpy as np
import threading
import time
from collections import deque
from vita49_stream_server import VITA49StreamClient
from vita49_packets import VRTSignalDataPacket


# =============================================================================
# Base Receiver Class
# =============================================================================

class BaseVITA49Receiver:
    """
    Base class for all VITA49 receivers.

    Inherit from this and override process_samples() to implement your algorithm.
    """

    def __init__(self, name, port=4991):
        self.name = name
        self.port = port
        self.client = VITA49StreamClient(port=port)

        # Callbacks
        self.client.on_samples(self._on_samples)
        self.client.on_context(self._on_context)

        # Stream metadata (updated from context packets)
        self.sample_rate_hz = 30e6
        self.center_freq_hz = 2.4e9
        self.bandwidth_hz = 20e6

        # Statistics
        self.packets_received = 0
        self.samples_received = 0
        self.start_time = time.time()

        self._running = False

    def _on_context(self, context_data):
        """Handle context packet (stream metadata)"""
        try:
            from vita49_packets import VRTContextPacket
            ctx = VRTContextPacket.decode(context_data)

            if ctx.sample_rate_hz:
                self.sample_rate_hz = ctx.sample_rate_hz
            if ctx.rf_reference_frequency_hz:
                self.center_freq_hz = ctx.rf_reference_frequency_hz
            if ctx.bandwidth_hz:
                self.bandwidth_hz = ctx.bandwidth_hz

            print(f"[{self.name}] Config: {self.sample_rate_hz/1e6:.1f} MSPS @ {self.center_freq_hz/1e9:.3f} GHz")

        except Exception as e:
            print(f"[{self.name}] Context parse error: {e}")

    def _on_samples(self, packet, samples):
        """Internal sample handler - calls user's process_samples()"""
        self.packets_received += 1
        self.samples_received += len(samples)

        # Call user implementation
        try:
            self.process_samples(packet, samples)
        except Exception as e:
            print(f"[{self.name}] Processing error: {e}")

    def process_samples(self, packet, samples):
        """
        Override this method to implement your signal processing algorithm.

        Args:
            packet: VRTSignalDataPacket object with metadata
            samples: numpy array of complex64 IQ samples
        """
        raise NotImplementedError("Subclass must implement process_samples()")

    def start(self):
        """Start receiving"""
        print(f"[{self.name}] Starting on port {self.port}")
        self._running = True
        self.client.start()

    def stop(self):
        """Stop receiving"""
        print(f"[{self.name}] Stopping")
        self._running = False
        self.client.stop()

    def get_stats(self):
        """Get receiver statistics"""
        elapsed = time.time() - self.start_time
        return {
            'name': self.name,
            'packets': self.packets_received,
            'samples': self.samples_received,
            'elapsed_s': elapsed,
            'sample_rate_msps': (self.samples_received / 1e6) / elapsed if elapsed > 0 else 0
        }


# =============================================================================
# Example Receiver Implementations
# =============================================================================

class EnergyDetectorReceiver(BaseVITA49Receiver):
    """
    Energy detection receiver using CFAR (Constant False Alarm Rate).

    Detects signal presence by comparing energy to noise floor.
    """

    def __init__(self, port=4991, threshold_db=10.0, averaging=100):
        super().__init__("EnergyDetector", port)
        self.threshold_db = threshold_db
        self.noise_history = deque(maxlen=averaging)
        self.detection_count = 0

    def process_samples(self, packet, samples):
        """Detect signal energy above noise floor"""
        # Compute instantaneous power
        power_linear = np.mean(np.abs(samples)**2)
        power_dbfs = 10 * np.log10(power_linear + 1e-10)

        # Update noise floor estimate (using minimum)
        self.noise_history.append(power_dbfs)
        noise_floor = np.percentile(list(self.noise_history), 10) if self.noise_history else -100

        # Detection threshold
        threshold = noise_floor + self.threshold_db

        # Check for detection
        if power_dbfs > threshold:
            self.detection_count += 1
            snr = power_dbfs - noise_floor

            # Report every 100th detection to avoid spam
            if self.detection_count % 100 == 0:
                timestamp = packet.timestamp.to_time() if packet.timestamp else time.time()
                print(f"[{self.name}] DETECTION #{self.detection_count}: "
                      f"Power={power_dbfs:.1f} dBFS, SNR={snr:.1f} dB @ {timestamp:.3f}s")


class SpectrumAnalyzerReceiver(BaseVITA49Receiver):
    """
    Spectrum analyzer receiver.

    Computes FFT and tracks peak frequencies.
    """

    def __init__(self, port=4991, fft_size=1024, report_interval=5.0):
        super().__init__("SpectrumAnalyzer", port)
        self.fft_size = fft_size
        self.report_interval = report_interval
        self.last_report = time.time()

        self.sample_buffer = []
        self.peak_freqs = []

    def process_samples(self, packet, samples):
        """Analyze spectrum and find peak frequencies"""
        # Accumulate samples
        self.sample_buffer.extend(samples)

        # Process when we have enough
        if len(self.sample_buffer) >= self.fft_size:
            fft_samples = np.array(self.sample_buffer[:self.fft_size])
            self.sample_buffer = self.sample_buffer[self.fft_size:]

            # Compute FFT
            window = np.hanning(len(fft_samples))
            spectrum = np.fft.fftshift(np.fft.fft(fft_samples * window))
            spectrum_mag = np.abs(spectrum)

            # Find peak
            peak_bin = np.argmax(spectrum_mag)
            peak_freq_offset = (peak_bin - len(spectrum) // 2) * (self.sample_rate_hz / len(spectrum))
            peak_freq_abs = self.center_freq_hz + peak_freq_offset

            self.peak_freqs.append(peak_freq_abs)

            # Report periodically
            now = time.time()
            if now - self.last_report >= self.report_interval:
                avg_peak = np.mean(self.peak_freqs)
                print(f"[{self.name}] Peak frequency: {avg_peak/1e6:.3f} MHz "
                      f"(offset: {(avg_peak - self.center_freq_hz)/1e6:+.3f} MHz)")
                self.peak_freqs = []
                self.last_report = now


class SignalClassifierReceiver(BaseVITA49Receiver):
    """
    Simple signal classifier based on spectral features.

    Classifies signals as: Noise, CW (Continuous Wave), or Modulated
    """

    def __init__(self, port=4991, classification_interval=2.0):
        super().__init__("SignalClassifier", port)
        self.classification_interval = classification_interval
        self.last_classification = time.time()

        self.sample_buffer = []
        self.fft_size = 512

    def classify_signal(self, samples):
        """Simple classification based on spectral properties"""
        # Compute spectrum
        window = np.hanning(len(samples))
        spectrum = np.fft.fft(samples * window)
        spectrum_mag = np.abs(spectrum)

        # Features
        power = np.mean(np.abs(samples)**2)
        peak_to_avg = np.max(spectrum_mag) / np.mean(spectrum_mag)
        spectral_spread = np.std(spectrum_mag) / np.mean(spectrum_mag)

        # Simple classification rules
        if power < 0.001:  # Very low power
            return "NOISE"
        elif peak_to_avg > 20 and spectral_spread < 5:  # Strong narrow peak
            return "CW"
        else:
            return "MODULATED"

    def process_samples(self, packet, samples):
        """Classify signal type"""
        self.sample_buffer.extend(samples)

        # Classify periodically
        now = time.time()
        if now - self.last_classification >= self.classification_interval:
            if len(self.sample_buffer) >= self.fft_size:
                classification_samples = np.array(self.sample_buffer[:self.fft_size])

                signal_type = self.classify_signal(classification_samples)
                print(f"[{self.name}] Classification: {signal_type}")

                self.sample_buffer = []
                self.last_classification = now


class DataLoggerReceiver(BaseVITA49Receiver):
    """
    Data logger receiver.

    Logs samples to file in binary format for later analysis.
    """

    def __init__(self, port=4991, output_file="iq_samples.bin", max_samples=1000000):
        super().__init__("DataLogger", port)
        self.output_file = output_file
        self.max_samples = max_samples

        self.file_handle = open(output_file, 'wb')
        self.samples_written = 0

        print(f"[{self.name}] Logging to {output_file} (max {max_samples/1e6:.1f}M samples)")

    def process_samples(self, packet, samples):
        """Log samples to file"""
        if self.samples_written < self.max_samples:
            # Write as complex64 binary
            samples_to_write = min(len(samples), self.max_samples - self.samples_written)
            samples[:samples_to_write].tofile(self.file_handle)
            self.samples_written += samples_to_write

            # Report progress every 100k samples
            if self.samples_written % 100000 == 0:
                print(f"[{self.name}] Logged {self.samples_written/1e6:.1f}M samples "
                      f"({self.samples_written/self.max_samples*100:.0f}%)")
        else:
            # Finished logging
            if self.file_handle:
                self.file_handle.close()
                self.file_handle = None
                print(f"[{self.name}] ✓ Logging complete: {self.output_file} "
                      f"({self.samples_written/1e6:.1f}M samples)")

    def stop(self):
        """Close file on stop"""
        if self.file_handle:
            self.file_handle.close()
        super().stop()


# =============================================================================
# Parallel Receiver Manager
# =============================================================================

class ParallelReceiverManager:
    """
    Manages multiple receivers running in parallel.
    """

    def __init__(self, receivers):
        self.receivers = receivers
        self.threads = []

    def start_all(self):
        """Start all receivers in parallel threads"""
        print("="*60)
        print("Starting Parallel VITA49 Receivers")
        print("="*60)

        for receiver in self.receivers:
            thread = threading.Thread(target=receiver.start, daemon=True)
            thread.start()
            self.threads.append(thread)
            time.sleep(0.1)  # Stagger starts

        print(f"\n✓ {len(self.receivers)} receivers running in parallel")
        print("Press Ctrl+C to stop\n")

    def stop_all(self):
        """Stop all receivers"""
        print("\nStopping all receivers...")
        for receiver in self.receivers:
            receiver.stop()

        # Wait for threads
        for thread in self.threads:
            thread.join(timeout=2.0)

    def print_stats(self):
        """Print statistics for all receivers"""
        print("\n" + "="*60)
        print("Receiver Statistics")
        print("="*60)

        for receiver in self.receivers:
            stats = receiver.get_stats()
            print(f"{stats['name']:20s}: {stats['packets']:6d} pkts, "
                  f"{stats['samples']/1e6:6.1f}M samples, "
                  f"{stats['sample_rate_msps']:5.1f} MSPS")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="VITA49 Parallel Receivers Example",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This example demonstrates running multiple receivers in parallel:
  - Energy Detector: CFAR-based signal detection
  - Spectrum Analyzer: Peak frequency tracking
  - Signal Classifier: Noise/CW/Modulated classification
  - Data Logger: Save samples to file

All receivers process the SAME IQ stream simultaneously.

Before running:
  1. Deploy and start Pluto streamer
  2. Configure Pluto with vita49_config_client.py

Example:
  python example_parallel_receivers.py --port 4991
        """
    )

    parser.add_argument(
        '--port', '-p',
        type=int,
        default=4991,
        help="VITA49 UDP port (default: 4991)"
    )
    parser.add_argument(
        '--log-samples',
        type=int,
        default=1000000,
        help="Max samples to log (default: 1M)"
    )
    parser.add_argument(
        '--no-logger',
        action='store_true',
        help="Disable data logger"
    )

    args = parser.parse_args()

    # Create receivers
    receivers = [
        EnergyDetectorReceiver(port=args.port, threshold_db=10.0),
        SpectrumAnalyzerReceiver(port=args.port, fft_size=1024),
        SignalClassifierReceiver(port=args.port),
    ]

    if not args.no_logger:
        receivers.append(
            DataLoggerReceiver(port=args.port, max_samples=args.log_samples)
        )

    # Create manager
    manager = ParallelReceiverManager(receivers)

    # Start all receivers
    manager.start_all()

    # Monitor and print stats
    try:
        while True:
            time.sleep(10)
            manager.print_stats()

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        manager.stop_all()
        manager.print_stats()

    print("\n✓ All receivers stopped")
    return 0


if __name__ == '__main__':
    exit(main())
