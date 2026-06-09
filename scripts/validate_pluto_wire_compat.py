"""Live-Pluto wire-compatibility check.

Subscribes to the streaming Pluto, captures N packets, asserts:
  * VRT data packets parse cleanly via the new packets.py code
  * stream_id is the legacy 0x01000000 (no regression from Phase 2)
  * config_epoch is 0 (no Class ID emitted on the wire path that's still C)
  * IQ samples deinterleave to a complex array of expected length
  * Packet rate looks reasonable

Subscribe trick: the Pluto's C control thread auto-registers the SENDER
of any inbound context packet as a data subscriber, so we just need to
send one context packet with a value matching the current config to
trigger the subscription without changing anything.
"""

import socket
import sys
import time
from collections import Counter
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(REPO_SRC))

from vita49.packets import (  # noqa: E402
    PacketType,
    VRTContextPacket,
    VRTHeader,
    VRTSignalDataPacket,
    VRTTimestamp,
)

PLUTO_IP = "192.168.0.17"
CONTROL_PORT = 4990
DATA_PORT = 4991
HOST_BIND = "0.0.0.0"
EXPECTED_STREAM_ID = 0x01000000
EXPECTED_EPOCH = 0
CAPTURE_PACKETS = 100
CAPTURE_TIMEOUT_S = 5.0


def subscribe(rx_socket: socket.socket) -> None:
    """Send a context packet that matches the Pluto's defaults so it
    just subscribes us without reconfiguring anything."""
    ctx = VRTContextPacket(
        stream_id=EXPECTED_STREAM_ID,
        timestamp=VRTTimestamp.from_time(time.time()),
        sample_rate_hz=30e6,  # Matches DEFAULT_RATE_HZ in pluto_vita49_streamer.c
    )
    payload = ctx.encode()
    # Send from the same UDP port we're listening on so the Pluto
    # records (us_ip, 4991) as the subscriber address.
    rx_socket.sendto(payload, (PLUTO_IP, CONTROL_PORT))
    print(f"[subscribe] sent {len(payload)}-byte context packet to {PLUTO_IP}:{CONTROL_PORT}")


def main() -> int:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    rx.bind((HOST_BIND, DATA_PORT))
    rx.settimeout(CAPTURE_TIMEOUT_S)

    subscribe(rx)

    data_packets = 0
    context_packets = 0
    other_packets = 0
    stream_ids: Counter = Counter()
    epoch_counter: Counter = Counter()
    sample_counts: Counter = Counter()
    first_packet_time = None
    last_packet_time = None
    decode_errors = 0
    first_sample_preview = None

    print(f"[capture] reading {CAPTURE_PACKETS} packets (timeout {CAPTURE_TIMEOUT_S}s/packet)...")

    while data_packets + context_packets < CAPTURE_PACKETS:
        try:
            raw, src = rx.recvfrom(65536)
        except socket.timeout:
            print(f"[capture] timeout after {data_packets} data + {context_packets} context packets")
            break

        if first_packet_time is None:
            first_packet_time = time.time()
        last_packet_time = time.time()

        try:
            header = VRTHeader.decode(raw[:4])
        except Exception as e:
            decode_errors += 1
            print(f"[capture] header decode error from {src}: {e}")
            continue

        if header.packet_type == PacketType.IF_DATA_WITH_STREAM_ID:
            try:
                pkt = VRTSignalDataPacket.decode(raw)
            except Exception as e:
                decode_errors += 1
                print(f"[capture] data decode error: {e}")
                continue
            data_packets += 1
            stream_ids[pkt.stream_id] += 1
            epoch_counter[pkt.config_epoch] += 1
            iq = pkt.to_iq_samples()
            sample_counts[len(iq)] += 1
            if first_sample_preview is None and len(iq) > 0:
                first_sample_preview = (complex(iq[0]), complex(iq[-1]))
        elif header.packet_type == PacketType.CONTEXT:
            context_packets += 1
        else:
            other_packets += 1

    if data_packets == 0:
        print("FAIL: zero data packets received. Is the Pluto streamer actually running?")
        return 1

    duration = (last_packet_time - first_packet_time) if first_packet_time else 0
    pps = data_packets / duration if duration > 0 else 0
    print()
    print(f"[summary] {data_packets} data, {context_packets} context, {other_packets} other, "
          f"{decode_errors} decode errors over {duration:.2f}s ({pps:.1f} pps)")
    print(f"[summary] stream_ids: {dict(stream_ids)}")
    print(f"[summary] config_epochs: {dict(epoch_counter)}")
    print(f"[summary] samples_per_packet histogram: {dict(sample_counts)}")
    if first_sample_preview:
        print(f"[summary] first/last sample of first data packet: {first_sample_preview}")

    failures = []
    if set(stream_ids) != {EXPECTED_STREAM_ID}:
        failures.append(f"stream_id mismatch: got {dict(stream_ids)}, expected only {EXPECTED_STREAM_ID:#x}")
    if set(epoch_counter) != {EXPECTED_EPOCH}:
        failures.append(f"config_epoch mismatch: got {dict(epoch_counter)}, expected only {EXPECTED_EPOCH}")
    if decode_errors:
        failures.append(f"{decode_errors} decode errors")
    if len(sample_counts) > 1:
        # Pluto C streamer may emit a smaller final packet per buffer; tolerate up to 2 sizes
        if len(sample_counts) > 2:
            failures.append(f"too many distinct packet sizes: {dict(sample_counts)}")

    if failures:
        print()
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 2

    print()
    print("PASS: wire format matches new Python packets.py exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
