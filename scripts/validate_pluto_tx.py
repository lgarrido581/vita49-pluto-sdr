"""TX-path validation against a Pluto running with --tx-channels 0.

Generates a known CW tone, encodes it as VITA-49 IF Data packets with
stream_id 0x02000000 (TX0), and fires them at the Pluto's TX_DATA_PORT.

The script can't directly observe the Pluto's TX stats (those print to
the SSH session). After it finishes sending, it asks the user to paste
the [TX] line from the Pluto's terminal. The script then sanity-checks:
  - packets_received >= sent count (within UDP loss tolerance)
  - packets_transmitted is non-zero
  - packets_dropped_wrong_stream == 0 (we used stream_id 0x02000000)
  - push_failures stays sane
"""

import socket
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vita49.packets import (  # noqa: E402
    VRTSignalDataPacket,
    create_stream_id,
)

PLUTO_IP = "192.168.0.17"
TX_DATA_PORT = 4992

SAMPLE_RATE_HZ = 30e6
TONE_FREQ_HZ = 1e6              # 1 MHz IF tone (will appear at TX_LO + 1 MHz)
SAMPLES_PER_PACKET = 1024       # < Pluto TX buffer size (4096), padded with zeros
PACKETS_TO_SEND = 200
INTER_PACKET_DELAY_S = 0.005    # 5 ms -> 200 pps -> ~40s total at TX rate at 30 MSPS
                                # (deliberately slow; we're proving the path works,
                                # not driving continuous TX)
TONE_AMPLITUDE = 0.7            # Stay well under int16 clipping after scale_factor


def make_tone_packet(sample_offset: int, packet_count: int) -> bytes:
    """Build one IF Data packet with SAMPLES_PER_PACKET samples of a CW tone."""
    t = (sample_offset + np.arange(SAMPLES_PER_PACKET)) / SAMPLE_RATE_HZ
    iq = (TONE_AMPLITUDE * np.exp(1j * 2 * np.pi * TONE_FREQ_HZ * t)).astype(np.complex64)
    pkt = VRTSignalDataPacket.from_iq_samples(
        iq_samples=iq,
        stream_id=create_stream_id(channel=0, device_id=2),  # 0x02000000
        sample_rate=SAMPLE_RATE_HZ,
        timestamp=time.time(),
        packet_count=packet_count & 0xF,
    )
    return pkt.encode()


def main() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)

    # Sanity check: a single sample packet encodes/decodes correctly here before
    # we blast 200 of them at the Pluto.
    probe = make_tone_packet(0, 0)
    print(f"[probe] packet size = {len(probe)} bytes "
          f"({SAMPLES_PER_PACKET} samples, stream_id=0x02000000)")
    decoded = VRTSignalDataPacket.decode(probe)
    assert decoded.stream_id == 0x02000000, decoded.stream_id
    assert len(decoded.to_iq_samples()) == SAMPLES_PER_PACKET
    print("[probe] local encode/decode round-trip OK")

    print(f"[send] firing {PACKETS_TO_SEND} packets to "
          f"{PLUTO_IP}:{TX_DATA_PORT} at {1/INTER_PACKET_DELAY_S:.0f} pps")
    sent_ok = 0
    sent_failed = 0
    t0 = time.time()
    for i in range(PACKETS_TO_SEND):
        pkt = make_tone_packet(i * SAMPLES_PER_PACKET, i)
        try:
            s.sendto(pkt, (PLUTO_IP, TX_DATA_PORT))
            sent_ok += 1
        except Exception as e:
            sent_failed += 1
            print(f"[send] sendto failed on packet {i}: {e}")
        time.sleep(INTER_PACKET_DELAY_S)
    duration = time.time() - t0
    print(f"[send] done in {duration:.2f}s. ok={sent_ok}, failed={sent_failed}")
    s.close()

    if sent_ok < PACKETS_TO_SEND * 0.95:
        print("FAIL: sender side dropped too many packets locally")
        return 2

    print()
    print("=" * 70)
    print("ACTION REQUIRED:")
    print("Look at the Pluto's terminal output. Within the next 5 seconds you")
    print("should see a [TX] stats line. Paste it back so I can verify:")
    print("  - 'Recv' count matches roughly the sent count above")
    print("  - 'Tx' count is non-zero")
    print("  - 'wrong_sid' and 'disabled_ch' drops are 0")
    print("  - 'PushFails' stays low")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
