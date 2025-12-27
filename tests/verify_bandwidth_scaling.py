#!/usr/bin/env python3
"""
VITA49 Bandwidth Scaling Verification

Tests that network bandwidth scales correctly with sample rate.
This verifies that rate control is working - bandwidth should be
proportional to sample rate, not constant at maximum.

Expected bandwidth formula:
    bandwidth_bps = sample_rate * 4 bytes/sample * 8 bits/byte
                  = sample_rate * 32

Example:
    5 MSPS  -> 160 Mbps (theoretical), ~40 Mbps with overhead
    10 MSPS -> 320 Mbps (theoretical), ~80 Mbps with overhead
    20 MSPS -> 640 Mbps (theoretical), ~160 Mbps with overhead
    30 MSPS -> 960 Mbps (theoretical), ~240 Mbps with overhead

Usage:
    python verify_bandwidth_scaling.py --pluto 192.168.2.1
"""

import argparse
import socket
import struct
import time
import sys
import os
from typing import List, Dict, Tuple

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from vita49.config_client import VITA49ConfigClient
except ImportError:
    class VITA49ConfigClient:
        def __init__(self, pluto_ip, control_port=4990, data_port=4991):
            self.pluto_ip = pluto_ip
            self.control_port = control_port
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        def encode_context(self, sample_rate_hz=None, center_freq_hz=None, gain_db=None):
            timestamp = time.time()
            int_sec = int(timestamp)
            frac_sec = int((timestamp - int_sec) * 1e12)
            cif = 0
            context_fields = []

            def encode_hz(val):
                return struct.pack('>q', int(val * (1 << 20)))

            if center_freq_hz is not None:
                cif |= (1 << 27)
                context_fields.append(encode_hz(center_freq_hz))
            if gain_db is not None:
                cif |= (1 << 23)
                context_fields.append(struct.pack('>hh', int(gain_db * 128), 0))
            if sample_rate_hz is not None:
                cif |= (1 << 21)
                context_fields.append(encode_hz(sample_rate_hz))

            field_bytes = b''.join(context_fields)
            packet_words = 1 + 1 + 1 + 2 + 1 + len(field_bytes) // 4
            header = (0b0100 << 28) | (0x01 << 22) | (0x02 << 20) | packet_words

            return struct.pack('>I', header) + struct.pack('>I', 0x01000000) + \
                   struct.pack('>I', int_sec) + struct.pack('>Q', frac_sec) + \
                   struct.pack('>I', cif) + field_bytes

        def configure(self, sample_rate_hz=None, center_freq_hz=None, gain_db=None):
            packet = self.encode_context(sample_rate_hz, center_freq_hz, gain_db)
            self.socket.sendto(packet, (self.pluto_ip, self.control_port))
            return True

        def close(self):
            self.socket.close()


class BandwidthScalingTest:
    """Test bandwidth scaling with sample rate."""

    # Test sample rates
    DEFAULT_RATES = [5_000_000, 10_000_000, 20_000_000, 30_000_000]

    def __init__(self, pluto_ip: str, data_port: int = 4991, control_port: int = 4990):
        self.pluto_ip = pluto_ip
        self.data_port = data_port
        self.control_port = control_port
        self.config_client = VITA49ConfigClient(pluto_ip, control_port, data_port)

    def measure_bandwidth(self, duration: float = 5.0) -> Dict:
        """Measure current bandwidth for specified duration."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
        sock.settimeout(2.0)
        sock.bind(('0.0.0.0', self.data_port))

        start_time = time.monotonic()
        total_bytes = 0
        packet_count = 0

        try:
            while time.monotonic() - start_time < duration:
                try:
                    data, addr = sock.recvfrom(16384)
                    total_bytes += len(data)
                    packet_count += 1
                except socket.timeout:
                    continue
        finally:
            sock.close()

        elapsed = time.monotonic() - start_time
        mbps = (total_bytes * 8 / elapsed / 1_000_000) if elapsed > 0 else 0

        return {
            'bytes': total_bytes,
            'packets': packet_count,
            'duration': elapsed,
            'mbps': mbps,
        }

    def test_rate(self, sample_rate: int, duration: float = 5.0) -> Dict:
        """Test bandwidth at specific sample rate."""
        # Configure Pluto
        self.config_client.configure(
            sample_rate_hz=sample_rate,
            center_freq_hz=2.4e9,
            gain_db=20.0
        )

        # Wait for configuration
        time.sleep(1.0)

        # Measure bandwidth
        result = self.measure_bandwidth(duration)

        # Calculate expected bandwidth
        # 4 bytes per sample (I16 + Q16), with packet overhead ~15%
        theoretical_mbps = sample_rate * 4 * 8 / 1_000_000
        expected_mbps = theoretical_mbps * 0.25  # Account for overhead, pacing gaps

        result['sample_rate'] = sample_rate
        result['theoretical_mbps'] = theoretical_mbps
        result['expected_min_mbps'] = expected_mbps

        return result

    def run_scaling_test(self, rates: List[int] = None,
                         duration: float = 5.0) -> Dict:
        """Run bandwidth scaling test across multiple rates."""
        if rates is None:
            rates = self.DEFAULT_RATES

        print("="*70)
        print("VITA49 Bandwidth Scaling Verification")
        print("="*70)
        print(f"Pluto IP: {self.pluto_ip}")
        print(f"Test duration per rate: {duration}s")
        print(f"Sample rates: {[f'{r/1e6:.0f}M' for r in rates]}")
        print("="*70)

        results = []
        for rate in rates:
            print(f"\nTesting {rate/1e6:.0f} MSPS...")
            result = self.test_rate(rate, duration)
            results.append(result)
            print(f"  Measured: {result['mbps']:.1f} Mbps "
                  f"({result['packets']} packets)")

        # Analyze scaling
        print("\n" + "="*70)
        print("Results Summary")
        print("="*70)
        print(f"\n{'Rate':>10} {'Measured':>12} {'Theoretical':>12} "
              f"{'Efficiency':>12} {'Status':>10}")
        print("-"*70)

        scaling_ratios = []
        all_passed = True

        for result in results:
            rate = result['sample_rate']
            measured = result['mbps']
            theoretical = result['theoretical_mbps']
            efficiency = (measured / theoretical * 100) if theoretical > 0 else 0

            # Check if bandwidth is reasonable (>10% of theoretical)
            passed = efficiency > 10

            if not passed:
                all_passed = False

            status = "PASS" if passed else "FAIL"

            print(f"{rate/1e6:>8.0f} M {measured:>10.1f} {theoretical:>10.1f} "
                  f"{efficiency:>10.1f}% {status:>10}")

            if len(results) > 1:
                scaling_ratios.append(measured / (rate / 1e6))

        # Check linear scaling
        print("\n--- Scaling Analysis ---")

        if len(scaling_ratios) > 1:
            ratio_mean = sum(scaling_ratios) / len(scaling_ratios)
            ratio_variance = sum((r - ratio_mean)**2 for r in scaling_ratios) / len(scaling_ratios)
            ratio_cv = (ratio_variance ** 0.5) / ratio_mean if ratio_mean > 0 else 0

            print(f"  Bandwidth/Rate ratio (should be constant): {ratio_mean:.2f} Mbps/MSPS")
            print(f"  Ratio coefficient of variation: {ratio_cv:.2%}")

            if ratio_cv < 0.3:
                print("  Scaling: LINEAR (rate control working correctly)")
            else:
                print("  Scaling: NON-LINEAR (potential rate control issue)")
                all_passed = False
        else:
            print("  Need multiple rates to analyze scaling")

        # Final verdict
        print("\n" + "="*70)
        if all_passed:
            print("RESULT: PASS - Bandwidth scales correctly with sample rate")
        else:
            print("RESULT: FAIL - Bandwidth does not scale correctly")
            print("\nPossible issues:")
            print("  - Rate control not working (pacing-mode=none)")
            print("  - Network bottleneck")
            print("  - Buffer underruns")
        print("="*70)

        return {
            'passed': all_passed,
            'results': results,
        }

    def close(self):
        """Cleanup."""
        self.config_client.close()


def main():
    parser = argparse.ArgumentParser(
        description="VITA49 Bandwidth Scaling Verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Standard test (5, 10, 20, 30 MSPS)
  python verify_bandwidth_scaling.py --pluto 192.168.2.1

  # Custom rates
  python verify_bandwidth_scaling.py --pluto 192.168.2.1 --rates 2.5e6 5e6 10e6

  # Longer measurement
  python verify_bandwidth_scaling.py --pluto 192.168.2.1 --duration 10

Expected behavior:
  With rate control enabled, bandwidth should scale linearly with sample rate.
  Without rate control, bandwidth would be constant at maximum regardless of rate.
        """
    )

    parser.add_argument(
        '--pluto', '-p',
        required=True,
        help="Pluto IP address"
    )
    parser.add_argument(
        '--rates', '-r',
        type=float,
        nargs='+',
        default=None,
        help="Sample rates to test (default: 5e6 10e6 20e6 30e6)"
    )
    parser.add_argument(
        '--duration', '-d',
        type=float,
        default=5.0,
        help="Test duration per rate in seconds (default: 5)"
    )
    parser.add_argument(
        '--data-port',
        type=int,
        default=4991,
        help="Data port (default: 4991)"
    )
    parser.add_argument(
        '--control-port',
        type=int,
        default=4990,
        help="Control port (default: 4990)"
    )

    args = parser.parse_args()

    rates = None
    if args.rates:
        rates = [int(r) for r in args.rates]

    tester = BandwidthScalingTest(
        pluto_ip=args.pluto,
        data_port=args.data_port,
        control_port=args.control_port
    )

    try:
        result = tester.run_scaling_test(rates=rates, duration=args.duration)
        return 0 if result['passed'] else 1
    finally:
        tester.close()


if __name__ == '__main__':
    exit(main())
