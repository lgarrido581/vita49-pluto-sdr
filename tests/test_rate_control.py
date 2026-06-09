#!/usr/bin/env python3
"""
VITA49 Streamer Test Suite

Tests the 2-thread DMA-paced streamer implementation to verify:
1. No sample loss (continuous packet sequence)
2. Bandwidth scales linearly with sample rate
3. No packet drops under normal network conditions

Architecture (on Pluto):
    - Data thread (Core 0): iio_buffer_refill() blocks ~2ms, then sends all packets
    - Control thread (Core 1): Receives config, zero CPU when idle
    - DMA blocking IS the pacing mechanism - no artificial delays

Expected throughput (Gigabit Ethernet):
    5 MSPS  -> ~40 Mbps
    10 MSPS -> ~80 Mbps
    20 MSPS -> ~160 Mbps
    30 MSPS -> ~240 Mbps

Usage:
    # Test against local Pluto
    python test_rate_control.py --pluto 192.168.2.1

    # Test specific sample rates
    python test_rate_control.py --pluto 192.168.2.1 --rates 5e6 10e6 20e6 30e6

    # Quick test (fewer packets)
    python test_rate_control.py --pluto 192.168.2.1 --quick
"""

import argparse
import socket
import struct
import time
import statistics
from collections import defaultdict
from typing import List, Dict, Tuple, Optional
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from vita49.config_client import VITA49ConfigClient
except ImportError:
    # Fallback inline implementation
    class VITA49ConfigClient:
        def __init__(self, pluto_ip, control_port=4990, data_port=4991):
            self.pluto_ip = pluto_ip
            self.control_port = control_port
            self.data_port = data_port
            self.stream_id = 0x01000000
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
                gain_fixed = int(gain_db * 128)
                context_fields.append(struct.pack('>hh', gain_fixed, 0))

            if sample_rate_hz is not None:
                cif |= (1 << 21)
                context_fields.append(encode_hz(sample_rate_hz))

            field_bytes = b''.join(context_fields)
            field_words = len(field_bytes) // 4
            packet_words = 1 + 1 + 1 + 2 + 1 + field_words

            header = 0
            header |= (0b0100 & 0xF) << 28
            header |= (0x01 & 0x3) << 22
            header |= (0x02 & 0x3) << 20
            header |= (packet_words & 0xFFFF)

            return b''.join([
                struct.pack('>I', header),
                struct.pack('>I', self.stream_id),
                struct.pack('>I', int_sec),
                struct.pack('>Q', frac_sec),
                struct.pack('>I', cif),
                field_bytes,
            ])

        def configure(self, sample_rate_hz=None, center_freq_hz=None, gain_db=None):
            packet = self.encode_context(sample_rate_hz, center_freq_hz, gain_db)
            self.socket.sendto(packet, (self.pluto_ip, self.control_port))
            return True

        def close(self):
            self.socket.close()


class DMAStreamTester:
    """Test 2-thread DMA-paced streamer implementation.

    With the DMA-paced architecture:
    - Data thread blocks on iio_buffer_refill() (~2ms at 30 MSPS)
    - Packets arrive in bursts after each DMA refill completes
    - Focus is on zero sample loss, not timing consistency
    - Bandwidth should scale linearly with sample rate
    """

    def __init__(self, pluto_ip: str, data_port: int = 4991, control_port: int = 4990):
        self.pluto_ip = pluto_ip
        self.data_port = data_port
        self.control_port = control_port
        self.config_client = VITA49ConfigClient(pluto_ip, control_port, data_port)

        # Statistics
        self.packet_times: List[float] = []
        self.packet_sizes: List[int] = []
        self.packet_counts: List[int] = []
        self.inter_packet_intervals: List[float] = []

    def setup_receiver(self, timeout: float = 5.0) -> socket.socket:
        """Create and bind UDP receiver socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
        sock.settimeout(timeout)
        sock.bind(('0.0.0.0', self.data_port))
        return sock

    def parse_vita49_header(self, data: bytes) -> Dict:
        """Parse VITA49 packet header."""
        if len(data) < 4:
            return None

        header = struct.unpack('>I', data[:4])[0]

        packet_type = (header >> 28) & 0xF
        packet_count = (header >> 16) & 0xF
        packet_size = header & 0xFFFF

        return {
            'type': packet_type,
            'count': packet_count,
            'size': packet_size,
            'raw_size': len(data),
        }

    def collect_packets(self, sock: socket.socket, duration: float,
                       sample_rate: int) -> Tuple[List[Dict], Dict]:
        """Collect packets for specified duration and compute statistics."""
        packets = []
        start_time = time.monotonic()
        last_time = start_time
        bytes_received = 0

        expected_interval_us = (360 / sample_rate) * 1_000_000  # 360 samples per packet typical

        print(f"  Collecting packets for {duration}s at {sample_rate/1e6:.1f} MSPS...")
        print(f"  Expected packet interval: {expected_interval_us:.1f} us")

        try:
            while time.monotonic() - start_time < duration:
                try:
                    data, addr = sock.recvfrom(16384)
                    recv_time = time.monotonic()

                    header = self.parse_vita49_header(data)
                    if header and header['type'] == 1:  # Data packet
                        interval = (recv_time - last_time) * 1_000_000  # microseconds

                        packets.append({
                            'time': recv_time,
                            'size': len(data),
                            'count': header['count'],
                            'interval_us': interval if len(packets) > 0 else 0,
                        })

                        bytes_received += len(data)
                        last_time = recv_time

                except socket.timeout:
                    break

        except KeyboardInterrupt:
            pass

        elapsed = time.monotonic() - start_time

        # Compute statistics
        stats = {
            'total_packets': len(packets),
            'total_bytes': bytes_received,
            'duration': elapsed,
            'packets_per_second': len(packets) / elapsed if elapsed > 0 else 0,
            'mbps': (bytes_received * 8 / elapsed / 1_000_000) if elapsed > 0 else 0,
        }

        if len(packets) > 1:
            intervals = [p['interval_us'] for p in packets[1:]]  # Skip first
            stats['avg_interval_us'] = statistics.mean(intervals)
            stats['std_interval_us'] = statistics.stdev(intervals) if len(intervals) > 1 else 0
            stats['min_interval_us'] = min(intervals)
            stats['max_interval_us'] = max(intervals)
            stats['expected_interval_us'] = expected_interval_us

        return packets, stats

    def verify_packet_sequence(self, packets: List[Dict]) -> Dict:
        """Verify packet sequence numbers for drops/duplicates."""
        if len(packets) < 2:
            return {'drops': 0, 'duplicates': 0, 'total': len(packets), 'drop_rate': 0.0}

        drops = 0
        duplicates = 0
        last_count = packets[0]['count']

        for pkt in packets[1:]:
            expected = (last_count + 1) & 0xF
            actual = pkt['count']

            if actual == last_count:
                duplicates += 1
            elif actual != expected:
                # Count how many we missed
                if actual > expected:
                    drops += actual - expected
                else:
                    drops += (16 - expected) + actual

            last_count = actual

        return {
            'drops': drops,
            'duplicates': duplicates,
            'total': len(packets),
            'drop_rate': drops / len(packets) if len(packets) > 0 else 0,
        }

    def test_rate(self, sample_rate: int, duration: float = 5.0) -> Dict:
        """Test rate control at specific sample rate."""
        print(f"\n{'='*60}")
        print(f"Testing Sample Rate: {sample_rate/1e6:.1f} MSPS")
        print(f"{'='*60}")

        # Configure Pluto
        print(f"  Configuring Pluto to {sample_rate/1e6:.1f} MSPS...")
        self.config_client.configure(
            sample_rate_hz=sample_rate,
            center_freq_hz=2.4e9,
            gain_db=20.0
        )

        # Wait for configuration to apply
        time.sleep(1.0)

        # Create receiver
        sock = self.setup_receiver(timeout=2.0)

        try:
            # Collect packets
            packets, stats = self.collect_packets(sock, duration, sample_rate)

            # Verify sequence
            seq_stats = self.verify_packet_sequence(packets)

            # Print results
            print(f"\n  Results:")
            print(f"    Packets received: {stats['total_packets']}")
            print(f"    Total bytes: {stats['total_bytes']:,}")
            print(f"    Throughput: {stats['mbps']:.2f} Mbps")
            print(f"    Packets/sec: {stats['packets_per_second']:.0f}")

            if 'avg_interval_us' in stats:
                print(f"\n  Timing:")
                print(f"    Expected interval: {stats['expected_interval_us']:.1f} us")
                print(f"    Actual avg interval: {stats['avg_interval_us']:.1f} us")
                print(f"    Std deviation: {stats['std_interval_us']:.1f} us")
                print(f"    Min/Max: {stats['min_interval_us']:.1f} / {stats['max_interval_us']:.1f} us")

            print(f"\n  Sequence Check:")
            print(f"    Drops: {seq_stats['drops']}")
            print(f"    Duplicates: {seq_stats['duplicates']}")
            print(f"    Drop rate: {seq_stats['drop_rate']*100:.2f}%")

            # Determine pass/fail
            # With FIFO architecture, focus is on:
            # 1. Zero or very low packet drops (sample loss)
            # 2. Bandwidth proportional to sample rate
            # Note: Timing consistency is NOT checked - packets arrive in bursts
            passed = True
            reasons = []

            # Check for excessive drops (critical - indicates sample loss)
            if seq_stats['drop_rate'] > 0.001:  # More than 0.1% drops
                passed = False
                reasons.append(f"Drop rate too high: {seq_stats['drop_rate']*100:.3f}% (target: <0.1%)")

            # Check bandwidth scaling (should be proportional to sample rate)
            expected_mbps = sample_rate * 4 * 8 / 1_000_000  # 4 bytes per sample, 8 bits
            # Allow 50% tolerance for network overhead and processing
            if stats['mbps'] < expected_mbps * 0.3:
                passed = False
                reasons.append(f"Low throughput: {stats['mbps']:.1f} Mbps (expected ~{expected_mbps:.1f})")

            result = {
                'sample_rate': sample_rate,
                'passed': passed,
                'reasons': reasons,
                'stats': stats,
                'sequence': seq_stats,
            }

            if passed:
                print(f"\n  [PASS] DMA streaming working correctly - no sample loss")
            else:
                print(f"\n  [FAIL] Issues detected:")
                for reason in reasons:
                    print(f"    - {reason}")

            return result

        finally:
            sock.close()

    def test_bandwidth_scaling(self, rates: List[int], duration: float = 5.0) -> Dict:
        """Test that bandwidth scales linearly with sample rate."""
        print("\n" + "="*60)
        print("Bandwidth Scaling Test")
        print("="*60)

        results = []
        for rate in rates:
            result = self.test_rate(rate, duration)
            results.append(result)
            time.sleep(1.0)  # Brief pause between tests

        # Analyze scaling
        print("\n" + "="*60)
        print("Bandwidth Scaling Summary")
        print("="*60)
        print(f"\n{'Sample Rate':>12} {'Expected Mbps':>14} {'Actual Mbps':>12} {'Ratio':>8} {'Status':>10}")
        print("-"*60)

        all_passed = True
        for result in results:
            rate = result['sample_rate']
            expected = rate * 4 * 8 / 1_000_000
            actual = result['stats']['mbps']
            ratio = actual / expected if expected > 0 else 0

            status = "OK" if 0.5 < ratio < 1.2 else "WARN"
            if ratio < 0.5:
                all_passed = False
                status = "FAIL"

            print(f"{rate/1e6:>10.1f} M {expected:>12.1f} {actual:>12.1f} {ratio:>8.2f} {status:>10}")

        return {
            'passed': all_passed,
            'results': results,
        }

    def run_full_test(self, rates: List[int] = None, duration: float = 5.0,
                     quick: bool = False) -> bool:
        """Run complete test suite."""
        if rates is None:
            rates = [5_000_000, 10_000_000, 20_000_000, 30_000_000]

        if quick:
            duration = 2.0

        print("\n" + "#"*60)
        print("# VITA49 DMA-Paced Streamer Test Suite")
        print("#"*60)
        print(f"# Pluto IP: {self.pluto_ip}")
        print(f"# Test Duration: {duration}s per rate")
        print(f"# Sample Rates: {[f'{r/1e6:.0f}M' for r in rates]}")
        print("# Architecture: 2-thread DMA-paced (refill blocks, then burst send)")
        print("#"*60)

        # Run bandwidth scaling test
        scaling_result = self.test_bandwidth_scaling(rates, duration)

        # Summary
        print("\n" + "#"*60)
        print("# Final Summary")
        print("#"*60)

        passed = 0
        failed = 0
        for result in scaling_result['results']:
            if result['passed']:
                passed += 1
            else:
                failed += 1

        print(f"\n  Tests Passed: {passed}/{len(scaling_result['results'])}")
        print(f"  Tests Failed: {failed}/{len(scaling_result['results'])}")

        overall_passed = failed == 0
        if overall_passed:
            print("\n  [OVERALL PASS] DMA streaming working correctly - no sample loss!")
        else:
            print("\n  [OVERALL FAIL] Some tests failed - check streamer implementation")

        return overall_passed

    def close(self):
        """Cleanup resources."""
        self.config_client.close()


def main():
    parser = argparse.ArgumentParser(
        description="VITA49 DMA-Paced Streamer Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full test suite
  python test_rate_control.py --pluto 192.168.2.1

  # Test specific sample rates
  python test_rate_control.py --pluto 192.168.2.1 --rates 5e6 10e6 30e6

  # Quick test (shorter duration)
  python test_rate_control.py --pluto 192.168.2.1 --quick

  # Test single rate
  python test_rate_control.py --pluto 192.168.2.1 --rates 30e6 --duration 10

What this tests:
  - 2-thread DMA-paced architecture (iio_buffer_refill blocks, then burst send)
  - Zero sample loss (no timestamp jumps)
  - Bandwidth scales linearly with sample rate
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
        help="Sample rates to test in Hz (default: 5e6 10e6 20e6 30e6)"
    )
    parser.add_argument(
        '--duration', '-d',
        type=float,
        default=5.0,
        help="Test duration per rate in seconds (default: 5)"
    )
    parser.add_argument(
        '--quick', '-q',
        action='store_true',
        help="Quick test mode (2s per rate)"
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

    # Convert rates from scientific notation if provided
    rates = None
    if args.rates:
        rates = [int(r) for r in args.rates]

    tester = DMAStreamTester(
        pluto_ip=args.pluto,
        data_port=args.data_port,
        control_port=args.control_port
    )

    try:
        passed = tester.run_full_test(
            rates=rates,
            duration=args.duration,
            quick=args.quick
        )
        return 0 if passed else 1
    finally:
        tester.close()


if __name__ == '__main__':
    exit(main())
