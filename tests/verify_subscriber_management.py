#!/usr/bin/env python3
"""
Simple verification script for subscriber management.
Tests basic functionality without SO_REUSEPORT complications.
"""

import socket
import struct
import time
import sys


def create_context_packet(freq_hz=2400000000, sample_rate_hz=30000000, gain_db=20.0):
    """Create a VITA49 context packet."""
    # CIF
    cif = (1 << 29) | (1 << 27) | (1 << 23) | (1 << 21)

    # Encode fields
    payload = b''
    bw_hz = int(sample_rate_hz * 0.8)
    payload += struct.pack('>q', int(bw_hz * (1 << 20)))
    payload += struct.pack('>q', int(freq_hz * (1 << 20)))
    payload += struct.pack('>hh', int(gain_db * 128), 0)
    payload += struct.pack('>q', int(sample_rate_hz * (1 << 20)))

    # Timestamps
    ts_int = int(time.time())
    ts_frac = 0

    # Header
    total_words = 1 + 1 + 1 + 2 + 1 + (len(payload) // 4)
    header = (0x4 << 28) | (0x1 << 22) | (0x2 << 20) | total_words

    # Build packet
    packet = struct.pack('>I', header)
    packet += struct.pack('>I', 0x01000000)
    packet += struct.pack('>I', ts_int)
    packet += struct.pack('>Q', ts_frac)
    packet += struct.pack('>I', cif)
    packet += payload

    return packet


def test_basic_functionality(streamer_ip):
    """Test basic subscriber functionality."""
    print("\n" + "="*70)
    print("BASIC SUBSCRIBER MANAGEMENT VERIFICATION")
    print("="*70)
    print(f"\nStreamer: {streamer_ip}")
    print("\nThis script verifies:")
    print("  1. Can register as subscriber")
    print("  2. Receives VITA49 data packets")
    print("  3. Can re-register after stopping")
    print("\nIMPORTANT: Check streamer logs for detailed subscriber statistics")
    print("="*70)

    # Test 1: Register and receive
    print("\n[Test 1] Register as subscriber and receive data...")

    # Create receiver
    data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    data_sock.settimeout(5.0)

    try:
        data_sock.bind(('0.0.0.0', 4991))
        print(f"  ✓ Bound to port 4991")
    except OSError as e:
        print(f"  ✗ Failed to bind to port 4991: {e}")
        print(f"  Note: Another process may be using the port")
        data_sock.close()
        return False

    # Send config to register
    config_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    packet = create_context_packet()
    config_sock.sendto(packet, (streamer_ip, 4990))
    config_sock.close()
    print(f"  ✓ Sent configuration packet")

    # Wait for data
    print(f"  ⏳ Waiting 3 seconds for data packets...")
    time.sleep(3)

    # Try to receive
    packets_received = 0
    bytes_received = 0
    start_time = time.time()

    while time.time() - start_time < 2.0:
        try:
            data, addr = data_sock.recvfrom(65536)
            packets_received += 1
            bytes_received += len(data)
        except socket.timeout:
            break

    if packets_received > 0:
        print(f"  ✓ Received {packets_received} packets ({bytes_received} bytes)")
        print(f"  ✓ TEST PASSED: Basic subscriber functionality works!")
    else:
        print(f"  ✗ Received 0 packets")
        print(f"  ✗ TEST FAILED: Not receiving data")
        print(f"\n  Troubleshooting:")
        print(f"    - Check streamer is running: ssh root@{streamer_ip} 'pgrep vita49_streamer'")
        print(f"    - Check firewall: ping {streamer_ip}")
        print(f"    - Check logs on streamer for 'Added subscriber' message")
        data_sock.close()
        return False

    data_sock.close()

    # Test 2: Re-register
    print(f"\n[Test 2] Re-register after stopping...")
    time.sleep(2)

    # Create new receiver
    data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    data_sock.settimeout(5.0)
    data_sock.bind(('0.0.0.0', 4991))

    # Send config again
    config_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    packet = create_context_packet(gain_db=25.0)  # Different gain to trigger update
    config_sock.sendto(packet, (streamer_ip, 4990))
    config_sock.close()
    print(f"  ✓ Sent new configuration packet")

    # Wait and receive
    time.sleep(2)
    packets_received = 0
    start_time = time.time()

    while time.time() - start_time < 2.0:
        try:
            data, addr = data_sock.recvfrom(65536)
            packets_received += 1
        except socket.timeout:
            break

    if packets_received > 0:
        print(f"  ✓ Received {packets_received} packets after re-registration")
        print(f"  ✓ TEST PASSED: Re-registration works!")
    else:
        print(f"  ✗ Received 0 packets after re-registration")
        print(f"  ✗ TEST FAILED")

    data_sock.close()

    # Summary
    print(f"\n" + "="*70)
    print("VERIFICATION COMPLETE")
    print("="*70)
    print("\nNEXT STEPS:")
    print(f"  1. SSH to streamer: ssh root@{streamer_ip}")
    print(f"  2. Check subscriber list in streamer output")
    print(f"  3. Look for '[Subscribers] Active: X/16' section")
    print(f"  4. Verify per-subscriber statistics are updating")
    print("\nExpected output on streamer:")
    print("  [Subscribers] Active: 1/16")
    print(f"    [0] <your-ip>:4991 - Pkts: XXXX, Fails: 0/0, Uptime: XXs")
    print("="*70)

    return True


def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        streamer_ip = sys.argv[1]
    else:
        streamer_ip = input("Enter streamer IP (e.g., pluto.local or 192.168.2.1): ").strip()
        if not streamer_ip:
            streamer_ip = "pluto.local"

    print(f"\nUsing streamer IP: {streamer_ip}")

    try:
        success = test_basic_functionality(streamer_ip)
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 1
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
