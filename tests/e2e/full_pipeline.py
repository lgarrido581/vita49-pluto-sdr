#!/usr/bin/env python3
"""
Full End-to-End VITA49 Pipeline Test

This script orchestrates the complete test chain:
    1. Receive from Pluto+ via pyadi-iio
    2. Re-stream as VITA49 packets
    3. Receive and display in plotting application

All components run in separate processes with proper cleanup.

Usage:
    python test_e2e_full_pipeline.py --pluto-uri ip:192.168.2.1

Options:
    --pluto-uri    : Pluto+ SDR URI (default: ip:pluto.local)
    --freq         : Center frequency in Hz (default: 2.4e9)
    --rate         : Sample rate in Hz (default: 30e6)
    --gain         : RX gain in dB (default: 20)
    --port         : VITA49 UDP port (default: 4991)
    --no-plotter   : Skip the plotting receiver (run streamer only)
"""

import argparse
import logging
import multiprocessing
import signal
import sys
import time
import subprocess

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(processName)s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_vita49_restreamer(
    pluto_uri: str,
    freq: float,
    rate: float,
    gain: float,
    port: int
):
    """Run VITA49 re-streamer in separate process"""
    try:
        from tests.e2e.step2_vita49_restreamer import VITA49Restreamer

        logger.info("Starting VITA49 re-streamer...")

        restreamer = VITA49Restreamer(
            pluto_uri=pluto_uri,
            center_freq_hz=freq,
            sample_rate_hz=rate,
            rx_gain_db=gain,
            destination="127.0.0.1",
            port=port,
            samples_per_packet=360
        )

        if not restreamer.start():
            logger.error("Failed to start re-streamer")
            return

        logger.info("Re-streamer running")

        # Run until stopped
        try:
            while True:
                time.sleep(5)
                stats = restreamer.get_statistics()
                logger.info(f"SDR: {stats['sdr_msps']:.1f} MSPS | "
                          f"VITA49: {stats['vita49_packets_sent']} pkts, "
                          f"{stats['vita49_mbps']:.2f} Mbps")
        except KeyboardInterrupt:
            logger.info("Re-streamer stopping...")

        restreamer.stop()
        logger.info("Re-streamer stopped")

    except Exception as e:
        logger.error(f"Re-streamer error: {e}")


def run_plotting_receiver(port: int, fft_size: int):
    """Run plotting receiver in separate process"""
    try:
        from tests.e2e.step3_plotting_receiver import VITA49PlottingReceiver

        logger.info("Starting plotting receiver...")

        receiver = VITA49PlottingReceiver(
            port=port,
            fft_size=fft_size,
            waterfall_lines=100,
            update_interval_ms=50
        )

        logger.info("Plotting receiver running")
        receiver.start()

        logger.info("Plotting receiver stopped")

    except Exception as e:
        logger.error(f"Plotting receiver error: {e}")


class PipelineOrchestrator:
    """Orchestrates the full pipeline"""

    def __init__(
        self,
        pluto_uri: str,
        freq: float,
        rate: float,
        gain: float,
        port: int,
        enable_plotter: bool = True
    ):
        self.pluto_uri = pluto_uri
        self.freq = freq
        self.rate = rate
        self.gain = gain
        self.port = port
        self.enable_plotter = enable_plotter

        self.restreamer_process: multiprocessing.Process = None
        self.plotter_process: multiprocessing.Process = None

    def start(self):
        """Start all pipeline components"""
        print("\n" + "="*70)
        print("VITA49 End-to-End Pipeline Test")
        print("="*70)
        print(f"  Pluto+ URI:       {self.pluto_uri}")
        print(f"  Center Frequency: {self.freq/1e9:.3f} GHz")
        print(f"  Sample Rate:      {self.rate/1e6:.1f} MSPS")
        print(f"  RX Gain:          {self.gain} dB")
        print(f"  VITA49 Port:      {self.port}")
        print(f"  Plotting:         {'Enabled' if self.enable_plotter else 'Disabled'}")
        print("="*70)
        print("\nPipeline Flow:")
        print(f"  Pluto+ SDR ({self.pluto_uri})")
        print("    ↓ pyadi-iio")
        print("  VITA49 Re-Streamer")
        print(f"    ↓ UDP packets (port {self.port})")
        if self.enable_plotter:
            print("  Plotting Receiver (matplotlib)")
        print("\n" + "="*70 + "\n")

        # Start VITA49 re-streamer
        logger.info("Starting VITA49 re-streamer process...")
        self.restreamer_process = multiprocessing.Process(
            target=run_vita49_restreamer,
            args=(self.pluto_uri, self.freq, self.rate, self.gain, self.port),
            name="ReStreamer"
        )
        self.restreamer_process.start()

        # Wait a bit for re-streamer to initialize
        time.sleep(2)

        # Start plotting receiver if enabled
        if self.enable_plotter:
            logger.info("Starting plotting receiver process...")
            self.plotter_process = multiprocessing.Process(
                target=run_plotting_receiver,
                args=(self.port, 1024),
                name="Plotter"
            )
            self.plotter_process.start()

        logger.info("All processes started successfully!")

    def stop(self):
        """Stop all pipeline components"""
        logger.info("Stopping pipeline...")

        if self.plotter_process and self.plotter_process.is_alive():
            logger.info("Stopping plotter process...")
            self.plotter_process.terminate()
            self.plotter_process.join(timeout=5)

        if self.restreamer_process and self.restreamer_process.is_alive():
            logger.info("Stopping re-streamer process...")
            self.restreamer_process.terminate()
            self.restreamer_process.join(timeout=5)

        logger.info("Pipeline stopped")

    def wait(self):
        """Wait for all processes to complete"""
        try:
            if self.enable_plotter and self.plotter_process:
                # Wait for plotter (blocks until user closes window)
                logger.info("Waiting for plotter window to close...")
                self.plotter_process.join()
            else:
                # No plotter - show stats in main process
                logger.info("Press Ctrl+C to stop...")
                print("\nMonitoring streaming (stats every 5 seconds)...\n")
                while True:
                    time.sleep(5)
                    # Check if re-streamer is still alive
                    if self.restreamer_process and not self.restreamer_process.is_alive():
                        logger.error("Re-streamer process died unexpectedly")
                        break
                    print(f"[{time.strftime('%H:%M:%S')}] Re-streamer process running (PID: {self.restreamer_process.pid if self.restreamer_process else 'N/A'})")

        except KeyboardInterrupt:
            logger.info("Interrupted by user")

        self.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Full End-to-End VITA49 Pipeline Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pipeline Flow:
  1. Receive IQ samples from Pluto+ SDR using pyadi-iio
  2. Re-stream samples as VITA49 UDP packets
  3. Receive and visualize in real-time plotting application

Example:
  python test_e2e_full_pipeline.py --pluto-uri ip:192.168.2.1 --freq 2.4e9
        """
    )

    parser.add_argument(
        '--pluto-uri', '-u',
        default="ip:pluto.local",
        help="Pluto+ URI (default: ip:pluto.local)"
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
        '--port', '-p',
        type=int,
        default=4991,
        help="VITA49 UDP port (default: 4991)"
    )
    parser.add_argument(
        '--no-plotter',
        action='store_true',
        help="Run without plotting receiver (streamer only)"
    )

    args = parser.parse_args()

    # Create orchestrator
    orchestrator = PipelineOrchestrator(
        pluto_uri=args.pluto_uri,
        freq=args.freq,
        rate=args.rate,
        gain=args.gain,
        port=args.port,
        enable_plotter=not args.no_plotter
    )

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        print("\n\nShutting down pipeline...")
        orchestrator.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Start and wait
    orchestrator.start()
    orchestrator.wait()

    return 0


if __name__ == '__main__':
    # Required for Windows multiprocessing
    multiprocessing.freeze_support()
    exit(main())
