#!/usr/bin/env python3
"""
VITA49 Configuration Client

Send configuration commands to Pluto via VITA49 Context packets.
This allows remote control of SDR parameters without SSH.

Usage:
    # Configure Pluto at 192.168.2.1 to 5.8 GHz, 20 MSPS, 30 dB gain
    python vita49_config_client.py --pluto 192.168.2.1 --freq 5.8e9 --rate 20e6 --gain 30

    # Quick reconfigure (change only frequency)
    python vita49_config_client.py --pluto 192.168.2.1 --freq 2.4e9
"""

import argparse
import socket
import struct
import time


class VITA49ConfigClient:
    """
    Send configuration to Pluto via VITA49 Context packets.
    """

    def __init__(self, pluto_ip, control_port=4990, data_port=4991):
        self.pluto_ip = pluto_ip
        self.control_port = control_port
        self.data_port = data_port
        self.stream_id = 0x01000000  # Match server default

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def encode_context(self, sample_rate_hz=None, center_freq_hz=None,
                      bandwidth_hz=None, gain_db=None):
        """
        Encode VITA49 Context packet with configuration.

        Args:
            sample_rate_hz: Sample rate in Hz (optional)
            center_freq_hz: Center frequency in Hz (optional)
            bandwidth_hz: Bandwidth in Hz (optional)
            gain_db: RX gain in dB (optional)

        Returns:
            bytes ready for UDP transmission
        """
        timestamp = time.time()
        int_sec = int(timestamp)
        frac_sec = int((timestamp - int_sec) * 1e12)

        # Build Context Indicator Field (CIF)
        cif = 0
        context_fields = []

        def encode_hz(val):
            return struct.pack('>q', int(val * (1 << 20)))

        if bandwidth_hz is not None:
            cif |= (1 << 29)
            context_fields.append(encode_hz(bandwidth_hz))

        if center_freq_hz is not None:
            cif |= (1 << 27)
            context_fields.append(encode_hz(center_freq_hz))

        if sample_rate_hz is not None:
            cif |= (1 << 21)
            context_fields.append(encode_hz(sample_rate_hz))

        if gain_db is not None:
            cif |= (1 << 23)
            gain_fixed = int(gain_db * 128)
            context_fields.append(struct.pack('>hh', gain_fixed, 0))

        # Calculate packet size
        field_bytes = b''.join(context_fields)
        field_words = len(field_bytes) // 4
        packet_words = 1 + 1 + 1 + 2 + 1 + field_words

        # Build header (Context packet)
        header = 0
        header |= (0b0100 & 0xF) << 28  # Packet type = Context
        header |= (0x01 & 0x3) << 22    # TSI = UTC
        header |= (0x02 & 0x3) << 20    # TSF = picoseconds
        header |= (packet_words & 0xFFFF)

        return b''.join([
            struct.pack('>I', header),
            struct.pack('>I', self.stream_id),
            struct.pack('>I', int_sec),
            struct.pack('>Q', frac_sec),
            struct.pack('>I', cif),
            field_bytes,
        ])

    def configure(self, sample_rate_hz=None, center_freq_hz=None,
                 bandwidth_hz=None, gain_db=None):
        """
        Send configuration to Pluto.

        Returns:
            True if successful
        """
        # Auto-adjust bandwidth if not specified
        if sample_rate_hz and not bandwidth_hz:
            bandwidth_hz = sample_rate_hz * 0.8

        # Encode context packet
        packet = self.encode_context(
            sample_rate_hz=sample_rate_hz,
            center_freq_hz=center_freq_hz,
            bandwidth_hz=bandwidth_hz,
            gain_db=gain_db
        )

        try:
            # Send to Pluto's control port
            self.socket.sendto(packet, (self.pluto_ip, self.control_port))
            print(f"✓ Configuration sent to {self.pluto_ip}:{self.control_port}")

            # Print what was configured
            if sample_rate_hz:
                print(f"  Sample Rate: {sample_rate_hz/1e6:.1f} MSPS")
            if center_freq_hz:
                print(f"  Frequency:   {center_freq_hz/1e9:.3f} GHz")
            if bandwidth_hz:
                print(f"  Bandwidth:   {bandwidth_hz/1e6:.1f} MHz")
            if gain_db is not None:
                print(f"  Gain:        {gain_db} dB")

            print(f"\nPluto will now stream to this PC on UDP port {self.data_port}")
            return True

        except Exception as e:
            print(f"✗ Failed to send configuration: {e}")
            return False

    def close(self):
        """Close socket."""
        self.socket.close()


def main():
    parser = argparse.ArgumentParser(
        description="VITA49 Configuration Client for Pluto",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full configuration
  python vita49_config_client.py --pluto 192.168.2.1 \\
      --freq 2.4e9 --rate 30e6 --gain 40

  # Quick frequency change
  python vita49_config_client.py --pluto 192.168.2.1 --freq 5.8e9

  # Adjust gain only
  python vita49_config_client.py --pluto 192.168.2.1 --gain 20

After sending config, Pluto will stream IQ samples to your PC.
Use a receiver script to capture and process the data.
        """
    )

    parser.add_argument(
        '--pluto', '-p',
        required=True,
        help="Pluto IP address (e.g., 192.168.2.1 or pluto.local)"
    )
    parser.add_argument(
        '--freq', '-f',
        type=float,
        default=None,
        help="Center frequency in Hz (e.g., 2.4e9)"
    )
    parser.add_argument(
        '--rate', '-r',
        type=float,
        default=None,
        help="Sample rate in Hz (e.g., 30e6)"
    )
    parser.add_argument(
        '--gain', '-g',
        type=float,
        default=None,
        help="RX gain in dB (e.g., 40)"
    )
    parser.add_argument(
        '--bandwidth', '-b',
        type=float,
        default=None,
        help="Bandwidth in Hz (default: auto = 0.8 * sample_rate)"
    )
    parser.add_argument(
        '--control-port',
        type=int,
        default=4990,
        help="Control port (default: 4990)"
    )
    parser.add_argument(
        '--data-port',
        type=int,
        default=4991,
        help="Data port (default: 4991)"
    )

    args = parser.parse_args()

    # Check that at least one parameter is specified
    if not any([args.freq, args.rate, args.gain, args.bandwidth]):
        print("ERROR: Must specify at least one configuration parameter")
        print("       (--freq, --rate, --gain, or --bandwidth)")
        return 1

    print("="*60)
    print("VITA49 Configuration Client")
    print("="*60)
    print(f"Target: {args.pluto}:{args.control_port}")
    print()

    # Create client
    client = VITA49ConfigClient(
        pluto_ip=args.pluto,
        control_port=args.control_port,
        data_port=args.data_port
    )

    # Send configuration
    success = client.configure(
        sample_rate_hz=args.rate,
        center_freq_hz=args.freq,
        bandwidth_hz=args.bandwidth,
        gain_db=args.gain
    )

    client.close()

    return 0 if success else 1


if __name__ == '__main__':
    exit(main())
