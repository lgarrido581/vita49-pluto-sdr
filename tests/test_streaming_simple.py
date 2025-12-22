#!/usr/bin/env python3
"""
Simple VITA49 Streaming Test - Direct Mode

Runs the VITA49 re-streamer directly in the main process (not subprocess)
so you can see all console output and debug issues.

This is the recommended way to test when you don't need the plotting receiver.

Usage:
    python test_streaming_simple.py --pluto-uri ip:pluto.local --freq 103.7e6 --rate 2e6
"""

import argparse
import sys

# Import directly - no multiprocessing
from tests.e2e.step2_vita49_restreamer import VITA49Restreamer


def main():
    parser = argparse.ArgumentParser(
        description="Simple VITA49 Streaming Test (Direct Mode)"
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

    args = parser.parse_args()

    print("\n" + "="*70)
    print("VITA49 Streaming Test (Direct Mode)")
    print("="*70)
    print(f"  Pluto+ URI:       {args.pluto_uri}")
    print(f"  Center Frequency: {args.freq/1e9:.3f} GHz ({args.freq/1e6:.1f} MHz)")
    print(f"  Sample Rate:      {args.rate/1e6:.1f} MSPS")
    print(f"  RX Gain:          {args.gain} dB")
    print(f"  VITA49 Dest:      {args.dest}:{args.port}")
    print("="*70)
    print("\nStarting re-streamer...\n")

    # Create re-streamer
    restreamer = VITA49Restreamer(
        pluto_uri=args.pluto_uri,
        center_freq_hz=args.freq,
        sample_rate_hz=args.rate,
        rx_gain_db=args.gain,
        destination=args.dest,
        port=args.port
    )

    # Start
    if not restreamer.start():
        print("\n✗ Failed to start re-streamer")
        print("\nPossible issues:")
        print("  1. Can't connect to Pluto+ - check URI and network")
        print("  2. pyadi-iio not installed - run: pip install pyadi-iio")
        print("  3. Pluto+ already in use by another application")
        return 1

    print("✓ Re-streamer started successfully!")
    print("\nYou can now:")
    print("  - Run the packet inspector: python vita49_packet_inspector.py --port", args.port)
    print("  - Run the plotting receiver: python test_e2e_step3_plotting_receiver.py --port", args.port)
    print("\nPress Ctrl+C to stop\n")

    # Run
    try:
        import time
        while True:
            time.sleep(5)
            stats = restreamer.get_statistics()
            print(f"[{time.strftime('%H:%M:%S')}] "
                  f"SDR: {stats['sdr_buffers_received']} bufs, {stats['sdr_msps']:.1f} MSPS | "
                  f"VITA49: {stats['vita49_packets_sent']} pkts, {stats['vita49_mbps']:.2f} Mbps | "
                  f"Context: {stats['context_packets_sent']}")

    except KeyboardInterrupt:
        print("\n\nStopping...")

    restreamer.stop()
    print("✓ Stopped\n")

    return 0


if __name__ == '__main__':
    exit(main())
