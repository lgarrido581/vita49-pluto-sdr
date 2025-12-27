#!/usr/bin/env python3
"""
VITA49 Packet Timing Analyzer

Captures packets and analyzes timing characteristics to verify rate control.
Useful for diagnosing pacing issues and measuring jitter.

Usage:
    # Capture and analyze packets for 10 seconds
    python analyze_packet_timing.py --duration 10

    # Capture from specific port
    python analyze_packet_timing.py --port 4991 --duration 5

    # Save raw timing data to file
    python analyze_packet_timing.py --duration 10 --output timing_data.csv

    # Analyze against expected sample rate
    python analyze_packet_timing.py --rate 30e6 --duration 5
"""

import argparse
import socket
import struct
import time
import statistics
import sys
from typing import List, Dict, Tuple, Optional
from collections import defaultdict


class PacketTimingAnalyzer:
    """Analyze VITA49 packet timing for rate control verification."""

    def __init__(self, port: int = 4991, expected_rate: Optional[float] = None):
        self.port = port
        self.expected_rate = expected_rate

        # Timing data
        self.packet_times: List[float] = []
        self.packet_sizes: List[int] = []
        self.packet_counts: List[int] = []
        self.inter_packet_intervals: List[float] = []

        # For jitter analysis
        self.expected_interval_us: Optional[float] = None
        if expected_rate:
            # Assume 360 samples per packet (standard for 1500 MTU)
            samples_per_packet = 360
            self.expected_interval_us = (samples_per_packet / expected_rate) * 1_000_000

    def setup_socket(self, timeout: float = 2.0) -> socket.socket:
        """Create receiver socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
        sock.settimeout(timeout)
        sock.bind(('0.0.0.0', self.port))
        return sock

    def parse_header(self, data: bytes) -> Optional[Dict]:
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
            'size_words': packet_size,
            'raw_size': len(data),
        }

    def capture(self, duration: float) -> int:
        """Capture packets for specified duration."""
        sock = self.setup_socket()
        start_time = time.monotonic()
        last_time = None
        packet_count = 0

        print(f"Capturing packets on port {self.port} for {duration} seconds...")
        print(f"Press Ctrl+C to stop early\n")

        try:
            while time.monotonic() - start_time < duration:
                try:
                    data, addr = sock.recvfrom(16384)
                    recv_time = time.monotonic()

                    header = self.parse_header(data)
                    if header and header['type'] == 1:  # Data packet
                        self.packet_times.append(recv_time)
                        self.packet_sizes.append(len(data))
                        self.packet_counts.append(header['count'])

                        if last_time is not None:
                            interval = (recv_time - last_time) * 1_000_000  # us
                            self.inter_packet_intervals.append(interval)

                        last_time = recv_time
                        packet_count += 1

                        # Progress indicator
                        if packet_count % 10000 == 0:
                            elapsed = time.monotonic() - start_time
                            rate = packet_count / elapsed
                            print(f"  Captured {packet_count} packets ({rate:.0f} pkt/s)")

                except socket.timeout:
                    continue

        except KeyboardInterrupt:
            print("\nCapture interrupted")

        finally:
            sock.close()

        print(f"\nCapture complete: {packet_count} packets")
        return packet_count

    def analyze_timing(self) -> Dict:
        """Analyze timing statistics."""
        if len(self.inter_packet_intervals) < 2:
            return {'error': 'Not enough packets for analysis'}

        intervals = self.inter_packet_intervals

        stats = {
            'count': len(intervals),
            'mean_us': statistics.mean(intervals),
            'median_us': statistics.median(intervals),
            'stdev_us': statistics.stdev(intervals),
            'min_us': min(intervals),
            'max_us': max(intervals),
        }

        # Percentiles
        sorted_intervals = sorted(intervals)
        n = len(sorted_intervals)
        stats['p50_us'] = sorted_intervals[int(n * 0.50)]
        stats['p90_us'] = sorted_intervals[int(n * 0.90)]
        stats['p95_us'] = sorted_intervals[int(n * 0.95)]
        stats['p99_us'] = sorted_intervals[int(n * 0.99)]

        # Jitter analysis
        if len(intervals) > 1:
            # Jitter = variation in delay between consecutive packets
            jitter_samples = []
            for i in range(1, len(intervals)):
                jitter_samples.append(abs(intervals[i] - intervals[i-1]))
            stats['jitter_mean_us'] = statistics.mean(jitter_samples)
            stats['jitter_max_us'] = max(jitter_samples)

        # Compare to expected if provided
        if self.expected_interval_us:
            stats['expected_us'] = self.expected_interval_us
            stats['deviation_pct'] = ((stats['mean_us'] - self.expected_interval_us) /
                                       self.expected_interval_us) * 100

        return stats

    def analyze_sequence(self) -> Dict:
        """Analyze packet sequence for drops/duplicates."""
        if len(self.packet_counts) < 2:
            return {'error': 'Not enough packets'}

        drops = 0
        duplicates = 0
        gaps = []

        for i in range(1, len(self.packet_counts)):
            prev = self.packet_counts[i-1]
            curr = self.packet_counts[i]
            expected = (prev + 1) & 0xF

            if curr == prev:
                duplicates += 1
            elif curr != expected:
                # Calculate gap size
                if curr > expected:
                    gap = curr - expected
                else:
                    gap = (16 - expected) + curr
                drops += gap
                gaps.append(gap)

        return {
            'total_packets': len(self.packet_counts),
            'drops': drops,
            'duplicates': duplicates,
            'drop_rate': drops / len(self.packet_counts) if self.packet_counts else 0,
            'gap_sizes': gaps if gaps else None,
        }

    def analyze_throughput(self) -> Dict:
        """Analyze throughput statistics."""
        if len(self.packet_times) < 2:
            return {'error': 'Not enough packets'}

        duration = self.packet_times[-1] - self.packet_times[0]
        total_bytes = sum(self.packet_sizes)

        return {
            'duration_s': duration,
            'total_bytes': total_bytes,
            'packets': len(self.packet_sizes),
            'packets_per_sec': len(self.packet_sizes) / duration if duration > 0 else 0,
            'mbps': (total_bytes * 8 / duration / 1_000_000) if duration > 0 else 0,
            'avg_packet_size': statistics.mean(self.packet_sizes),
        }

    def print_report(self):
        """Print analysis report."""
        print("\n" + "="*70)
        print("VITA49 Packet Timing Analysis Report")
        print("="*70)

        # Throughput
        throughput = self.analyze_throughput()
        print("\n--- Throughput ---")
        if 'error' not in throughput:
            print(f"  Duration:        {throughput['duration_s']:.2f} seconds")
            print(f"  Total packets:   {throughput['packets']:,}")
            print(f"  Total bytes:     {throughput['total_bytes']:,}")
            print(f"  Packets/sec:     {throughput['packets_per_sec']:.0f}")
            print(f"  Throughput:      {throughput['mbps']:.2f} Mbps")
            print(f"  Avg packet size: {throughput['avg_packet_size']:.0f} bytes")

        # Timing
        timing = self.analyze_timing()
        print("\n--- Timing Analysis ---")
        if 'error' not in timing:
            print(f"  Sample count:    {timing['count']:,}")
            print(f"  Mean interval:   {timing['mean_us']:.2f} us")
            print(f"  Median interval: {timing['median_us']:.2f} us")
            print(f"  Std deviation:   {timing['stdev_us']:.2f} us")
            print(f"  Min interval:    {timing['min_us']:.2f} us")
            print(f"  Max interval:    {timing['max_us']:.2f} us")
            print(f"\n  Percentiles:")
            print(f"    P50:           {timing['p50_us']:.2f} us")
            print(f"    P90:           {timing['p90_us']:.2f} us")
            print(f"    P95:           {timing['p95_us']:.2f} us")
            print(f"    P99:           {timing['p99_us']:.2f} us")
            print(f"\n  Jitter:")
            print(f"    Mean jitter:   {timing['jitter_mean_us']:.2f} us")
            print(f"    Max jitter:    {timing['jitter_max_us']:.2f} us")

            if 'expected_us' in timing:
                print(f"\n  Expected interval: {timing['expected_us']:.2f} us")
                print(f"  Deviation:         {timing['deviation_pct']:+.2f}%")

        # Sequence
        sequence = self.analyze_sequence()
        print("\n--- Sequence Analysis ---")
        if 'error' not in sequence:
            print(f"  Total packets:   {sequence['total_packets']:,}")
            print(f"  Drops:           {sequence['drops']}")
            print(f"  Duplicates:      {sequence['duplicates']}")
            print(f"  Drop rate:       {sequence['drop_rate']*100:.4f}%")

        # Rate control assessment
        print("\n--- Rate Control Assessment ---")
        if 'error' not in timing and 'error' not in sequence:
            issues = []

            # Check consistency
            cv = timing['stdev_us'] / timing['mean_us'] if timing['mean_us'] > 0 else 0
            if cv > 0.5:
                issues.append(f"High timing variability (CV={cv:.2f})")

            # Check for drops
            if sequence['drop_rate'] > 0.01:
                issues.append(f"Packet drops detected ({sequence['drop_rate']*100:.2f}%)")

            # Check jitter
            if timing['jitter_max_us'] > timing['mean_us'] * 2:
                issues.append(f"High jitter spikes detected ({timing['jitter_max_us']:.0f}us)")

            if issues:
                print("  Status: ISSUES DETECTED")
                for issue in issues:
                    print(f"    - {issue}")
            else:
                print("  Status: GOOD")
                print("    Timing is consistent and no packet loss detected")

        print("\n" + "="*70)

    def save_csv(self, filename: str):
        """Save raw timing data to CSV file."""
        with open(filename, 'w') as f:
            f.write("packet_num,time_s,size_bytes,count,interval_us\n")
            for i, (t, s, c) in enumerate(zip(self.packet_times,
                                               self.packet_sizes,
                                               self.packet_counts)):
                interval = self.inter_packet_intervals[i-1] if i > 0 else 0
                f.write(f"{i},{t:.6f},{s},{c},{interval:.3f}\n")
        print(f"\nTiming data saved to: {filename}")


def main():
    parser = argparse.ArgumentParser(
        description="VITA49 Packet Timing Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic timing analysis
  python analyze_packet_timing.py --duration 10

  # Analyze with expected rate for deviation calculation
  python analyze_packet_timing.py --rate 30e6 --duration 5

  # Save raw data for external analysis
  python analyze_packet_timing.py --duration 10 --output timing.csv
        """
    )

    parser.add_argument(
        '--port', '-p',
        type=int,
        default=4991,
        help="UDP port to listen on (default: 4991)"
    )
    parser.add_argument(
        '--duration', '-d',
        type=float,
        default=10.0,
        help="Capture duration in seconds (default: 10)"
    )
    parser.add_argument(
        '--rate', '-r',
        type=float,
        default=None,
        help="Expected sample rate in Hz for deviation calculation"
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help="Output CSV file for raw timing data"
    )

    args = parser.parse_args()

    analyzer = PacketTimingAnalyzer(
        port=args.port,
        expected_rate=args.rate
    )

    # Capture packets
    count = analyzer.capture(args.duration)

    if count > 0:
        # Print analysis
        analyzer.print_report()

        # Save CSV if requested
        if args.output:
            analyzer.save_csv(args.output)
    else:
        print("\nNo packets received. Check that:")
        print(f"  1. Pluto streamer is running")
        print(f"  2. This host is registered as a subscriber")
        print(f"  3. Port {args.port} is correct and not blocked")
        return 1

    return 0


if __name__ == '__main__':
    exit(main())
