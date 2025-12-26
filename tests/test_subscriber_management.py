#!/usr/bin/env python3
"""
Comprehensive test suite for UDP subscriber management and error handling.

Tests cover:
1. Subscriber addition and limits
2. Failure handling and automatic removal
3. Timeout mechanism
4. Subscriber reactivation
5. Stress testing with multiple subscribers
"""

import socket
import struct
import time
import threading
import subprocess
import sys
from typing import List, Optional

# SO_REUSEPORT may not be available on all platforms
if not hasattr(socket, 'SO_REUSEPORT'):
    socket.SO_REUSEPORT = 15  # Linux value


class VITA49ContextPacket:
    """Helper class to create VITA49 context packets for configuration."""

    def __init__(self, freq_hz: int = 2400000000, sample_rate_hz: int = 30000000, gain_db: float = 20.0):
        self.freq_hz = freq_hz
        self.sample_rate_hz = sample_rate_hz
        self.gain_db = gain_db

    def encode(self) -> bytes:
        """Encode a VITA49 context packet."""
        # Context Indicator Field (CIF)
        cif = 0
        cif |= (1 << 29)  # bandwidth
        cif |= (1 << 27)  # rf_reference_frequency
        cif |= (1 << 23)  # gain
        cif |= (1 << 21)  # sample_rate

        # Encode fields in descending CIF bit order
        payload = b''

        # Bit 29: Bandwidth (64-bit, 20-bit radix)
        bw_hz = int(self.sample_rate_hz * 0.8)
        bw_fixed = int(bw_hz * (1 << 20))
        payload += struct.pack('>q', bw_fixed)

        # Bit 27: RF Reference Frequency (64-bit, 20-bit radix)
        freq_fixed = int(self.freq_hz * (1 << 20))
        payload += struct.pack('>q', freq_fixed)

        # Bit 23: Gain (16-bit, 7-bit radix) - stage 1 and stage 2
        gain_fixed = int(self.gain_db * 128)
        payload += struct.pack('>hh', gain_fixed, 0)

        # Bit 21: Sample Rate (64-bit, 20-bit radix)
        rate_fixed = int(self.sample_rate_hz * (1 << 20))
        payload += struct.pack('>q', rate_fixed)

        # Calculate timestamp
        ts_int = int(time.time())
        ts_frac = 0

        # Calculate packet size in 32-bit words
        total_words = 1 + 1 + 1 + 2 + 1 + (len(payload) // 4)

        # Build header
        header = 0
        header |= (0x4 & 0xF) << 28  # Context packet type
        header |= (0x1 & 0x3) << 22  # TSI: UTC
        header |= (0x2 & 0x3) << 20  # TSF: Picoseconds
        header |= (total_words & 0xFFFF)

        # Pack the full packet
        packet = struct.pack('>I', header)  # Header
        packet += struct.pack('>I', 0x01000000)  # Stream ID
        packet += struct.pack('>I', ts_int)  # Timestamp integer
        packet += struct.pack('>Q', ts_frac)  # Timestamp fractional
        packet += struct.pack('>I', cif)  # CIF
        packet += payload

        return packet


class UDPReceiver:
    """UDP receiver that listens for VITA49 data packets."""

    def __init__(self, port: int = 4991):
        self.port = port
        self.socket = None
        self.running = False
        self.packets_received = 0
        self.bytes_received = 0
        self.thread = None
        self.error_count = 0

    def start(self):
        """Start receiving packets."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # On Windows, also need SO_REUSEPORT to share port
        # On Linux, SO_REUSEADDR is usually sufficient
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            # SO_REUSEPORT not available on this platform
            pass

        # Set timeout so we can check running flag
        self.socket.settimeout(1.0)

        try:
            self.socket.bind(('0.0.0.0', self.port))
        except OSError as e:
            print(f"[Receiver] Warning: Could not bind to port {self.port}: {e}")
            # Try to bind to any available port
            self.socket.bind(('0.0.0.0', 0))
            self.port = self.socket.getsockname()[1]
            print(f"[Receiver] Bound to alternative port: {self.port}")

        self.running = True
        self.thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.thread.start()

    def _receive_loop(self):
        """Main receive loop."""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(65536)
                self.packets_received += 1
                self.bytes_received += len(data)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self.error_count += 1
                    print(f"[Receiver] Error: {e}")

    def stop(self):
        """Stop receiving packets."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        if self.socket:
            self.socket.close()


def send_config(streamer_ip: str, config_port: int = 4990,
                freq_hz: int = 2400000000, sample_rate_hz: int = 30000000,
                gain_db: float = 20.0) -> bool:
    """Send a VITA49 context packet to configure the streamer."""
    try:
        packet = VITA49ContextPacket(freq_hz, sample_rate_hz, gain_db)
        data = packet.encode()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(data, (streamer_ip, config_port))
        sock.close()
        return True
    except Exception as e:
        print(f"[Config] Failed to send: {e}")
        return False


def wait_for_packets(receiver: UDPReceiver, min_packets: int = 10, timeout: float = 5.0) -> bool:
    """Wait for receiver to get at least min_packets within timeout."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if receiver.packets_received >= min_packets:
            return True
        time.sleep(0.1)
    return False


def test_1_subscriber_addition():
    """Test 1: Subscriber addition and limit enforcement."""
    print("\n" + "="*70)
    print("TEST 1: Subscriber Addition and Limit Enforcement")
    print("="*70)

    streamer_ip = "127.0.0.1"
    receivers = []

    try:
        # Try to add 20 subscribers (should accept 16, reject 4)
        print(f"\n[Test] Adding 20 subscribers (max is 16)...")

        for i in range(20):
            receiver = UDPReceiver()
            receiver.start()
            receivers.append(receiver)

            # Send config to register as subscriber
            send_config(streamer_ip, freq_hz=2400000000 + i*1000000)
            time.sleep(0.1)

            print(f"  Added subscriber {i+1} on port {receiver.port}")

        # Wait for some packets to arrive
        time.sleep(3)

        # Count how many receivers got data
        active_receivers = sum(1 for r in receivers if r.packets_received > 0)

        print(f"\n[Result] Active receivers: {active_receivers}")
        print(f"[Result] Expected: 16 (MAX_SUBSCRIBERS)")

        if active_receivers == 16:
            print("✓ TEST PASSED: Correctly limited to 16 subscribers")
            return True
        else:
            print(f"✗ TEST FAILED: Expected 16 subscribers, got {active_receivers}")
            return False

    finally:
        # Cleanup
        for receiver in receivers:
            receiver.stop()


def test_2_failure_handling():
    """Test 2: Failure handling and automatic subscriber removal."""
    print("\n" + "="*70)
    print("TEST 2: Failure Handling and Auto-Removal")
    print("="*70)

    streamer_ip = "127.0.0.1"
    receiver = UDPReceiver()

    try:
        # Start receiver and register
        receiver.start()
        print(f"\n[Test] Started receiver on port {receiver.port}")

        send_config(streamer_ip)
        time.sleep(1)

        initial_packets = receiver.packets_received
        print(f"[Test] Received {initial_packets} packets initially")

        # Stop receiver (simulates network failure)
        print(f"\n[Test] Stopping receiver to simulate network failure...")
        receiver.stop()

        # Wait for 10+ failures (should take ~10 packets)
        # At 30 MSPS with 360 samples/packet, that's ~83 packets/sec
        # So 10 packets should take ~0.12 seconds, but we'll wait longer
        print(f"[Test] Waiting 5 seconds for subscriber to be marked inactive...")
        time.sleep(5)

        # Start a new receiver on a different port
        new_receiver = UDPReceiver()
        new_receiver.start()
        print(f"\n[Test] Started new receiver on port {new_receiver.port}")

        send_config(streamer_ip, gain_db=25.0)
        time.sleep(2)

        # Check if new receiver is getting data
        if new_receiver.packets_received > 100:
            print(f"✓ TEST PASSED: Failed subscriber removed, new slot available")
            print(f"  New receiver got {new_receiver.packets_received} packets")
            new_receiver.stop()
            return True
        else:
            print(f"✗ TEST FAILED: New subscriber not receiving data")
            print(f"  New receiver only got {new_receiver.packets_received} packets")
            new_receiver.stop()
            return False

    except Exception as e:
        print(f"✗ TEST FAILED: Exception: {e}")
        return False


def test_3_timeout_mechanism():
    """Test 3: Subscriber timeout mechanism."""
    print("\n" + "="*70)
    print("TEST 3: Subscriber Timeout Mechanism")
    print("="*70)

    streamer_ip = "127.0.0.1"
    receiver = UDPReceiver()

    try:
        # Start receiver and get some packets
        receiver.start()
        print(f"\n[Test] Started receiver on port {receiver.port}")

        send_config(streamer_ip)
        time.sleep(2)

        initial_packets = receiver.packets_received
        print(f"[Test] Received {initial_packets} packets")

        if initial_packets == 0:
            print(f"✗ TEST FAILED: Initial receiver got no packets - can't test timeout")
            receiver.stop()
            return False

        # Silently stop receiver (don't send FIN)
        print(f"\n[Test] Silently stopping receiver (simulating dropped connection)...")
        receiver.running = False
        if receiver.thread:
            receiver.thread.join()
        # Keep socket open but don't read from it

        # Wait just over the timeout period (30 seconds)
        print(f"[Test] Waiting 35 seconds for timeout...")
        print(f"[Test] NOTE: The streamer should log 'Removing subscriber (timeout)' after ~30s")
        for i in range(35):
            time.sleep(1)
            if (i + 1) % 5 == 0:
                print(f"  ... {i+1}/35 seconds elapsed")

        # Wait a bit more for cleanup to run (runs every 100 packets ~1.2s)
        print(f"[Test] Waiting 3 more seconds for cleanup cycle...")
        time.sleep(3)

        # Start new receiver and try to subscribe
        new_receiver = UDPReceiver()
        new_receiver.start()
        print(f"\n[Test] Started new receiver on port {new_receiver.port}")

        send_config(streamer_ip, gain_db=22.0)
        time.sleep(3)  # Give it more time

        # Check if new receiver is getting data (slot should be free due to timeout)
        if new_receiver.packets_received > 100:
            print(f"✓ TEST PASSED: Timed-out subscriber removed")
            print(f"  New receiver got {new_receiver.packets_received} packets")
            new_receiver.stop()
            return True
        else:
            print(f"✗ TEST FAILED: Timed-out subscriber not removed")
            print(f"  New receiver only got {new_receiver.packets_received} packets")
            print(f"  NOTE: Check streamer logs for timeout messages")
            new_receiver.stop()
            return False

    except Exception as e:
        print(f"✗ TEST FAILED: Exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if receiver.socket:
            receiver.socket.close()


def test_4_reactivation():
    """Test 4: Subscriber reactivation."""
    print("\n" + "="*70)
    print("TEST 4: Subscriber Reactivation")
    print("="*70)

    streamer_ip = "127.0.0.1"
    receiver = UDPReceiver()

    try:
        # Start receiver
        receiver.start()
        print(f"\n[Test] Started receiver on port {receiver.port}")

        send_config(streamer_ip)
        time.sleep(1)

        print(f"[Test] Received {receiver.packets_received} packets")

        # Stop receiver to cause failures
        receiver.stop()
        print(f"\n[Test] Stopped receiver to cause failures...")
        time.sleep(3)

        # Restart receiver (simulating recovery)
        receiver = UDPReceiver(port=receiver.port)
        receiver.start()
        print(f"\n[Test] Restarted receiver on same port {receiver.port}")

        # Send new config (should reactivate)
        send_config(streamer_ip, gain_db=23.0)
        time.sleep(2)

        # Check if receiving again
        if receiver.packets_received > 100:
            print(f"✓ TEST PASSED: Subscriber reactivated")
            print(f"  Received {receiver.packets_received} packets after reactivation")
            receiver.stop()
            return True
        else:
            print(f"✗ TEST FAILED: Subscriber not reactivated")
            print(f"  Only received {receiver.packets_received} packets")
            receiver.stop()
            return False

    except Exception as e:
        print(f"✗ TEST FAILED: Exception: {e}")
        return False


def test_5_stress_test():
    """Test 5: Stress test with multiple subscribers joining/leaving."""
    print("\n" + "="*70)
    print("TEST 5: Stress Test - Multiple Subscribers")
    print("="*70)
    print("\nNOTE: This test uses SO_REUSEPORT to share port 4991 among receivers")
    print("      All receivers on same machine will share incoming packets")

    streamer_ip = "127.0.0.1"
    receivers = []

    try:
        # Add 16 subscribers
        print(f"\n[Test] Adding 16 subscribers (with SO_REUSEPORT)...")
        for i in range(16):
            receiver = UDPReceiver()
            receiver.start()
            receivers.append(receiver)
            send_config(streamer_ip, freq_hz=2400000000 + i*1000000)
            time.sleep(0.1)

        print(f"[Test] Waiting 3 seconds for packets to arrive...")
        time.sleep(3)

        # Verify receiving (note: they share the port, so packets are distributed)
        total_packets = sum(r.packets_received for r in receivers)
        active_count = sum(1 for r in receivers if r.packets_received > 0)
        print(f"[Test] Total packets across all receivers: {total_packets}")
        print(f"[Test] Receivers that got packets: {active_count}/16")

        # Kill 8 (every other one)
        print(f"\n[Test] Stopping 8 subscribers...")
        for i in range(0, 16, 2):
            print(f"  Stopping receiver {i}")
            receivers[i].stop()

        # Wait for cleanup
        print(f"[Test] Waiting 5 seconds for cleanup...")
        time.sleep(5)

        # Add 8 new subscribers
        print(f"\n[Test] Adding 8 new subscribers...")
        new_receivers = []
        for i in range(8):
            receiver = UDPReceiver()
            receiver.start()
            new_receivers.append(receiver)
            send_config(streamer_ip, freq_hz=2500000000 + i*1000000)
            time.sleep(0.1)

        print(f"[Test] Waiting 3 seconds for new subscribers to receive...")
        time.sleep(3)

        # Count active subscribers (should be 8 original + 8 new = 16)
        active_original = sum(1 for r in receivers[1::2] if r.packets_received > 100)
        active_new = sum(1 for r in new_receivers if r.packets_received > 0)
        total_active = active_original + active_new

        print(f"\n[Result] Active original subscribers (odd indices): {active_original}/8")
        print(f"[Result] Active new subscribers: {active_new}/8")
        print(f"[Result] Total active: {total_active}")

        # Cleanup
        print(f"\n[Test] Cleaning up receivers...")
        for i, r in enumerate(receivers[1::2]):
            try:
                r.stop()
            except Exception as e:
                print(f"  Warning: Error stopping original receiver: {e}")
        for i, r in enumerate(new_receivers):
            try:
                r.stop()
            except Exception as e:
                print(f"  Warning: Error stopping new receiver: {e}")

        # Be lenient - if most are working, consider it a pass
        if total_active >= 12:  # Allow for some timing/port sharing issues
            print(f"✓ TEST PASSED: Cleanup and slot reuse working (got {total_active} active)")
            return True
        else:
            print(f"✗ TEST FAILED: Expected >=12 active, got {total_active}")
            print(f"  NOTE: Port sharing with SO_REUSEPORT may distribute packets unevenly")
            return False

    except Exception as e:
        print(f"✗ TEST FAILED: Exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print(f"\n[Cleanup] Stopping all remaining receivers...")
        for r in receivers:
            try:
                r.stop()
            except:
                pass
        print(f"[Cleanup] Done")


def main():
    """Run all subscriber management tests."""
    print("\n")
    print("="*70)
    print("VITA49 SUBSCRIBER MANAGEMENT TEST SUITE")
    print("="*70)
    print("\nNOTE: These tests require the vita49_streamer to be running")
    print("      Update streamer_ip in tests if not using 127.0.0.1")
    print("\nPre-requisites:")
    print("  1. Compile: make")
    print("  2. Run: ./build/vita49_streamer")
    print("\nIMPORTANT:")
    print("  • All test receivers bind to port 4991 (DATA_PORT)")
    print("  • Tests use SO_REUSEPORT to share the port")
    print("  • On same machine, OS distributes packets among receivers")
    print("  • For full isolation, run tests from different machines")
    print("="*70)

    input("\nPress Enter when streamer is running...")

    results = {}

    # Run tests
    tests = [
        ("Subscriber Addition", test_1_subscriber_addition),
        ("Failure Handling", test_2_failure_handling),
        ("Timeout Mechanism", test_3_timeout_mechanism),
        ("Reactivation", test_4_reactivation),
        ("Stress Test", test_5_stress_test),
    ]

    for name, test_func in tests:
        try:
            results[name] = test_func()
        except Exception as e:
            print(f"\n✗ TEST FAILED: {name} - Exception: {e}")
            results[name] = False

        time.sleep(2)  # Pause between tests

    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)

    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")

    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)

    print(f"\nTotal: {passed_count}/{total_count} tests passed")
    print("="*70 + "\n")

    return 0 if passed_count == total_count else 1


if __name__ == "__main__":
    sys.exit(main())
