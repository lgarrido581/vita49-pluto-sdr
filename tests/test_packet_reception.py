#!/usr/bin/env python3
"""
Quick test to verify VITA49 packet reception from Pluto
"""
import socket
import time

def test_vita49_reception(port=4991, timeout=10):
    """Listen for VITA49 packets and report what we receive"""
    print(f"Listening for VITA49 packets on port {port}...")
    print(f"Will wait {timeout} seconds for packets\n")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)

    start_time = time.time()
    packet_count = 0

    try:
        while time.time() - start_time < timeout:
            try:
                data, addr = sock.recvfrom(65536)
                packet_count += 1

                if packet_count == 1:
                    print(f"[OK] First packet received from {addr}")
                    print(f"  Packet size: {len(data)} bytes")
                    print(f"  First 4 bytes (header): {data[:4].hex()}")

                if packet_count % 100 == 0:
                    print(f"  Received {packet_count} packets...")

            except socket.timeout:
                continue

    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        sock.close()

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"  Total packets received: {packet_count}")
    print(f"  Elapsed time: {elapsed:.1f} seconds")
    if packet_count > 0:
        print(f"  Packet rate: {packet_count/elapsed:.1f} packets/sec")
        print(f"\n[SUCCESS] Packets are being received!")
    else:
        print(f"\n[PROBLEM] No packets received!")
        print(f"\nTroubleshooting:")
        print(f"  1. Is Pluto streamer running? (ssh root@pluto.local, check with 'ps aux | grep vita49')")
        print(f"  2. Is Pluto configured to send to this PC's IP?")
        print(f"  3. Is firewall blocking UDP port {port}?")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4991, help="UDP port to listen on")
    parser.add_argument("--timeout", type=int, default=10, help="Seconds to wait for packets")
    args = parser.parse_args()

    test_vita49_reception(args.port, args.timeout)
