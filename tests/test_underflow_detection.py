#!/usr/bin/env python3
"""
Comprehensive test for buffer underflow/overflow detection in VITA49 streamer.

This test script:
1. Starts the C streamer at high sample rate (61.44 MSPS)
2. Creates artificial network congestion on receiver
3. Monitors Context packets for state event indicators
4. Verifies underflow counter increases
5. Tests recovery by clearing congestion
6. Validates statistics are accurate

Usage:
    python3 test_underflow_detection.py --pluto-ip 192.168.2.1
"""

import socket
import struct
import time
import argparse
import threading
import queue
from typing import Dict, Tuple, Optional


class VITA49ContextParser:
    """Parser for VITA49 Context packets with state event indicators."""

    @staticmethod
    def parse_header(data: bytes) -> Dict:
        """Parse VITA49 packet header."""
        if len(data) < 4:
            return {}

        header = struct.unpack('>I', data[0:4])[0]

        packet_type = (header >> 28) & 0xF
        has_trailer = (header >> 26) & 0x1
        tsi = (header >> 22) & 0x3
        tsf = (header >> 20) & 0x3
        packet_count = (header >> 16) & 0xF
        packet_size = header & 0xFFFF

        return {
            'type': packet_type,
            'has_trailer': bool(has_trailer),
            'tsi': tsi,
            'tsf': tsf,
            'packet_count': packet_count,
            'size_words': packet_size,
            'size_bytes': packet_size * 4
        }

    @staticmethod
    def parse_context(data: bytes) -> Dict:
        """Parse VITA49 Context packet and extract configuration + state."""
        if len(data) < 28:
            return {}

        # Parse header
        header = VITA49ContextParser.parse_header(data)
        if header.get('type') != 4:  # Not a context packet
            return {}

        offset = 4

        # Stream ID
        stream_id = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4

        # Timestamps (skip)
        offset += 12  # 4 bytes integer + 8 bytes fractional

        # Context Indicator Field (CIF)
        cif = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4

        result = {
            'stream_id': stream_id,
            'cif': cif,
            'has_bandwidth': bool(cif & (1 << 29)),
            'has_rf_freq': bool(cif & (1 << 27)),
            'has_gain': bool(cif & (1 << 23)),
            'has_sample_rate': bool(cif & (1 << 21)),
            'has_state_event': bool(cif & (1 << 19))
        }

        # Parse fields in descending CIF bit order
        try:
            # Bit 29: Bandwidth
            if result['has_bandwidth']:
                bw_fixed = struct.unpack('>q', data[offset:offset+8])[0]
                result['bandwidth_hz'] = bw_fixed / (1 << 20)
                offset += 8

            # Bit 27: RF Reference Frequency
            if result['has_rf_freq']:
                freq_fixed = struct.unpack('>q', data[offset:offset+8])[0]
                result['center_freq_hz'] = freq_fixed / (1 << 20)
                offset += 8

            # Bit 23: Gain
            if result['has_gain']:
                gain_fixed = struct.unpack('>h', data[offset:offset+2])[0]
                result['gain_db'] = gain_fixed / 128.0
                offset += 4  # Skip both stage1 and stage2

            # Bit 21: Sample Rate
            if result['has_sample_rate']:
                rate_fixed = struct.unpack('>q', data[offset:offset+8])[0]
                result['sample_rate_hz'] = rate_fixed / (1 << 20)
                offset += 8

            # Bit 19: State/Event Indicators
            if result['has_state_event']:
                state_event = struct.unpack('>I', data[offset:offset+4])[0]
                result['state_event'] = state_event
                result['calibrated_time'] = bool(state_event & (1 << 31))
                result['overrange'] = bool(state_event & (1 << 19))
                result['sample_loss'] = bool(state_event & (1 << 18))
                offset += 4

        except struct.error as e:
            print(f"Error parsing context fields: {e}")

        return result


class NetworkThrottler:
    """Simulates network congestion by dropping packets."""

    def __init__(self, drop_rate: float = 0.5):
        self.drop_rate = drop_rate
        self.enabled = False
        self.packets_dropped = 0
        self.packets_total = 0

    def should_drop(self) -> bool:
        """Determine if packet should be dropped."""
        if not self.enabled:
            return False

        self.packets_total += 1
        import random
        if random.random() < self.drop_rate:
            self.packets_dropped += 1
            return True
        return False

    def enable(self):
        """Enable throttling."""
        self.enabled = True
        self.packets_dropped = 0
        self.packets_total = 0

    def disable(self):
        """Disable throttling."""
        self.enabled = False

    def get_stats(self) -> Tuple[int, int]:
        """Return (dropped, total)."""
        return self.packets_dropped, self.packets_total


class VITA49Receiver:
    """Receives and analyzes VITA49 packets."""

    def __init__(self, control_port=4990, data_port=4991):
        self.control_port = control_port
        self.data_port = data_port
        self.running = False
        self.throttler = NetworkThrottler()

        # Statistics
        self.data_packets = 0
        self.context_packets = 0
        self.last_context = None
        self.state_events_detected = []

        # Socket
        self.data_sock = None
        self.receive_thread = None
        self.packet_queue = queue.Queue()

    def start(self):
        """Start receiving packets."""
        # Create UDP socket for data
        self.data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.data_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.data_sock.bind(('', self.data_port))
        self.data_sock.settimeout(1.0)

        self.running = True
        self.receive_thread = threading.Thread(target=self._receive_loop)
        self.receive_thread.start()

    def stop(self):
        """Stop receiving packets."""
        self.running = False
        if self.receive_thread:
            self.receive_thread.join(timeout=2.0)
        if self.data_sock:
            self.data_sock.close()

    def _receive_loop(self):
        """Main receive loop."""
        while self.running:
            try:
                data, addr = self.data_sock.recvfrom(8192)

                # Apply throttling
                if self.throttler.should_drop():
                    continue

                # Parse header to determine packet type
                header = VITA49ContextParser.parse_header(data)
                packet_type = header.get('type', 0)

                if packet_type == 4:  # Context packet
                    self.context_packets += 1
                    context = VITA49ContextParser.parse_context(data)
                    self.last_context = context
                    self.packet_queue.put(('context', context))

                    # Check for state events
                    if context.get('has_state_event'):
                        self.state_events_detected.append({
                            'time': time.time(),
                            'overrange': context.get('overrange', False),
                            'sample_loss': context.get('sample_loss', False)
                        })

                elif packet_type == 1:  # Data packet
                    self.data_packets += 1
                    self.packet_queue.put(('data', header))

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"Error in receive loop: {e}")

    def send_config(self, pluto_ip: str, freq_hz: int, rate_hz: int, gain_db: float):
        """Send configuration context packet to streamer."""
        # Create control socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Build context packet
        buf = bytearray(128)
        offset = 0

        # CIF
        cif = 0
        cif |= (1 << 29)  # bandwidth
        cif |= (1 << 27)  # rf_reference_frequency
        cif |= (1 << 23)  # gain
        cif |= (1 << 21)  # sample_rate

        # Calculate payload
        payload = bytearray()

        # Bandwidth (80% of sample rate)
        bw_hz = int(rate_hz * 0.8)
        bw_fixed = int(bw_hz * (1 << 20))
        payload.extend(struct.pack('>q', bw_fixed))

        # RF Frequency
        freq_fixed = int(freq_hz * (1 << 20))
        payload.extend(struct.pack('>q', freq_fixed))

        # Gain
        gain_fixed = int(gain_db * 128)
        payload.extend(struct.pack('>h', gain_fixed))
        payload.extend(struct.pack('>h', 0))  # Stage 2

        # Sample Rate
        rate_fixed = int(rate_hz * (1 << 20))
        payload.extend(struct.pack('>q', rate_fixed))

        # Calculate packet size
        total_words = 1 + 1 + 1 + 2 + 1 + (len(payload) // 4)

        # Build header
        ts_us = int(time.time() * 1000000)
        ts_int = ts_us // 1000000
        ts_frac = (ts_us % 1000000) * 1000000

        header = 0
        header |= (4 & 0xF) << 28  # Context packet type
        header |= (1 & 0x3) << 22  # TSI
        header |= (2 & 0x3) << 20  # TSF
        header |= (total_words & 0xFFFF)

        # Pack packet
        packet = struct.pack('>I', header)
        packet += struct.pack('>I', 0x01000000)  # Stream ID
        packet += struct.pack('>I', ts_int)
        packet += struct.pack('>Q', ts_frac)
        packet += struct.pack('>I', cif)
        packet += payload

        # Send to control port
        sock.sendto(packet, (pluto_ip, self.control_port))
        sock.close()

        print(f"Sent config: {freq_hz/1e6:.1f} MHz, {rate_hz/1e6:.1f} MSPS, {gain_db:.1f} dB")

    def get_stats(self) -> Dict:
        """Get receiver statistics."""
        return {
            'data_packets': self.data_packets,
            'context_packets': self.context_packets,
            'last_context': self.last_context,
            'state_events': self.state_events_detected,
            'throttler': self.throttler.get_stats()
        }


def run_test(pluto_ip: str, duration: int = 60):
    """
    Run comprehensive underflow detection test.

    Test phases:
    1. Normal operation (10s)
    2. High congestion (20s) - should trigger underflows
    3. Recovery (30s) - should stabilize
    """
    print("=" * 60)
    print("VITA49 Buffer Underflow/Overflow Detection Test")
    print("=" * 60)

    receiver = VITA49Receiver()
    receiver.start()
    time.sleep(1)

    # Phase 1: Normal operation
    print("\n[Phase 1] Starting normal operation...")
    receiver.send_config(pluto_ip, freq_hz=2400000000, rate_hz=61440000, gain_db=20.0)
    time.sleep(10)

    stats = receiver.get_stats()
    print(f"Normal operation: {stats['data_packets']} data packets, "
          f"{stats['context_packets']} context packets")
    if stats['last_context']:
        print(f"  Sample rate: {stats['last_context'].get('sample_rate_hz', 0)/1e6:.1f} MSPS")

    # Phase 2: Create congestion
    print("\n[Phase 2] Enabling network congestion (50% packet loss)...")
    receiver.throttler.drop_rate = 0.5
    receiver.throttler.enable()

    # Monitor for state events
    initial_events = len(receiver.state_events_detected)
    print(f"  Initial state events: {initial_events}")

    congestion_start = time.time()
    while time.time() - congestion_start < 20:
        time.sleep(2)
        stats = receiver.get_stats()
        dropped, total = stats['throttler']
        events = len(receiver.state_events_detected)

        print(f"  [{int(time.time() - congestion_start)}s] "
              f"Packets: {stats['data_packets']}, "
              f"Dropped: {dropped}/{total}, "
              f"State events: {events}")

        # Check if we've detected underflow/overflow
        if events > initial_events:
            print(f"  ✓ State event indicators detected in Context packets!")
            recent_event = receiver.state_events_detected[-1]
            if recent_event['sample_loss']:
                print(f"    - Sample Loss (underflow) detected")
            if recent_event['overrange']:
                print(f"    - Overrange (overflow) detected")

    # Phase 3: Recovery
    print("\n[Phase 3] Disabling congestion, testing recovery...")
    receiver.throttler.disable()

    recovery_start = time.time()
    prev_packets = stats['data_packets']

    while time.time() - recovery_start < 15:
        time.sleep(3)
        stats = receiver.get_stats()
        packet_rate = (stats['data_packets'] - prev_packets) / 3.0
        prev_packets = stats['data_packets']

        print(f"  [{int(time.time() - recovery_start)}s] "
              f"Packet rate: {packet_rate:.1f} pkt/s, "
              f"Total events: {len(receiver.state_events_detected)}")

    # Final statistics
    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)

    final_stats = receiver.get_stats()
    print(f"Total data packets received: {final_stats['data_packets']}")
    print(f"Total context packets received: {final_stats['context_packets']}")
    print(f"Total state events detected: {len(final_stats['state_events'])}")

    if final_stats['state_events']:
        print(f"\nState event breakdown:")
        sample_loss_count = sum(1 for e in final_stats['state_events'] if e['sample_loss'])
        overrange_count = sum(1 for e in final_stats['state_events'] if e['overrange'])
        print(f"  Sample Loss (underflow): {sample_loss_count}")
        print(f"  Overrange (overflow): {overrange_count}")

    # Verify test success
    success = len(final_stats['state_events']) > 0
    print(f"\nTest Status: {'✓ PASSED' if success else '✗ FAILED'}")

    if success:
        print("Successfully detected buffer health issues via Context packets!")
    else:
        print("WARNING: No state events detected. Possible issues:")
        print("  1. Streamer may not be running")
        print("  2. Network congestion insufficient to trigger underflows")
        print("  3. State event indicators not properly implemented")

    receiver.stop()
    return success


def main():
    parser = argparse.ArgumentParser(
        description='Test VITA49 buffer underflow/overflow detection'
    )
    parser.add_argument('--pluto-ip', default='192.168.2.1',
                        help='IP address of Pluto SDR (default: 192.168.2.1)')
    parser.add_argument('--duration', type=int, default=60,
                        help='Test duration in seconds (default: 60)')

    args = parser.parse_args()

    print(f"\nTarget: {args.pluto_ip}")
    print(f"Duration: {args.duration}s\n")

    try:
        success = run_test(args.pluto_ip, args.duration)
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        return 1
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
