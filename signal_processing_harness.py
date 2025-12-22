#!/usr/bin/env python3
"""
VITA 49 Signal Processing Test Harness

This module provides tools for receiving VITA 49 streams and running
signal processing algorithms on the received IQ data. It's designed
to help develop and test RF signal detection algorithms before
transitioning to production VITA 49-compatible SDRs.

Features:
- Receive VITA 49 streams from Pluto+ or other VRT sources
- Real-time signal processing with configurable algorithms
- Built-in detection algorithms (energy, cyclostationary, etc.)
- Spectrogram and waterfall displays
- Recording to SigMF format
- Performance benchmarking

Usage:
    # Basic signal processing
    harness = SignalProcessingHarness(port=4991)
    harness.add_detector(EnergyDetector(threshold_db=-20))
    harness.start()

    # With recording
    harness.enable_recording("capture.sigmf")

Author: Pluto+ Radar Emulator Project
License: MIT
"""

import asyncio
import json
import logging
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any, Tuple
import numpy as np
from scipy import signal as scipy_signal

from vita49_packets import (
    VRTSignalDataPacket,
    VRTContextPacket,
    VRTHeader,
    VRTTimestamp,
    PacketType
)
from vita49_stream_server import VITA49StreamClient


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# Detection Result Types
# =============================================================================

class DetectionType(Enum):
    """Types of signal detections"""
    ENERGY = "energy"
    CYCLOSTATIONARY = "cyclostationary"
    MATCHED_FILTER = "matched_filter"
    FEATURE = "feature"
    ML = "machine_learning"


@dataclass
class Detection:
    """A signal detection result"""
    detection_type: DetectionType
    timestamp: float
    frequency_hz: float
    bandwidth_hz: float
    snr_db: float
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'type': self.detection_type.value,
            'timestamp': self.timestamp,
            'frequency_hz': self.frequency_hz,
            'bandwidth_hz': self.bandwidth_hz,
            'snr_db': self.snr_db,
            'confidence': self.confidence,
            'metadata': self.metadata
        }


@dataclass
class ProcessingResult:
    """Result from a processing block"""
    detections: List[Detection] = field(default_factory=list)
    spectrum: Optional[np.ndarray] = None
    waterfall_line: Optional[np.ndarray] = None
    metrics: Dict[str, float] = field(default_factory=dict)


# =============================================================================
# Signal Detector Base Class
# =============================================================================

class SignalDetector(ABC):
    """Abstract base class for signal detectors"""

    def __init__(self, name: str = "detector"):
        self.name = name
        self.enabled = True
        self.detection_count = 0

    @abstractmethod
    def process(
        self,
        samples: np.ndarray,
        sample_rate: float,
        center_freq: float,
        timestamp: float
    ) -> List[Detection]:
        """
        Process samples and return detections.

        Args:
            samples: Complex IQ samples
            sample_rate: Sample rate in Hz
            center_freq: Center frequency in Hz
            timestamp: Timestamp of first sample

        Returns:
            List of Detection objects
        """
        pass

    def reset(self):
        """Reset detector state"""
        self.detection_count = 0


# =============================================================================
# Built-in Detectors
# =============================================================================

class EnergyDetector(SignalDetector):
    """
    Simple energy-based signal detector.

    Detects signals by comparing spectral energy to a threshold.
    Good for wideband signals with unknown characteristics.
    """

    def __init__(
        self,
        threshold_db: float = -20.0,
        fft_size: int = 1024,
        averaging: int = 4,
        min_bandwidth_hz: float = 100e3,
        name: str = "energy_detector"
    ):
        super().__init__(name)
        self.threshold_db = threshold_db
        self.fft_size = fft_size
        self.averaging = averaging
        self.min_bandwidth_hz = min_bandwidth_hz

        # Noise floor estimation (running average)
        self._noise_floor_db = -100.0
        self._noise_alpha = 0.1

    def process(
        self,
        samples: np.ndarray,
        sample_rate: float,
        center_freq: float,
        timestamp: float
    ) -> List[Detection]:
        detections = []

        # Compute averaged spectrum
        n_ffts = len(samples) // self.fft_size
        if n_ffts < self.averaging:
            return detections

        spectrum_sum = np.zeros(self.fft_size)
        window = np.hanning(self.fft_size)

        for i in range(min(n_ffts, self.averaging)):
            segment = samples[i * self.fft_size:(i + 1) * self.fft_size]
            spectrum = np.abs(np.fft.fftshift(np.fft.fft(segment * window)))**2
            spectrum_sum += spectrum

        spectrum_avg = spectrum_sum / self.averaging
        spectrum_db = 10 * np.log10(spectrum_avg + 1e-10)

        # Update noise floor estimate (use lower 25% of spectrum)
        sorted_spectrum = np.sort(spectrum_db)
        noise_estimate = np.mean(sorted_spectrum[:len(sorted_spectrum)//4])
        self._noise_floor_db = (
            (1 - self._noise_alpha) * self._noise_floor_db +
            self._noise_alpha * noise_estimate
        )

        # Frequency bins
        freq_bins = np.fft.fftshift(np.fft.fftfreq(self.fft_size, 1/sample_rate))

        # Find peaks above threshold
        threshold = self._noise_floor_db + self.threshold_db
        above_threshold = spectrum_db > threshold

        # Group contiguous bins into detections
        min_bins = int(self.min_bandwidth_hz / (sample_rate / self.fft_size))
        regions = self._find_regions(above_threshold, min_bins)

        for start_bin, end_bin in regions:
            # Calculate detection parameters
            region_spectrum = spectrum_db[start_bin:end_bin]
            peak_bin = start_bin + np.argmax(region_spectrum)

            freq_start = freq_bins[start_bin]
            freq_end = freq_bins[end_bin - 1]
            bandwidth = freq_end - freq_start
            peak_freq = freq_bins[peak_bin]

            snr = np.max(region_spectrum) - self._noise_floor_db

            detection = Detection(
                detection_type=DetectionType.ENERGY,
                timestamp=timestamp,
                frequency_hz=center_freq + peak_freq,
                bandwidth_hz=bandwidth,
                snr_db=snr,
                confidence=min(1.0, snr / 20.0),  # Simple confidence mapping
                metadata={
                    'noise_floor_db': self._noise_floor_db,
                    'peak_power_db': np.max(region_spectrum),
                    'start_freq_hz': center_freq + freq_start,
                    'end_freq_hz': center_freq + freq_end
                }
            )
            detections.append(detection)
            self.detection_count += 1

        return detections

    def _find_regions(
        self,
        mask: np.ndarray,
        min_length: int
    ) -> List[Tuple[int, int]]:
        """Find contiguous regions in boolean mask"""
        regions = []
        in_region = False
        start = 0

        for i, val in enumerate(mask):
            if val and not in_region:
                start = i
                in_region = True
            elif not val and in_region:
                if i - start >= min_length:
                    regions.append((start, i))
                in_region = False

        if in_region and len(mask) - start >= min_length:
            regions.append((start, len(mask)))

        return regions


class CFARDetector(SignalDetector):
    """
    Constant False Alarm Rate (CFAR) detector.

    Adaptive threshold based on local noise estimation.
    More robust to varying noise conditions than fixed threshold.
    """

    def __init__(
        self,
        guard_cells: int = 4,
        training_cells: int = 16,
        pfa: float = 1e-4,
        fft_size: int = 1024,
        name: str = "cfar_detector"
    ):
        super().__init__(name)
        self.guard_cells = guard_cells
        self.training_cells = training_cells
        self.pfa = pfa
        self.fft_size = fft_size

        # Calculate threshold factor from Pfa
        # For CA-CFAR: Pfa = (1 + T/N)^(-N) where N = training cells
        # Solving: T = N * (Pfa^(-1/N) - 1)
        self.threshold_factor = training_cells * (pfa ** (-1/training_cells) - 1)

    def process(
        self,
        samples: np.ndarray,
        sample_rate: float,
        center_freq: float,
        timestamp: float
    ) -> List[Detection]:
        detections = []

        if len(samples) < self.fft_size:
            return detections

        # Compute spectrum
        window = np.hanning(self.fft_size)
        segment = samples[:self.fft_size]
        spectrum = np.abs(np.fft.fftshift(np.fft.fft(segment * window)))**2
        spectrum_db = 10 * np.log10(spectrum + 1e-10)

        # Frequency bins
        freq_bins = np.fft.fftshift(np.fft.fftfreq(self.fft_size, 1/sample_rate))

        # Apply CA-CFAR
        half_window = self.guard_cells + self.training_cells
        threshold = np.zeros_like(spectrum)

        for i in range(half_window, len(spectrum) - half_window):
            # Training cells (excluding guard cells)
            left_train = spectrum[i - half_window:i - self.guard_cells]
            right_train = spectrum[i + self.guard_cells + 1:i + half_window + 1]
            noise_estimate = np.mean(np.concatenate([left_train, right_train]))
            threshold[i] = noise_estimate * self.threshold_factor

        # Find detections
        detections_mask = spectrum > threshold
        peaks = self._find_peaks(spectrum, detections_mask)

        for peak_bin in peaks:
            if peak_bin < half_window or peak_bin >= len(spectrum) - half_window:
                continue

            snr = 10 * np.log10(spectrum[peak_bin] / threshold[peak_bin])
            freq = freq_bins[peak_bin]

            detection = Detection(
                detection_type=DetectionType.ENERGY,
                timestamp=timestamp,
                frequency_hz=center_freq + freq,
                bandwidth_hz=sample_rate / self.fft_size,  # Single bin
                snr_db=snr,
                confidence=min(1.0, snr / 15.0),
                metadata={
                    'cfar_threshold': 10 * np.log10(threshold[peak_bin]),
                    'peak_power_db': spectrum_db[peak_bin],
                    'pfa': self.pfa
                }
            )
            detections.append(detection)
            self.detection_count += 1

        return detections

    def _find_peaks(
        self,
        spectrum: np.ndarray,
        mask: np.ndarray
    ) -> List[int]:
        """Find local maxima in masked spectrum"""
        peaks = []
        for i in range(1, len(spectrum) - 1):
            if mask[i]:
                if spectrum[i] > spectrum[i-1] and spectrum[i] > spectrum[i+1]:
                    peaks.append(i)
        return peaks


class PulseDetector(SignalDetector):
    """
    Pulse/burst signal detector.

    Detects pulsed signals by looking for sudden energy changes.
    Useful for radar pulse detection and similar applications.
    """

    def __init__(
        self,
        min_pulse_width_us: float = 1.0,
        max_pulse_width_us: float = 100.0,
        threshold_db: float = 10.0,
        name: str = "pulse_detector"
    ):
        super().__init__(name)
        self.min_pulse_width_us = min_pulse_width_us
        self.max_pulse_width_us = max_pulse_width_us
        self.threshold_db = threshold_db

        # State for pulse detection
        self._envelope_history = deque(maxlen=1000)

    def process(
        self,
        samples: np.ndarray,
        sample_rate: float,
        center_freq: float,
        timestamp: float
    ) -> List[Detection]:
        detections = []

        # Calculate envelope
        envelope = np.abs(samples)

        # Lowpass filter envelope
        cutoff_hz = 1 / (self.min_pulse_width_us * 1e-6)
        nyq = sample_rate / 2
        if cutoff_hz < nyq:
            b, a = scipy_signal.butter(4, cutoff_hz / nyq, btype='low')
            envelope_filt = scipy_signal.filtfilt(b, a, envelope)
        else:
            envelope_filt = envelope

        # Convert to dB
        envelope_db = 20 * np.log10(envelope_filt + 1e-10)

        # Estimate noise floor
        noise_floor = np.percentile(envelope_db, 25)

        # Find pulses (rising edges above threshold)
        threshold = noise_floor + self.threshold_db
        above_threshold = envelope_db > threshold

        # Find pulse edges
        edges = np.diff(above_threshold.astype(int))
        rising_edges = np.where(edges == 1)[0]
        falling_edges = np.where(edges == -1)[0]

        # Match rising and falling edges
        min_samples = int(self.min_pulse_width_us * 1e-6 * sample_rate)
        max_samples = int(self.max_pulse_width_us * 1e-6 * sample_rate)

        for rise in rising_edges:
            # Find corresponding fall
            falls_after = falling_edges[falling_edges > rise]
            if len(falls_after) == 0:
                continue

            fall = falls_after[0]
            width_samples = fall - rise

            if min_samples <= width_samples <= max_samples:
                pulse_samples = samples[rise:fall]
                pulse_power = np.mean(np.abs(pulse_samples)**2)
                pulse_power_db = 10 * np.log10(pulse_power + 1e-10)

                # Estimate carrier frequency from pulse
                spectrum = np.abs(np.fft.fft(pulse_samples))
                peak_bin = np.argmax(spectrum[:len(spectrum)//2])
                freq_offset = peak_bin * sample_rate / len(pulse_samples)

                pulse_timestamp = timestamp + rise / sample_rate

                detection = Detection(
                    detection_type=DetectionType.MATCHED_FILTER,
                    timestamp=pulse_timestamp,
                    frequency_hz=center_freq + freq_offset,
                    bandwidth_hz=1 / (width_samples / sample_rate),
                    snr_db=pulse_power_db - noise_floor,
                    confidence=0.8,
                    metadata={
                        'pulse_width_us': width_samples / sample_rate * 1e6,
                        'pulse_power_db': pulse_power_db,
                        'noise_floor_db': noise_floor
                    }
                )
                detections.append(detection)
                self.detection_count += 1

        return detections


# =============================================================================
# Signal Processing Harness
# =============================================================================

class SignalProcessingHarness:
    """
    Signal Processing Test Harness for VITA 49 Streams

    Receives VITA 49 packets and processes them through a chain of
    signal processing algorithms. Supports real-time display,
    recording, and algorithm benchmarking.
    """

    def __init__(
        self,
        listen_address: str = "0.0.0.0",
        port: int = 4991,
        buffer_duration_s: float = 1.0
    ):
        """
        Initialize signal processing harness.

        Args:
            listen_address: UDP listen address
            port: UDP port for VITA 49 stream
            buffer_duration_s: Processing buffer duration in seconds
        """
        self.listen_address = listen_address
        self.port = port
        self.buffer_duration_s = buffer_duration_s

        # VITA 49 client
        self.client = VITA49StreamClient(listen_address, port)

        # Signal detectors
        self.detectors: List[SignalDetector] = []

        # Stream parameters (updated from context packets)
        self.sample_rate = 30e6  # Default
        self.center_freq = 2.4e9  # Default
        self.bandwidth = 20e6  # Default

        # Processing state
        self._running = False
        self._process_thread: Optional[threading.Thread] = None
        self._sample_buffer: deque = deque()
        self._buffer_lock = threading.Lock()

        # Results
        self.detections: deque = deque(maxlen=10000)
        self.spectrum_history: deque = deque(maxlen=100)

        # Statistics
        self.stats = {
            'packets_processed': 0,
            'samples_processed': 0,
            'detections': 0,
            'processing_time_ms': 0.0
        }

        # Callbacks
        self._on_detection: Optional[Callable] = None
        self._on_spectrum: Optional[Callable] = None

        # Recording
        self._recording = False
        self._record_file: Optional[Path] = None
        self._record_samples: List[np.ndarray] = []

    def add_detector(self, detector: SignalDetector):
        """Add a signal detector to the processing chain"""
        self.detectors.append(detector)
        logger.info(f"Added detector: {detector.name}")

    def remove_detector(self, name: str):
        """Remove a detector by name"""
        self.detectors = [d for d in self.detectors if d.name != name]

    def _on_samples_received(self, packet: VRTSignalDataPacket, samples: np.ndarray):
        """Callback for received samples"""
        with self._buffer_lock:
            self._sample_buffer.append((packet, samples))

    def _processing_loop(self):
        """Main signal processing loop"""
        logger.info("Processing loop started")

        target_samples = int(self.sample_rate * self.buffer_duration_s)

        while self._running:
            try:
                # Collect samples
                collected_samples = []
                first_timestamp = None

                with self._buffer_lock:
                    while self._sample_buffer and len(collected_samples) < target_samples:
                        packet, samples = self._sample_buffer.popleft()
                        if first_timestamp is None and packet.timestamp:
                            first_timestamp = packet.timestamp.to_time()
                        collected_samples.extend(samples)

                if len(collected_samples) < target_samples:
                    time.sleep(0.01)
                    continue

                # Convert to numpy array
                samples = np.array(collected_samples[:target_samples], dtype=np.complex64)
                timestamp = first_timestamp or time.time()

                # Recording
                if self._recording:
                    self._record_samples.append(samples.copy())

                # Run detectors
                start_time = time.time()

                for detector in self.detectors:
                    if not detector.enabled:
                        continue

                    try:
                        detections = detector.process(
                            samples,
                            self.sample_rate,
                            self.center_freq,
                            timestamp
                        )

                        for det in detections:
                            self.detections.append(det)
                            self.stats['detections'] += 1

                            if self._on_detection:
                                self._on_detection(det)

                    except Exception as e:
                        logger.error(f"Detector {detector.name} error: {e}")

                processing_time = (time.time() - start_time) * 1000
                self.stats['processing_time_ms'] = processing_time
                self.stats['samples_processed'] += len(samples)
                self.stats['packets_processed'] += 1

                # Compute spectrum for display
                if self._on_spectrum:
                    spectrum = 10 * np.log10(
                        np.abs(np.fft.fftshift(np.fft.fft(samples[:1024])))**2 + 1e-10
                    )
                    self.spectrum_history.append(spectrum)
                    self._on_spectrum(spectrum)

            except Exception as e:
                logger.error(f"Processing error: {e}")

        logger.info("Processing loop stopped")

    def start(self) -> bool:
        """Start receiving and processing"""
        # Set up sample callback
        self.client.on_samples(self._on_samples_received)

        # Start VITA 49 client
        if not self.client.start():
            logger.error("Failed to start VITA 49 client")
            return False

        # Start processing thread
        self._running = True
        self._process_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self._process_thread.start()

        logger.info(f"Signal processing harness started on port {self.port}")
        return True

    def stop(self):
        """Stop processing"""
        self._running = False

        if self._process_thread:
            self._process_thread.join(timeout=2.0)

        self.client.stop()

        # Save recording if active
        if self._recording:
            self.stop_recording()

        logger.info("Signal processing harness stopped")

    def enable_recording(self, filename: str):
        """Start recording samples"""
        self._record_file = Path(filename)
        self._record_samples = []
        self._recording = True
        logger.info(f"Recording started: {filename}")

    def stop_recording(self) -> Optional[Path]:
        """Stop recording and save to file"""
        if not self._recording:
            return None

        self._recording = False

        if not self._record_samples:
            logger.warning("No samples recorded")
            return None

        # Concatenate samples
        all_samples = np.concatenate(self._record_samples)

        # Save as numpy file (can also save as SigMF)
        np.save(self._record_file, all_samples)

        # Save metadata
        meta_file = self._record_file.with_suffix('.json')
        metadata = {
            'sample_rate_hz': self.sample_rate,
            'center_freq_hz': self.center_freq,
            'bandwidth_hz': self.bandwidth,
            'num_samples': len(all_samples),
            'duration_s': len(all_samples) / self.sample_rate,
            'timestamp': datetime.now().isoformat(),
            'format': 'complex64'
        }
        with open(meta_file, 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Recording saved: {self._record_file} ({len(all_samples)} samples)")
        return self._record_file

    def get_recent_detections(self, count: int = 100) -> List[Detection]:
        """Get recent detections"""
        return list(self.detections)[-count:]

    def get_statistics(self) -> dict:
        """Get processing statistics"""
        stats = self.stats.copy()
        stats['client_packets'] = self.client.packets_received
        stats['client_samples'] = self.client.samples_received
        stats['num_detectors'] = len(self.detectors)
        stats['buffer_samples'] = len(self._sample_buffer)
        return stats

    def on_detection(self, callback: Callable[[Detection], None]):
        """Set callback for detections"""
        self._on_detection = callback

    def on_spectrum(self, callback: Callable[[np.ndarray], None]):
        """Set callback for spectrum updates"""
        self._on_spectrum = callback


# =============================================================================
# CLI Interface
# =============================================================================

def main():
    """Command-line interface for signal processing harness"""
    import argparse

    parser = argparse.ArgumentParser(
        description="VITA 49 Signal Processing Test Harness"
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=4991,
        help="VITA 49 UDP port (default: 4991)"
    )
    parser.add_argument(
        '--threshold', '-t',
        type=float,
        default=-20.0,
        help="Detection threshold in dB (default: -20)"
    )
    parser.add_argument(
        '--cfar',
        action='store_true',
        help="Use CFAR detector instead of energy detector"
    )
    parser.add_argument(
        '--pulse',
        action='store_true',
        help="Enable pulse detector"
    )
    parser.add_argument(
        '--record', '-r',
        type=str,
        default=None,
        help="Record to file"
    )

    args = parser.parse_args()

    # Create harness
    harness = SignalProcessingHarness(port=args.port)

    # Add detectors
    if args.cfar:
        harness.add_detector(CFARDetector(pfa=1e-4))
    else:
        harness.add_detector(EnergyDetector(threshold_db=args.threshold))

    if args.pulse:
        harness.add_detector(PulseDetector())

    # Detection callback
    def on_detection(det: Detection):
        print(f"[{det.detection_type.value}] "
              f"f={det.frequency_hz/1e6:.3f} MHz, "
              f"BW={det.bandwidth_hz/1e3:.1f} kHz, "
              f"SNR={det.snr_db:.1f} dB, "
              f"conf={det.confidence:.2f}")

    harness.on_detection(on_detection)

    # Enable recording if requested
    if args.record:
        harness.enable_recording(args.record)

    # Start processing
    if not harness.start():
        print("Failed to start harness")
        return 1

    print(f"Signal processing harness running on port {args.port}")
    print("Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(5)
            stats = harness.get_statistics()
            print(f"Stats: {stats['packets_processed']} pkts, "
                  f"{stats['samples_processed']} samples, "
                  f"{stats['detections']} detections, "
                  f"{stats['processing_time_ms']:.1f} ms/block")
    except KeyboardInterrupt:
        print("\nStopping...")
        harness.stop()

    return 0


if __name__ == '__main__':
    exit(main())
