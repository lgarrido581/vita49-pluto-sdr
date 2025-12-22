#!/usr/bin/env python3
"""
Step 3: VITA49 Plotting Receiver Application

This script receives VITA49 UDP packets and displays real-time plots of:
- Time domain (I/Q waveforms)
- Frequency domain (spectrum/FFT)
- Waterfall (spectrogram)
- Signal statistics

This completes the end-to-end test chain:
    Pluto+ → pyadi-iio → VITA49 Re-Streamer → This Script

Usage:
    python test_e2e_step3_plotting_receiver.py --port 4991
"""

import argparse
import logging
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque
import time

from vita49.stream_server import VITA49StreamClient
from vita49.packets import VRTSignalDataPacket

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VITA49PlottingReceiver:
    """
    Real-time plotting receiver for VITA49 streams
    """

    def __init__(
        self,
        port: int = 4991,
        fft_size: int = 1024,
        waterfall_lines: int = 100,
        update_interval_ms: int = 50
    ):
        self.port = port
        self.fft_size = fft_size
        self.waterfall_lines = waterfall_lines
        self.update_interval_ms = update_interval_ms

        # VITA49 client
        self.client = VITA49StreamClient(port=port)

        # Buffers
        self.sample_buffer = deque(maxlen=fft_size * 2)
        self.waterfall_buffer = deque(maxlen=waterfall_lines)

        # Stream metadata
        self.sample_rate = 30e6  # Default, updated from context packets
        self.center_freq = 2.4e9  # Default
        self.bandwidth = 20e6  # Default
        self.gain_db = 0.0  # Default
        self.last_timestamp = None
        self.context_received = False

        # Auto-scaling for spectrum/waterfall
        self.spectrum_min = -80.0  # dB
        self.spectrum_max = -20.0  # dB
        self.auto_scale = True  # Enable auto-scaling

        # Statistics
        self.stats = {
            'packets_received': 0,
            'samples_received': 0,
            'last_update': time.time(),
            'update_rate_hz': 0.0,
            'packet_rate_hz': 0.0,
            'signal_power_dbfs': -100.0,
            'noise_floor_db': -100.0,
            'peak_power_db': -100.0
        }

        # Set up plots (will be updated when context packet arrives)
        self._setup_plots()

    def _setup_plots(self):
        """Set up matplotlib figures and axes"""
        # Create figure with 4 subplots
        self.fig = plt.figure(figsize=(14, 10))
        gs = self.fig.add_gridspec(3, 2, hspace=0.3, wspace=0.3)

        # Time domain - I/Q
        self.ax_time = self.fig.add_subplot(gs[0, :])
        self.line_i, = self.ax_time.plot([], [], 'b-', label='I', alpha=0.7, linewidth=0.8)
        self.line_q, = self.ax_time.plot([], [], 'r-', label='Q', alpha=0.7, linewidth=0.8)
        self.ax_time.set_xlim(0, self.fft_size)
        self.ax_time.set_ylim(-0.1, 0.1)  # Start with smaller range, will auto-scale
        self.ax_time.set_xlabel('Sample')
        self.ax_time.set_ylabel('Amplitude (normalized)')
        self.ax_time.set_title('Time Domain (I/Q)')
        self.ax_time.legend(loc='upper right')
        self.ax_time.grid(True, alpha=0.3)

        # Spectrum
        self.ax_spectrum = self.fig.add_subplot(gs[1, :])
        self.line_spectrum, = self.ax_spectrum.plot([], [], 'g-', linewidth=1.0)
        self.ax_spectrum.set_xlim(-self.sample_rate/2/1e6, self.sample_rate/2/1e6)
        self.ax_spectrum.set_ylim(-100, 0)
        self.ax_spectrum.set_xlabel('Frequency Offset (MHz)')
        self.ax_spectrum.set_ylabel('Power (dBFS)')
        self.ax_spectrum.set_title('Spectrum')
        self.ax_spectrum.grid(True, alpha=0.3)

        # Waterfall
        self.ax_waterfall = self.fig.add_subplot(gs[2, 0])
        self.waterfall_image = self.ax_waterfall.imshow(
            np.zeros((self.waterfall_lines, self.fft_size)),
            aspect='auto',
            cmap='viridis',
            interpolation='nearest',
            vmin=-100,
            vmax=0,
            extent=[-self.sample_rate/2/1e6, self.sample_rate/2/1e6, 0, self.waterfall_lines]
        )
        self.ax_waterfall.set_xlabel('Frequency Offset (MHz)')
        self.ax_waterfall.set_ylabel('Time')
        self.ax_waterfall.set_title('Waterfall (Spectrogram)')
        plt.colorbar(self.waterfall_image, ax=self.ax_waterfall, label='Power (dBFS)')

        # Statistics text
        self.ax_stats = self.fig.add_subplot(gs[2, 1])
        self.ax_stats.axis('off')
        self.stats_text = self.ax_stats.text(
            0.02, 0.98, '',
            transform=self.ax_stats.transAxes,
            verticalalignment='top',
            fontfamily='monospace',
            fontsize=8  # Smaller font to fit more text
        )

        plt.suptitle(f'VITA49 Real-Time Receiver - Port {self.port}', fontsize=14, fontweight='bold')

    def _on_context_received(self, context_data: bytes):
        """Callback for received VITA49 context packets"""
        try:
            from vita49.packets import VRTContextPacket

            # Decode context packet
            context = VRTContextPacket.decode(context_data)

            # Update stream parameters
            if context.sample_rate_hz:
                old_rate = self.sample_rate
                self.sample_rate = context.sample_rate_hz
                if not self.context_received or abs(old_rate - self.sample_rate) > 1e3:
                    logger.info(f"Sample rate updated: {self.sample_rate/1e6:.1f} MSPS")
                    # Update spectrum plot limits
                    self.ax_spectrum.set_xlim(-self.sample_rate/2/1e6, self.sample_rate/2/1e6)
                    # Update waterfall extent
                    self.waterfall_image.set_extent(
                        [-self.sample_rate/2/1e6, self.sample_rate/2/1e6, 0, self.waterfall_lines]
                    )

            if context.rf_reference_frequency_hz:
                old_freq = self.center_freq
                self.center_freq = context.rf_reference_frequency_hz
                if not self.context_received or abs(old_freq - self.center_freq) > 1e6:
                    logger.info(f"Center frequency updated: {self.center_freq/1e9:.3f} GHz")

            if context.bandwidth_hz:
                self.bandwidth = context.bandwidth_hz

            if context.gain_db is not None:
                self.gain_db = context.gain_db

            self.context_received = True

        except Exception as e:
            logger.error(f"Error parsing context packet: {e}")

    def _on_samples_received(self, packet: VRTSignalDataPacket, samples: np.ndarray):
        """Callback for received VITA49 samples"""
        # Add to buffer
        for s in samples:
            self.sample_buffer.append(s)

        # Update stats
        self.stats['packets_received'] += 1
        self.stats['samples_received'] += len(samples)

        # Update timestamp
        if packet.timestamp:
            self.last_timestamp = packet.timestamp.to_time()

    def _update_plots(self, frame):
        """Update plots (called by animation)"""
        if len(self.sample_buffer) < self.fft_size:
            return self.line_i, self.line_q, self.line_spectrum, self.waterfall_image

        # Get samples
        samples = np.array(list(self.sample_buffer)[-self.fft_size:])

        # Update time domain with auto-scaling
        time_indices = np.arange(len(samples))
        self.line_i.set_data(time_indices, samples.real)
        self.line_q.set_data(time_indices, samples.imag)

        # Auto-scale time domain Y-axis based on actual signal amplitude
        if self.auto_scale:
            max_amplitude = max(np.max(np.abs(samples.real)), np.max(np.abs(samples.imag)))
            # Add 20% margin and use exponential smoothing
            target_ylim = max_amplitude * 1.2
            current_ylim = self.ax_time.get_ylim()[1]
            alpha = 0.05
            new_ylim = (1 - alpha) * current_ylim + alpha * target_ylim
            new_ylim = max(new_ylim, 0.01)  # Minimum range
            self.ax_time.set_ylim(-new_ylim, new_ylim)

        # Compute spectrum
        window = np.hanning(len(samples))
        spectrum = np.fft.fftshift(np.fft.fft(samples * window))
        spectrum_mag = np.abs(spectrum)
        spectrum_db = 20 * np.log10(spectrum_mag + 1e-10)

        # Auto-scale spectrum display
        if self.auto_scale and len(spectrum_db) > 0:
            # Use percentiles for robust min/max
            noise_floor = np.percentile(spectrum_db, 10)  # 10th percentile = noise floor
            peak_power = np.percentile(spectrum_db, 99)   # 99th percentile = peak (ignore outliers)

            # Update with smoothing (exponential moving average)
            alpha = 0.1
            self.spectrum_min = (1 - alpha) * self.spectrum_min + alpha * (noise_floor - 10)
            self.spectrum_max = (1 - alpha) * self.spectrum_max + alpha * (peak_power + 5)

            # Update plot limits
            self.ax_spectrum.set_ylim(self.spectrum_min, self.spectrum_max)

            # Update waterfall colormap limits
            self.waterfall_image.set_clim(vmin=self.spectrum_min, vmax=self.spectrum_max)

            # Store for statistics
            self.stats['noise_floor_db'] = noise_floor
            self.stats['peak_power_db'] = peak_power

        # Update spectrum plot
        freq_bins = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/self.sample_rate)) / 1e6
        self.line_spectrum.set_data(freq_bins, spectrum_db)

        # Update waterfall (render immediately, even if buffer not full)
        self.waterfall_buffer.append(spectrum_db)

        if len(self.waterfall_buffer) > 0:
            waterfall_data = np.array(self.waterfall_buffer)
            # Pad with zeros if buffer not yet full
            if len(waterfall_data) < self.waterfall_lines:
                padding = np.full((self.waterfall_lines - len(waterfall_data), self.fft_size),
                                 self.spectrum_min)  # Fill with min value
                waterfall_data = np.vstack([padding, waterfall_data])
            self.waterfall_image.set_data(waterfall_data)

        # Calculate statistics
        signal_power = np.mean(np.abs(samples)**2)
        signal_power_dbfs = 10 * np.log10(signal_power + 1e-10)
        self.stats['signal_power_dbfs'] = signal_power_dbfs

        # Calculate update rate
        now = time.time()
        dt = now - self.stats['last_update']
        if dt > 0:
            self.stats['update_rate_hz'] = 1.0 / dt
        self.stats['last_update'] = now

        # Update statistics text
        stats_str = self._format_statistics()
        self.stats_text.set_text(stats_str)

        return self.line_i, self.line_q, self.line_spectrum, self.waterfall_image

    def _format_statistics(self) -> str:
        """Format statistics for display"""
        # Context received indicator
        ctx_status = "✓" if self.context_received else "✗"

        # Ultra-compact format to fit everything
        snr = self.stats['peak_power_db'] - self.stats['noise_floor_db']

        stats = f"""STREAM  {ctx_status}
{'-'*28}
{self.center_freq/1e6:.1f} MHz
{self.sample_rate/1e6:.1f} MSPS
{self.bandwidth/1e6:.1f} MHz BW
{self.gain_db:.0f} dB gain

RX STATS
{'-'*28}
Pkts:  {self.stats['packets_received']:,}
Samps: {self.stats['samples_received']/1e6:.1f}M
Rate:  {self.stats['update_rate_hz']:.0f} Hz

SIGNAL (dB)
{'-'*28}
Pwr:   {self.stats['signal_power_dbfs']:.1f}
Noise: {self.stats['noise_floor_db']:.1f}
Peak:  {self.stats['peak_power_db']:.1f}
SNR:   {snr:.1f}
Range: {self.spectrum_min:.0f}/{self.spectrum_max:.0f}
"""
        return stats

    def start(self):
        """Start receiving and plotting"""
        # Set callbacks
        self.client.on_samples(self._on_samples_received)
        self.client.on_context(self._on_context_received)

        # Start VITA49 client
        if not self.client.start():
            logger.error("Failed to start VITA49 client")
            return False

        logger.info(f"VITA49 plotting receiver started on port {self.port}")

        # Start animation
        self.ani = FuncAnimation(
            self.fig,
            self._update_plots,
            interval=self.update_interval_ms,
            blit=False,
            cache_frame_data=False
        )

        # Show plot
        plt.show()

        # Stop client when plot window closes
        self.client.stop()
        logger.info("Plotting receiver stopped")

        return True


def main():
    parser = argparse.ArgumentParser(
        description="VITA49 Real-Time Plotting Receiver"
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=4991,
        help="VITA49 UDP port (default: 4991)"
    )
    parser.add_argument(
        '--fft-size', '-f',
        type=int,
        default=1024,
        help="FFT size (default: 1024)"
    )
    parser.add_argument(
        '--waterfall-lines', '-w',
        type=int,
        default=100,
        help="Waterfall history lines (default: 100)"
    )
    parser.add_argument(
        '--update-rate', '-u',
        type=int,
        default=50,
        help="Plot update interval in ms (default: 50)"
    )

    args = parser.parse_args()

    print("="*60)
    print("VITA49 Real-Time Plotting Receiver")
    print("="*60)
    print(f"  Listening on port: {args.port}")
    print(f"  FFT Size: {args.fft_size}")
    print(f"  Waterfall Lines: {args.waterfall_lines}")
    print(f"  Update Rate: {args.update_rate} ms")
    print("="*60)
    print("\nWaiting for VITA49 packets...")
    print("(Close the plot window to exit)\n")

    # Create and start receiver
    receiver = VITA49PlottingReceiver(
        port=args.port,
        fft_size=args.fft_size,
        waterfall_lines=args.waterfall_lines,
        update_interval_ms=args.update_rate
    )

    receiver.start()

    return 0


if __name__ == '__main__':
    exit(main())
