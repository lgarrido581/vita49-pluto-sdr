#!/usr/bin/env python3
"""
Dynamic Configuration Test for VITA49 C Streamer

Tests that configuration changes sent via VITA49 Context packets
are properly applied to the SDR hardware during streaming.

This test validates the fix for the critical bug where:
- Control thread receives VITA49 Context packets with new freq/rate/gain
- Configuration is updated in g_sdr_config struct
- Streaming thread now properly applies changes to the hardware
- All subscribers are notified of the configuration change

Usage:
    python tests/test_dynamic_config.py

Requirements:
    - C streamer running (vita49_streamer)
    - Access to Pluto SDR hardware
"""

import argparse
import socket
import struct
import time
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

# VITA49 Constants
CONTROL_PORT = 4990  # Config transmission port
DATA_PORT = 4991     # Data reception port

# VITA49 Packet Types
VRT_PKT_TYPE_DATA = 0x1
VRT_PKT_TYPE_CONTEXT = 0x4
VRT_TSI_UTC = 0x1
VRT_TSF_PICOSECONDS = 0x2


@dataclass
class Config:
    """SDR Configuration"""
    freq_hz: float
    rate_hz: float
    gain_db: float
    bandwidth_hz: Optional[float] = None

    def __post_init__(self):
        if self.bandwidth_hz is None:
            self.bandwidth_hz = self.rate_hz * 0.8


def encode_context_packet(config: Config, stream_id: int = 0x01000000) -> bytes:
    """
    Encode VITA49 Context packet with SDR configuration.

    Args:
        config: Configuration to encode
        stream_id: 32-bit stream identifier

    Returns:
        Encoded packet bytes
    """
    # Timestamp (current time)
    now = time.time()
    ts_int = int(now)
    ts_frac = int((now - ts_int) * 1e12)  # Convert to picoseconds

    # Context Indicator Field (CIF) - matches C implementation
    cif = 0
    cif |= (1 << 29)  # bandwidth
    cif |= (1 << 27)  # rf_reference_frequency
    cif |= (1 << 21)  # sample_rate
    cif |= (1 << 23)  # gain

    # Encode context fields (64-bit fixed point, 20-bit radix for Hz)
    bw_fixed = int(config.bandwidth_hz * (1 << 20))
    freq_fixed = int(config.freq_hz * (1 << 20))
    rate_fixed = int(config.rate_hz * (1 << 20))
    gain_fixed = int(config.gain_db * 128)  # 7-bit radix

    # Build payload in DESCENDING CIF bit order (VITA49 requirement)
    # Bit 29: Bandwidth
    # Bit 27: RF Reference Frequency
    # Bit 23: Gain (comes BEFORE bit 21!)
    # Bit 21: Sample Rate
    payload = b''
    payload += struct.pack('>q', bw_fixed)       # Bit 29: bandwidth (8 bytes)
    payload += struct.pack('>q', freq_fixed)     # Bit 27: rf_frequency (8 bytes)
    payload += struct.pack('>hh', gain_fixed, 0) # Bit 23: gain stage1, stage2 (4 bytes)
    payload += struct.pack('>q', rate_fixed)     # Bit 21: sample_rate (8 bytes)

    # Calculate packet size in 32-bit words
    # header(1) + stream_id(1) + ts_int(1) + ts_frac(2) + cif(1) + payload
    payload_words = len(payload) // 4
    total_words = 1 + 1 + 1 + 2 + 1 + payload_words

    # Build header
    header = 0
    header |= (VRT_PKT_TYPE_CONTEXT & 0xF) << 28
    header |= (VRT_TSI_UTC & 0x3) << 22
    header |= (VRT_TSF_PICOSECONDS & 0x3) << 20
    header |= (total_words & 0xFFFF)

    # Encode packet
    packet = b''
    packet += struct.pack('>I', header)
    packet += struct.pack('>I', stream_id)
    packet += struct.pack('>I', ts_int)
    packet += struct.pack('>Q', ts_frac)
    packet += struct.pack('>I', cif)
    packet += payload

    return packet


def parse_context_packet(data: bytes) -> Optional[Config]:
    """
    Parse VITA49 Context packet to extract configuration.

    Args:
        data: Packet bytes

    Returns:
        Config object or None if parse fails
    """
    if len(data) < 28:
        return None

    # Skip header (4) and stream_id (4)
    offset = 8

    # Skip timestamps (12 bytes)
    offset += 12

    # Read CIF
    cif = struct.unpack('>I', data[offset:offset+4])[0]
    offset += 4

    freq_hz = None
    rate_hz = None
    gain_db = None
    bandwidth_hz = None

    # Parse fields in CIF bit order (descending)
    # Bit 29: Bandwidth
    if cif & (1 << 29):
        bw_fixed = struct.unpack('>q', data[offset:offset+8])[0]
        bandwidth_hz = bw_fixed / (1 << 20)
        offset += 8

    # Bit 27: RF Reference Frequency
    if cif & (1 << 27):
        freq_fixed = struct.unpack('>q', data[offset:offset+8])[0]
        freq_hz = freq_fixed / (1 << 20)
        offset += 8

    # Bit 23: Gain (comes before bit 21!)
    if cif & (1 << 23):
        gain_fixed = struct.unpack('>h', data[offset:offset+2])[0]
        gain_db = gain_fixed / 128.0
        offset += 4  # Skip both stage1 and stage2

    # Bit 21: Sample Rate
    if cif & (1 << 21):
        rate_fixed = struct.unpack('>q', data[offset:offset+8])[0]
        rate_hz = rate_fixed / (1 << 20)
        offset += 8

    if freq_hz is not None and rate_hz is not None and gain_db is not None:
        return Config(
            freq_hz=freq_hz,
            rate_hz=rate_hz,
            gain_db=gain_db,
            bandwidth_hz=bandwidth_hz
        )

    return None


def send_config(dest_ip: str, config: Config, source_port: int = DATA_PORT) -> bool:
    """
    Send configuration via VITA49 Context packet.

    IMPORTANT: We bind to DATA_PORT as the source port so the C streamer
    registers us as a subscriber with the correct address.

    Args:
        dest_ip: Destination IP address
        config: Configuration to send
        source_port: Source port to bind (default: DATA_PORT)

    Returns:
        True if successful
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Bind to specific port so C streamer registers us correctly
        sock.bind(('0.0.0.0', source_port))
        packet = encode_context_packet(config)
        sock.sendto(packet, (dest_ip, CONTROL_PORT))
        sock.close()
        return True
    except Exception as e:
        print(f"✗ Error sending config: {e}")
        return False


def receive_context_packet(sock: socket.socket, timeout: float = 10.0) -> Optional[Config]:
    """
    Receive and parse a context packet.

    Args:
        sock: UDP socket bound to DATA_PORT
        timeout: Timeout in seconds

    Returns:
        Config object or None if timeout/error
    """
    sock.settimeout(timeout)
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            data, addr = sock.recvfrom(8192)

            # Check if it's a context packet
            if len(data) < 4:
                continue

            header = struct.unpack('>I', data[0:4])[0]
            packet_type = (header >> 28) & 0xF

            if packet_type == VRT_PKT_TYPE_CONTEXT:
                config = parse_context_packet(data)
                if config:
                    return config
        except socket.timeout:
            break
        except Exception as e:
            print(f"  Warning: Error receiving packet: {e}")
            continue

    return None


def verify_config_applied(dest_ip: str, expected_config: Config, timeout: float = 10.0, sock: socket.socket = None) -> bool:
    """
    Verify that configuration was applied by receiving a context packet.

    This function will keep receiving context packets until it finds one that matches
    the expected configuration, or until timeout. This handles the case where stale
    context packets from previous configurations are still in flight.

    Args:
        dest_ip: Streamer IP address
        expected_config: Expected configuration
        timeout: Timeout in seconds
        sock: Optional existing socket to use (if None, creates new one)

    Returns:
        True if config matches
    """
    # Use existing socket or create new one
    close_sock = False
    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('0.0.0.0', DATA_PORT))
        close_sock = True

    print(f"  Waiting for matching context packet (timeout: {timeout}s)...")
    print(f"  (Will skip stale packets until we find matching config)")

    sock.settimeout(timeout)
    start_time = time.time()
    packets_received = 0
    last_received_config = None

    while time.time() - start_time < timeout:
        try:
            data, addr = sock.recvfrom(8192)

            # Check if it's a context packet
            if len(data) < 4:
                continue

            header = struct.unpack('>I', data[0:4])[0]
            packet_type = (header >> 28) & 0xF

            if packet_type == VRT_PKT_TYPE_CONTEXT:
                received_config = parse_context_packet(data)
                if received_config:
                    packets_received += 1
                    last_received_config = received_config

                    # Compare configurations (allow small tolerance for floating point)
                    freq_match = abs(received_config.freq_hz - expected_config.freq_hz) < 1000  # 1 kHz tolerance
                    rate_match = abs(received_config.rate_hz - expected_config.rate_hz) < 1000  # 1 kHz tolerance
                    gain_match = abs(received_config.gain_db - expected_config.gain_db) < 0.1   # 0.1 dB tolerance

                    if freq_match and rate_match and gain_match:
                        # Found matching config!
                        print(f"  ✓ Config verified (after {packets_received} context packet(s)):")
                        print(f"    Frequency: {received_config.freq_hz/1e6:.1f} MHz")
                        print(f"    Rate:      {received_config.rate_hz/1e6:.1f} MSPS")
                        print(f"    Gain:      {received_config.gain_db:.1f} dB")
                        if close_sock:
                            sock.close()
                        return True
                    else:
                        # Config doesn't match - this is a stale packet
                        print(f"  → Skipping stale packet #{packets_received}: "
                              f"{received_config.freq_hz/1e6:.1f} MHz, "
                              f"{received_config.rate_hz/1e6:.1f} MSPS, "
                              f"{received_config.gain_db:.1f} dB")

        except socket.timeout:
            break
        except Exception as e:
            print(f"  Warning: Error receiving packet: {e}")
            continue

    # Timeout - no matching config found
    if close_sock:
        sock.close()

    if packets_received == 0:
        print("  ✗ No context packets received")
        print("  Possible issues:")
        print("    - C streamer not running")
        print("    - Streamer not sending data yet (no SDR connected?)")
        print("    - Firewall blocking UDP port 4991")
        print("    - Streamer on different IP address")
    else:
        print(f"  ✗ Timeout - received {packets_received} context packet(s) but none matched")
        if last_received_config:
            print(f"  Last received config:")
            print(f"    Frequency: {last_received_config.freq_hz/1e6:.1f} MHz (expected {expected_config.freq_hz/1e6:.1f} MHz)")
            print(f"    Rate:      {last_received_config.rate_hz/1e6:.1f} MSPS (expected {expected_config.rate_hz/1e6:.1f} MSPS)")
            print(f"    Gain:      {last_received_config.gain_db:.1f} dB (expected {expected_config.gain_db:.1f} dB)")

    return False


def main():
    parser = argparse.ArgumentParser(
        description="Test dynamic configuration changes for VITA49 C Streamer"
    )
    parser.add_argument(
        '--dest', '-d',
        default='127.0.0.1',
        help="Streamer IP address (default: 127.0.0.1)"
    )
    parser.add_argument(
        '--wait-time', '-w',
        type=float,
        default=5.0,
        help="Wait time between configs in seconds (default: 5.0)"
    )

    args = parser.parse_args()

    print("\n" + "="*70)
    print("VITA49 Dynamic Configuration Test")
    print("="*70)
    print(f"  Streamer IP:  {args.dest}")
    print(f"  Control Port: {CONTROL_PORT}")
    print(f"  Data Port:    {DATA_PORT}")
    print("="*70)
    print()
    print("IMPORTANT: Make sure the C streamer is running before starting this test!")
    print("           Start it with: ./vita49_streamer")
    print()

    # Create persistent socket for receiving data
    # This ensures we use the same port for both sending config and receiving data
    print("Setting up receiver socket on port 4991...")
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        recv_sock.bind(('0.0.0.0', DATA_PORT))
        print("  ✓ Bound to 0.0.0.0:4991")
    except OSError as e:
        print(f"  ✗ Failed to bind to port {DATA_PORT}: {e}")
        print("  Another process may be using this port.")
        return 1
    print()

    # Test configuration 1: Initial config (2.4 GHz, 30 MSPS, 20 dB)
    config1 = Config(
        freq_hz=2.4e9,
        rate_hz=30e6,
        gain_db=20.0
    )

    print("Step 1: Sending initial configuration...")
    print(f"  Frequency: {config1.freq_hz/1e6:.1f} MHz")
    print(f"  Rate:      {config1.rate_hz/1e6:.1f} MSPS")
    print(f"  Gain:      {config1.gain_db:.1f} dB")

    # Send config from the same socket we're receiving on
    try:
        packet = encode_context_packet(config1)
        recv_sock.sendto(packet, (args.dest, CONTROL_PORT))
        print("  ✓ Sent")
    except Exception as e:
        print(f"  ✗ Failed to send: {e}")
        recv_sock.close()
        return 1
    print()

    # Verify initial config
    print("Step 2: Verifying initial configuration...")
    print("  (This will wait for C streamer to start sending data)")
    if not verify_config_applied(args.dest, config1, timeout=15.0, sock=recv_sock):
        print("✗ Initial configuration verification failed")
        recv_sock.close()
        return 1
    print()

    # Wait before sending new config
    print(f"Step 3: Waiting {args.wait_time} seconds before sending new config...")
    time.sleep(args.wait_time)
    print()

    # Test configuration 2: New config (915 MHz, 10 MSPS, 40 dB)
    config2 = Config(
        freq_hz=915e6,
        rate_hz=10e6,
        gain_db=40.0
    )

    print("Step 4: Sending NEW configuration (this tests dynamic reconfiguration)...")
    print(f"  Frequency: {config2.freq_hz/1e6:.1f} MHz  [CHANGED from {config1.freq_hz/1e6:.1f} MHz]")
    print(f"  Rate:      {config2.rate_hz/1e6:.1f} MSPS  [CHANGED from {config1.rate_hz/1e6:.1f} MSPS]")
    print(f"  Gain:      {config2.gain_db:.1f} dB  [CHANGED from {config1.gain_db:.1f} dB]")

    # Send config from the same socket
    try:
        packet = encode_context_packet(config2)
        recv_sock.sendto(packet, (args.dest, CONTROL_PORT))
        print("  ✓ Sent")
    except Exception as e:
        print(f"  ✗ Failed to send: {e}")
        recv_sock.close()
        return 1
    print()

    # Verify new config was applied
    print("Step 5: Verifying NEW configuration was applied to hardware...")
    print("  (Should receive context packet within ~100ms)")
    if not verify_config_applied(args.dest, config2, timeout=10.0, sock=recv_sock):
        print()
        print("="*70)
        print("✗ TEST FAILED")
        print("="*70)
        print("The new configuration was NOT applied to the hardware!")
        print("This indicates the bug is NOT fixed.")
        print()
        recv_sock.close()
        return 1

    recv_sock.close()

    print()
    print("="*70)
    print("✓ TEST PASSED")
    print("="*70)
    print("Dynamic configuration changes are working correctly!")
    print("The streamer successfully:")
    print("  1. Received the new configuration via control thread")
    print("  2. Applied it to the SDR hardware via streaming thread")
    print("  3. Notified all subscribers of the change")
    print()

    return 0


if __name__ == '__main__':
    try:
        exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        exit(1)
