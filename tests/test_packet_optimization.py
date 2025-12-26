#!/usr/bin/env python3
"""
VITA49 Packet Optimization Test Suite

Tests UDP packet size optimization to prevent IP fragmentation and maximize throughput.
Validates that packets fit within MTU constraints for different network configurations.

Requirements:
    - tcpdump (for packet capture and analysis)
    - Root/sudo access (for tcpdump)
    - vita49_streamer binary in ../build/

Test Coverage:
    1. Standard MTU (1500 bytes)
    2. Jumbo frames (9000 bytes)
    3. Custom MTU values (576, 1492)
    4. IP fragmentation detection
    5. Network efficiency measurement
    6. Throughput comparison
"""

import os
import sys
import time
import subprocess
import socket
import struct
import threading
import statistics
from pathlib import Path
from typing import Dict, List, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Constants
CONTROL_PORT = 4990
DATA_PORT = 4991
VITA49_HEADER_SIZE = 20
VITA49_TRAILER_SIZE = 4
IP_HEADER_SIZE = 20
UDP_HEADER_SIZE = 8

# MTU Configurations to test
MTU_CONFIGS = {
    "Minimum IPv4": 576,
    "PPPoE": 1492,
    "Standard Ethernet": 1500,
    "Jumbo Frames": 9000
}


class PacketCapture:
    """Manages tcpdump packet capture for analysis"""

    def __init__(self, port: int, pcap_file: str):
        self.port = port
        self.pcap_file = pcap_file
        self.process = None

    def start(self):
        """Start tcpdump capture"""
        # tcpdump filter for UDP packets on DATA_PORT
        cmd = [
            'sudo', 'tcpdump',
            '-i', 'any',
            '-w', self.pcap_file,
            f'udp port {self.port}',
            '-U'  # Write packets immediately
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)  # Wait for tcpdump to start
            print(f"✓ Started packet capture: {self.pcap_file}")
        except FileNotFoundError:
            print("WARNING: tcpdump not found. Install with: sudo apt-get install tcpdump")
            raise
        except PermissionError:
            print("WARNING: Need sudo for tcpdump. Run as root or configure sudo.")
            raise

    def stop(self):
        """Stop tcpdump capture"""
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)
            print(f"✓ Stopped packet capture")

    def analyze(self) -> Dict:
        """Analyze captured packets using tshark"""
        if not os.path.exists(self.pcap_file):
            return {"error": "PCAP file not found"}

        results = {
            "total_packets": 0,
            "fragmented_packets": 0,
            "packet_sizes": [],
            "ip_sizes": [],
            "udp_sizes": []
        }

        try:
            # Get packet count
            cmd = ['tshark', '-r', self.pcap_file, '-T', 'fields', '-e', 'frame.number']
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            results["total_packets"] = len(output.strip().split('\n')) if output.strip() else 0

            # Get packet sizes (IP layer)
            cmd = ['tshark', '-r', self.pcap_file, '-T', 'fields', '-e', 'ip.len']
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            if output.strip():
                results["ip_sizes"] = [int(x) for x in output.strip().split('\n') if x]

            # Get UDP payload sizes
            cmd = ['tshark', '-r', self.pcap_file, '-T', 'fields', '-e', 'udp.length']
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            if output.strip():
                results["udp_sizes"] = [int(x) for x in output.strip().split('\n') if x]

            # Check for IP fragmentation
            cmd = ['tshark', '-r', self.pcap_file, '-T', 'fields', '-e', 'ip.flags.mf', '-e', 'ip.frag_offset']
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            if output.strip():
                for line in output.strip().split('\n'):
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        mf_flag = parts[0]  # More Fragments flag
                        frag_offset = parts[1]
                        if mf_flag == '1' or (frag_offset and int(frag_offset) > 0):
                            results["fragmented_packets"] += 1

            # Calculate packet size including headers
            if results["ip_sizes"]:
                # IP total length already includes IP header + UDP header + payload
                results["packet_sizes"] = results["ip_sizes"]

        except FileNotFoundError:
            print("WARNING: tshark not found. Install with: sudo apt-get install tshark")
            results["error"] = "tshark not available"
        except subprocess.CalledProcessError as e:
            results["error"] = f"Analysis failed: {e}"

        return results


class StreamerClient:
    """Simple client to receive VITA49 data packets"""

    def __init__(self):
        self.sock = None
        self.received_packets = []
        self.running = False
        self.thread = None

    def start(self):
        """Start receiving packets"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', DATA_PORT))
        self.sock.settimeout(0.5)

        self.running = True
        self.thread = threading.Thread(target=self._receive_loop)
        self.thread.daemon = True
        self.thread.start()
        print(f"✓ Started receiver on port {DATA_PORT}")

    def stop(self):
        """Stop receiving packets"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.sock:
            self.sock.close()
        print(f"✓ Stopped receiver")

    def _receive_loop(self):
        """Receive packets in background thread"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65536)
                self.received_packets.append({
                    'size': len(data),
                    'timestamp': time.time(),
                    'data': data
                })
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"Receive error: {e}")
                break

    def get_stats(self) -> Dict:
        """Get reception statistics"""
        if not self.received_packets:
            return {"total": 0}

        sizes = [p['size'] for p in self.received_packets]

        return {
            "total": len(self.received_packets),
            "min_size": min(sizes),
            "max_size": max(sizes),
            "avg_size": statistics.mean(sizes),
            "median_size": statistics.median(sizes)
        }


def test_mtu_configuration(mtu: int, mtu_name: str, duration: int = 5) -> Dict:
    """
    Test streamer with specific MTU configuration

    Args:
        mtu: MTU size in bytes
        mtu_name: Descriptive name for this MTU
        duration: How long to capture packets (seconds)

    Returns:
        Dictionary with test results
    """
    print(f"\n{'='*60}")
    print(f"Testing MTU: {mtu_name} ({mtu} bytes)")
    print(f"{'='*60}")

    # Calculate expected packet size
    vita49_overhead = VITA49_HEADER_SIZE + VITA49_TRAILER_SIZE
    ip_udp_overhead = IP_HEADER_SIZE + UDP_HEADER_SIZE
    available_payload = mtu - ip_udp_overhead - vita49_overhead

    samples_per_packet = (available_payload // 4) & ~1  # 4 bytes per sample, align to even
    expected_vita49_size = (samples_per_packet * 4) + vita49_overhead
    expected_udp_size = expected_vita49_size + ip_udp_overhead

    print(f"Expected samples/packet: {samples_per_packet}")
    print(f"Expected VITA49 size: {expected_vita49_size} bytes")
    print(f"Expected UDP datagram: {expected_udp_size} bytes")
    print(f"MTU efficiency: {100.0 * expected_udp_size / mtu:.1f}%\n")

    # Setup paths
    build_dir = Path(__file__).parent.parent / "build"
    streamer_bin = build_dir / "vita49_streamer"
    pcap_file = f"/tmp/vita49_mtu_{mtu}.pcap"

    if not streamer_bin.exists():
        return {
            "error": f"Streamer binary not found: {streamer_bin}",
            "mtu": mtu,
            "mtu_name": mtu_name
        }

    # Start components
    capture = PacketCapture(DATA_PORT, pcap_file)
    client = StreamerClient()

    results = {
        "mtu": mtu,
        "mtu_name": mtu_name,
        "expected_samples_per_packet": samples_per_packet,
        "expected_packet_size": expected_udp_size,
        "duration": duration
    }

    try:
        # Start packet capture
        capture.start()

        # Start receiver
        client.start()
        time.sleep(0.5)

        # Start streamer with MTU argument
        if mtu == 9000:
            cmd = [str(streamer_bin), '--jumbo']
        else:
            cmd = [str(streamer_bin), '--mtu', str(mtu)]

        print(f"Starting streamer: {' '.join(cmd)}")
        streamer = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Let it run
        print(f"Capturing for {duration} seconds...")
        time.sleep(duration)

        # Stop streamer
        streamer.terminate()
        try:
            stdout, stderr = streamer.communicate(timeout=5)
            results["streamer_stdout"] = stdout.decode()
            results["streamer_stderr"] = stderr.decode()
        except subprocess.TimeoutExpired:
            streamer.kill()

        # Stop receiver
        client.stop()
        time.sleep(0.5)

        # Stop capture
        capture.stop()
        time.sleep(1)

        # Get client stats
        client_stats = client.get_stats()
        results["received_packets"] = client_stats

        # Analyze capture
        print("\nAnalyzing packet capture...")
        pcap_stats = capture.analyze()
        results["pcap_analysis"] = pcap_stats

        # Check for fragmentation
        if pcap_stats.get("fragmented_packets", 0) > 0:
            print(f"⚠ WARNING: {pcap_stats['fragmented_packets']} fragmented packets detected!")
            results["fragmentation_detected"] = True
        else:
            print(f"✓ No IP fragmentation detected")
            results["fragmentation_detected"] = False

        # Validate packet sizes
        if pcap_stats.get("ip_sizes"):
            actual_sizes = pcap_stats["ip_sizes"]
            max_size = max(actual_sizes)
            min_size = min(actual_sizes)
            avg_size = statistics.mean(actual_sizes)

            print(f"\nPacket size analysis:")
            print(f"  Total packets: {len(actual_sizes)}")
            print(f"  Min size: {min_size} bytes")
            print(f"  Max size: {max_size} bytes")
            print(f"  Avg size: {avg_size:.1f} bytes")

            # Check if packets fit in MTU
            if max_size > mtu:
                print(f"  ⚠ WARNING: Packets exceed MTU ({max_size} > {mtu})")
                results["mtu_exceeded"] = True
            else:
                efficiency = 100.0 * avg_size / mtu
                print(f"  ✓ All packets fit in MTU")
                print(f"  Network efficiency: {efficiency:.1f}%")
                results["mtu_exceeded"] = False
                results["network_efficiency"] = efficiency

    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        results["error"] = str(e)
    finally:
        # Cleanup
        try:
            capture.stop()
        except:
            pass
        try:
            client.stop()
        except:
            pass

    return results


def generate_performance_report(all_results: List[Dict]):
    """Generate comprehensive performance comparison report"""

    print("\n" + "="*80)
    print("PERFORMANCE COMPARISON REPORT")
    print("="*80)

    print("\n{:<20} {:<10} {:<15} {:<15} {:<12}".format(
        "MTU Configuration", "MTU (B)", "Samples/Pkt", "Efficiency (%)", "Fragmentation"
    ))
    print("-" * 80)

    for result in all_results:
        if "error" in result:
            print(f"{result['mtu_name']:<20} ERROR: {result['error']}")
            continue

        mtu_name = result.get("mtu_name", "Unknown")
        mtu = result.get("mtu", 0)
        samples = result.get("expected_samples_per_packet", 0)
        efficiency = result.get("network_efficiency", 0.0)
        fragmented = "YES ⚠" if result.get("fragmentation_detected") else "NO ✓"

        print("{:<20} {:<10} {:<15} {:<15.1f} {:<12}".format(
            mtu_name, mtu, samples, efficiency, fragmented
        ))

    print("\n" + "="*80)
    print("PACKET ANALYSIS")
    print("="*80)

    for result in all_results:
        if "error" in result or "pcap_analysis" not in result:
            continue

        pcap = result["pcap_analysis"]
        if not pcap.get("ip_sizes"):
            continue

        print(f"\n{result['mtu_name']} (MTU {result['mtu']}):")
        print(f"  Total packets captured: {pcap.get('total_packets', 0)}")
        print(f"  Fragmented packets: {pcap.get('fragmented_packets', 0)}")

        if pcap.get("ip_sizes"):
            sizes = pcap["ip_sizes"]
            print(f"  Packet sizes: min={min(sizes)}B, max={max(sizes)}B, avg={statistics.mean(sizes):.1f}B")

        if result.get("received_packets"):
            rcv = result["received_packets"]
            print(f"  Received by client: {rcv.get('total', 0)} packets")

    print("\n" + "="*80)
    print("RECOMMENDATIONS")
    print("="*80)

    # Find most efficient configuration without fragmentation
    valid_results = [r for r in all_results
                     if not r.get("error")
                     and not r.get("fragmentation_detected")
                     and not r.get("mtu_exceeded")]

    if valid_results:
        best = max(valid_results, key=lambda x: x.get("network_efficiency", 0))
        print(f"\n✓ Best configuration: {best['mtu_name']} (MTU {best['mtu']})")
        print(f"  - Network efficiency: {best.get('network_efficiency', 0):.1f}%")
        print(f"  - Samples per packet: {best.get('expected_samples_per_packet', 0)}")
        print(f"  - No fragmentation, optimal throughput")

        if best['mtu'] == 9000:
            print("\n  Note: Jumbo frames require network infrastructure support:")
            print("  - All switches/routers in path must support jumbo frames")
            print("  - Network interface must be configured: ifconfig <if> mtu 9000")
    else:
        print("\n⚠ WARNING: No valid configurations found without fragmentation")

    print("\n" + "="*80)


def main():
    """Run comprehensive packet optimization tests"""

    print("\n" + "="*80)
    print("VITA49 PACKET OPTIMIZATION TEST SUITE")
    print("="*80)
    print("\nThis test validates UDP packet sizing to prevent IP fragmentation")
    print("and maximize network throughput across different MTU configurations.\n")

    # Check requirements
    print("Checking requirements...")

    # Check for tcpdump
    try:
        subprocess.run(['which', 'tcpdump'], check=True, capture_output=True)
        print("✓ tcpdump found")
    except subprocess.CalledProcessError:
        print("✗ tcpdump not found. Install with: sudo apt-get install tcpdump")
        return 1

    # Check for tshark
    try:
        subprocess.run(['which', 'tshark'], check=True, capture_output=True)
        print("✓ tshark found")
    except subprocess.CalledProcessError:
        print("✗ tshark not found. Install with: sudo apt-get install tshark")
        return 1

    # Check for streamer binary
    build_dir = Path(__file__).parent.parent / "build"
    streamer_bin = build_dir / "vita49_streamer"

    if not streamer_bin.exists():
        print(f"✗ Streamer binary not found: {streamer_bin}")
        print("  Build with: cd build && make")
        return 1
    print(f"✓ Streamer binary found")

    # Run tests for each MTU configuration
    all_results = []

    for mtu_name, mtu_value in MTU_CONFIGS.items():
        result = test_mtu_configuration(mtu_value, mtu_name, duration=5)
        all_results.append(result)
        time.sleep(2)  # Brief pause between tests

    # Generate report
    generate_performance_report(all_results)

    print("\n✓ All tests completed\n")
    return 0


if __name__ == '__main__':
    sys.exit(main())
