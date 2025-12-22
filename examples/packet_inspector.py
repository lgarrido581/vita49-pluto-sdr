#!/usr/bin/env python3
"""
VITA49 Packet Inspector / Debugger

A simple debugging tool that captures and displays raw and decoded VITA49 packets.
Useful for verifying packet format, troubleshooting streams, and understanding
VITA49 protocol details.

Usage:
    python vita49_packet_inspector.py --port 4991
    python vita49_packet_inspector.py --port 4991 --count 10
    python vita49_packet_inspector.py --port 4991 --continuous
"""

import argparse
import socket
import struct
import sys
from datetime import datetime
from typing import Optional

from vita49_packets import (
    VRTHeader,
    VRTSignalDataPacket,
    VRTContextPacket,
    VRTTimestamp,
    PacketType,
    TSI,
    TSF
)


class VITA49PacketInspector:
    """
    Inspector for VITA49 packets - captures and displays detailed packet information
    """

    def __init__(self, port: int = 4991, listen_address: str = "0.0.0.0"):
        self.port = port
        self.listen_address = listen_address
        self.socket: Optional[socket.socket] = None
        self.packets_captured = 0

    def start(self) -> bool:
        """Start listening for packets"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self.socket.bind((self.listen_address, self.port))
            self.socket.settimeout(5.0)  # 5 second timeout
            print(f"✓ Listening on {self.listen_address}:{self.port}")
            return True

        except Exception as e:
            print(f"✗ Failed to start: {e}")
            return False

    def stop(self):
        """Stop listening"""
        if self.socket:
            self.socket.close()
            self.socket = None

    def receive_packet(self) -> Optional[bytes]:
        """Receive one UDP packet"""
        try:
            data, addr = self.socket.recvfrom(65536)
            self.packets_captured += 1
            return data

        except socket.timeout:
            print("⏱  Timeout waiting for packet (5s)")
            return None
        except Exception as e:
            print(f"✗ Error receiving packet: {e}")
            return None

    def display_raw_packet(self, data: bytes, addr=None):
        """Display raw packet bytes"""
        print("\n" + "="*70)
        print("RAW PACKET DATA")
        print("="*70)
        print(f"Packet #{self.packets_captured}")
        print(f"Length: {len(data)} bytes")
        print(f"Time: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        if addr:
            print(f"From: {addr[0]}:{addr[1]}")

        # Display hex dump
        print("\nHex Dump (first 128 bytes):")
        print("-" * 70)
        for i in range(0, min(len(data), 128), 16):
            # Offset
            hex_str = f"{i:04x}  "

            # Hex bytes
            hex_bytes = []
            ascii_str = ""
            for j in range(16):
                if i + j < len(data):
                    byte = data[i + j]
                    hex_bytes.append(f"{byte:02x}")
                    # ASCII representation
                    ascii_str += chr(byte) if 32 <= byte < 127 else '.'
                else:
                    hex_bytes.append("  ")
                    ascii_str += " "

            # Format with space every 4 bytes
            hex_str += " ".join([
                "".join(hex_bytes[k:k+2]) for k in range(0, 16, 2)
            ])
            hex_str += f"  |{ascii_str}|"

            print(hex_str)

    def display_header(self, header: VRTHeader):
        """Display decoded VRT header"""
        print("\n" + "-"*70)
        print("HEADER FIELDS")
        print("-"*70)
        print(f"  Packet Type:      {header.packet_type.name} ({header.packet_type.value})")
        print(f"  Class ID Present: {header.class_id_present}")
        print(f"  Trailer Present:  {header.trailer_present}")
        print(f"  TSI:              {header.tsi.name}")
        print(f"  TSF:              {header.tsf.name}")
        print(f"  Packet Count:     {header.packet_count} (0x{header.packet_count:X})")
        print(f"  Packet Size:      {header.packet_size} words ({header.packet_size * 4} bytes)")

    def display_timestamp(self, timestamp: VRTTimestamp):
        """Display timestamp information"""
        print("\n" + "-"*70)
        print("TIMESTAMP")
        print("-"*70)
        print(f"  Integer Seconds:     {timestamp.integer_seconds}")
        print(f"  Fractional Seconds:  {timestamp.fractional_seconds} ps")

        # Convert to readable time
        time_float = timestamp.to_time()
        dt = datetime.fromtimestamp(time_float)
        print(f"  As Float:            {time_float:.9f}")
        print(f"  As DateTime:         {dt.strftime('%Y-%m-%d %H:%M:%S.%f')}")

    def display_signal_data_packet(self, packet: VRTSignalDataPacket):
        """Display signal data packet details"""
        print("\n" + "-"*70)
        print("SIGNAL DATA PACKET")
        print("-"*70)
        print(f"  Stream ID:        0x{packet.stream_id:08X}")

        # Parse stream ID components
        from vita49_packets import parse_stream_id
        stream_info = parse_stream_id(packet.stream_id)
        print(f"    Channel:        {stream_info['channel']}")
        print(f"    Device ID:      {stream_info['device_id']}")
        print(f"    Data Type:      {stream_info['data_type']}")

        if packet.timestamp:
            self.display_timestamp(packet.timestamp)

        # Payload info
        print("\n" + "-"*70)
        print("PAYLOAD")
        print("-"*70)
        print(f"  Payload Length:   {len(packet.payload)} int16 values ({len(packet.payload) * 2} bytes)")
        print(f"  IQ Samples:       {len(packet.payload) // 2} complex samples")

        # Convert to IQ and show statistics
        iq_samples = packet.to_iq_samples()
        print(f"\n  IQ Statistics:")
        print(f"    I mean:         {iq_samples.real.mean():.6f}")
        print(f"    Q mean:         {iq_samples.imag.mean():.6f}")
        print(f"    Magnitude mean: {abs(iq_samples).mean():.6f}")
        print(f"    Power (dBFS):   {10 * np.log10(np.mean(np.abs(iq_samples)**2) + 1e-10):.2f}")

        # Show first few samples
        print(f"\n  First 5 Samples:")
        for i in range(min(5, len(iq_samples))):
            print(f"    [{i}] {iq_samples[i].real:+.6f} {iq_samples[i].imag:+.6f}j")

        # Trailer
        if packet.trailer:
            print("\n" + "-"*70)
            print("TRAILER")
            print("-"*70)
            print(f"  Valid Data:       {packet.trailer.valid_data}")

    def display_context_packet(self, packet: VRTContextPacket):
        """Display context packet details"""
        print("\n" + "-"*70)
        print("CONTEXT PACKET")
        print("-"*70)
        print(f"  Stream ID:        0x{packet.stream_id:08X}")

        if packet.timestamp:
            self.display_timestamp(packet.timestamp)

        # CIF
        print("\n" + "-"*70)
        print("CONTEXT INDICATOR FIELD (CIF)")
        print("-"*70)
        print(f"  Bandwidth:                 {packet.cif.bandwidth}")
        print(f"  IF Reference Frequency:    {packet.cif.if_reference_frequency}")
        print(f"  RF Reference Frequency:    {packet.cif.rf_reference_frequency}")
        print(f"  Sample Rate:               {packet.cif.sample_rate}")
        print(f"  Gain:                      {packet.cif.gain}")
        print(f"  Reference Level:           {packet.cif.reference_level}")
        print(f"  Temperature:               {packet.cif.temperature}")

        # Context fields
        print("\n" + "-"*70)
        print("CONTEXT FIELDS")
        print("-"*70)

        if packet.bandwidth_hz is not None:
            print(f"  Bandwidth:                 {packet.bandwidth_hz/1e6:.3f} MHz")

        if packet.if_reference_frequency_hz is not None:
            print(f"  IF Reference Frequency:    {packet.if_reference_frequency_hz/1e6:.3f} MHz")

        if packet.rf_reference_frequency_hz is not None:
            print(f"  RF Reference Frequency:    {packet.rf_reference_frequency_hz/1e9:.6f} GHz")
            print(f"                             {packet.rf_reference_frequency_hz/1e6:.3f} MHz")

        if packet.sample_rate_hz is not None:
            print(f"  Sample Rate:               {packet.sample_rate_hz/1e6:.3f} MSPS")
            print(f"                             {packet.sample_rate_hz:.0f} Hz")

        if packet.gain_db is not None:
            print(f"  Gain:                      {packet.gain_db:.2f} dB")

        if packet.reference_level_dbm is not None:
            print(f"  Reference Level:           {packet.reference_level_dbm:.2f} dBm")

        if packet.temperature_c is not None:
            print(f"  Temperature:               {packet.temperature_c:.2f} °C")

    def inspect_packet(self, data: bytes, show_raw: bool = True):
        """Inspect a packet and display all information"""
        if show_raw:
            self.display_raw_packet(data)

        try:
            # Decode header
            header = VRTHeader.decode(data[:4])
            self.display_header(header)

            # Decode based on packet type
            if header.packet_type in (PacketType.IF_DATA_WITH_STREAM_ID,
                                     PacketType.IF_DATA_WITHOUT_STREAM_ID):
                packet = VRTSignalDataPacket.decode(data)
                self.display_signal_data_packet(packet)

            elif header.packet_type == PacketType.CONTEXT:
                packet = VRTContextPacket.decode(data)
                self.display_context_packet(packet)

            else:
                print(f"\n⚠  Unknown packet type: {header.packet_type}")

        except Exception as e:
            print(f"\n✗ Error decoding packet: {e}")
            import traceback
            traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(
        description="VITA49 Packet Inspector - Debug and analyze VITA49 packets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Capture and display one packet
  python vita49_packet_inspector.py --port 4991

  # Capture 10 packets
  python vita49_packet_inspector.py --port 4991 --count 10

  # Continuous capture (Ctrl+C to stop)
  python vita49_packet_inspector.py --port 4991 --continuous

  # Hide raw hex dump
  python vita49_packet_inspector.py --port 4991 --no-raw
        """
    )

    parser.add_argument(
        '--port', '-p',
        type=int,
        default=4991,
        help="UDP port to listen on (default: 4991)"
    )
    parser.add_argument(
        '--count', '-c',
        type=int,
        default=1,
        help="Number of packets to capture (default: 1)"
    )
    parser.add_argument(
        '--continuous',
        action='store_true',
        help="Capture continuously until Ctrl+C"
    )
    parser.add_argument(
        '--no-raw',
        action='store_true',
        help="Don't show raw hex dump"
    )
    parser.add_argument(
        '--listen',
        default="0.0.0.0",
        help="Listen address (default: 0.0.0.0)"
    )

    args = parser.parse_args()

    # Import numpy here (only needed for signal data packets)
    global np
    import numpy as np

    print("\n" + "="*70)
    print("VITA49 PACKET INSPECTOR")
    print("="*70)
    print(f"Port: {args.port}")
    if args.continuous:
        print("Mode: Continuous (press Ctrl+C to stop)")
    else:
        print(f"Mode: Capture {args.count} packet(s)")
    print(f"Raw Hex Dump: {'No' if args.no_raw else 'Yes'}")
    print("="*70)

    inspector = VITA49PacketInspector(port=args.port, listen_address=args.listen)

    if not inspector.start():
        return 1

    try:
        count = 0
        while args.continuous or count < args.count:
            print(f"\n⏳ Waiting for packet...")

            data = inspector.receive_packet()
            if data is None:
                continue

            inspector.inspect_packet(data, show_raw=not args.no_raw)

            count += 1

            if not args.continuous:
                print(f"\n✓ Captured {count}/{args.count} packets")
                if count >= args.count:
                    break
            else:
                print(f"\n✓ Total packets captured: {count}")
                print("\nPress Ctrl+C to stop, or wait for next packet...")

    except KeyboardInterrupt:
        print("\n\n⏹  Stopped by user")

    finally:
        inspector.stop()

    print(f"\n{'='*70}")
    print(f"Session complete. Total packets: {inspector.packets_captured}")
    print("="*70 + "\n")

    return 0


if __name__ == '__main__':
    exit(main())
