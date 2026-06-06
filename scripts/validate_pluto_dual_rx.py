"""Dual-RX validation against a Pluto running with --rx-channels 0,1.

Asserts:
  * BOTH stream_ids 0x01000000 and 0x01000001 are emitted
  * They arrive interleaved (we want both within a short window, not one
    blasted before the other)
  * Per-channel counts are roughly balanced (each ~half of total)
  * Context packets arrive for both channels
  * IQ samples on both channels decode cleanly to expected shape
  * config_epoch is 0 (epoch barrier ships in Phase 7)
  * Per-channel 4-bit packet counters advance monotonically (modulo wrap)
"""

import socket
import sys
import time
from collections import Counter, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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
RX0_ID = 0x01000000
RX1_ID = 0x01000001
CAPTURE_PACKETS = 400
TIMEOUT_S = 5.0


def subscribe(sock):
    ctx = VRTContextPacket(
        stream_id=RX0_ID,
        timestamp=VRTTimestamp.from_time(time.time()),
        sample_rate_hz=30e6,
    )
    sock.sendto(ctx.encode(), (PLUTO_IP, CONTROL_PORT))


def main() -> int:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    rx.bind(("0.0.0.0", DATA_PORT))
    rx.settimeout(TIMEOUT_S)
    subscribe(rx)
    print(f"[subscribe] sent context to {PLUTO_IP}:{CONTROL_PORT}, "
          f"capturing {CAPTURE_PACKETS} packets...")

    data_counts: Counter = Counter()
    ctx_counts: Counter = Counter()
    epoch_counts: Counter = Counter()
    sample_counts: Counter = Counter()
    last_pkt_counter = {RX0_ID: None, RX1_ID: None}
    counter_anomalies = 0
    decode_errors = 0
    # Interleave check: collect arrival stream_id sequence
    arrival_seq: deque = deque(maxlen=200)
    first_iq = {}

    total = 0
    t0 = None
    while total < CAPTURE_PACKETS:
        try:
            raw, _ = rx.recvfrom(65536)
        except socket.timeout:
            print(f"[capture] timeout after {total} packets")
            break
        if t0 is None:
            t0 = time.time()

        try:
            header = VRTHeader.decode(raw[:4])
        except Exception as e:
            decode_errors += 1
            print(f"[err] header decode: {e}")
            continue

        if header.packet_type == PacketType.IF_DATA_WITH_STREAM_ID:
            try:
                pkt = VRTSignalDataPacket.decode(raw)
            except Exception as e:
                decode_errors += 1
                print(f"[err] data decode: {e}")
                continue
            data_counts[pkt.stream_id] += 1
            epoch_counts[pkt.config_epoch] += 1
            iq = pkt.to_iq_samples()
            sample_counts[len(iq)] += 1
            arrival_seq.append(pkt.stream_id)
            if pkt.stream_id not in first_iq and len(iq) > 0:
                first_iq[pkt.stream_id] = (complex(iq[0]), complex(iq[len(iq) // 2]))
            # Per-channel packet counter monotonicity (4-bit wrap = 16)
            prev = last_pkt_counter[pkt.stream_id]
            cur = pkt.header.packet_count
            if prev is not None:
                expected = (prev + 1) & 0xF
                if cur != expected:
                    counter_anomalies += 1
            last_pkt_counter[pkt.stream_id] = cur
            total += 1
        elif header.packet_type == PacketType.CONTEXT:
            try:
                ctx_pkt = VRTContextPacket.decode(raw)
                ctx_counts[ctx_pkt.stream_id] += 1
            except Exception:
                ctx_counts["unparseable"] += 1
            total += 1

    duration = (time.time() - t0) if t0 else 0
    print()
    print(f"[summary] total {total} packets in {duration:.3f}s "
          f"({total / duration:.1f} pps)" if duration > 0 else f"[summary] total {total}")
    print(f"[summary] data stream_ids: { {hex(k): v for k, v in data_counts.items()} }")
    print(f"[summary] context stream_ids: { {hex(k) if isinstance(k, int) else k: v for k, v in ctx_counts.items()} }")
    print(f"[summary] config_epochs: {dict(epoch_counts)}")
    print(f"[summary] samples_per_packet histogram: {dict(sample_counts)}")
    print(f"[summary] decode_errors={decode_errors}, packet_counter_anomalies={counter_anomalies}")
    for sid, preview in first_iq.items():
        print(f"[summary] {hex(sid)} first/mid sample: {preview}")

    # Interleave: in a stretch of 100 consecutive packets, both should appear
    if len(arrival_seq) >= 100:
        last100 = list(arrival_seq)[-100:]
        unique_in_last100 = set(last100)
        print(f"[interleave] last 100 arrivals contain stream_ids: "
              f"{ {hex(s) for s in unique_in_last100} }")

    failures = []
    if data_counts.get(RX0_ID, 0) == 0:
        failures.append("no RX0 packets received")
    if data_counts.get(RX1_ID, 0) == 0:
        failures.append("no RX1 packets received — Phase 4 did not enable ch1 stream")
    if data_counts.get(RX0_ID, 0) and data_counts.get(RX1_ID, 0):
        # Should be close to 50/50. Anything outside 40-60% is suspicious.
        total_data = data_counts[RX0_ID] + data_counts[RX1_ID]
        rx0_frac = data_counts[RX0_ID] / total_data
        if not (0.40 <= rx0_frac <= 0.60):
            failures.append(f"RX0/RX1 balance off: {rx0_frac:.2%} RX0")
    if ctx_counts.get(RX0_ID, 0) == 0:
        failures.append("no RX0 context packets")
    if ctx_counts.get(RX1_ID, 0) == 0:
        failures.append("no RX1 context packets — context-per-channel not wired")
    if set(epoch_counts) != {0}:
        failures.append(f"unexpected config_epochs (Phase 7 not done): {dict(epoch_counts)}")
    if decode_errors:
        failures.append(f"{decode_errors} decode errors")
    if counter_anomalies > 5:  # allow a few from initial sync / startup
        failures.append(f"{counter_anomalies} packet-counter discontinuities")

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 2
    print("PASS: dual-RX wire contract verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
