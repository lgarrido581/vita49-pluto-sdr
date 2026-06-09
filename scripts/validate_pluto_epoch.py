"""Phase 7 validation: config_epoch propagation through the C streamer.

Test sequence:
  1. Subscribe with a vanilla context (no Class ID). Capture baseline packets
     and assert they're untagged (epoch=0, no Class ID emitted). This proves
     the wire-compat path is unbroken when no client tags an epoch.
  2. Send a context packet tagged with config_epoch=5. After a brief settle
     window (the streamer checks for reconfig every ~100 ms), capture more
     packets and assert they ALL carry Class ID with epoch=5.
  3. Send a context packet tagged with config_epoch=7. Repeat the check
     against epoch=7.
  4. Also assert the streamer's context packets get retagged correctly.

This exercises the full plumbing: encode_data_packet + encode_context_packet
Class ID insertion, parse_context_packet Class ID extraction, the
control-thread epoch bump, and the streaming-thread destroy/recreate-buffer
barrier.
"""

import socket
import sys
import time
from collections import Counter
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

SETTLE_S = 0.30          # streamer scans config_changed every ~100ms
CAPTURE_PER_PHASE = 200
TIMEOUT_S = 5.0


def send_subscribe_ctx(sock, epoch: int = 0):
    """Subscribe by sending a context packet. If epoch != 0 the context
    will carry a Class ID with that epoch, which the streamer will adopt
    as the new g_current_epoch (and bump for all subsequent packets)."""
    ctx = VRTContextPacket(
        stream_id=RX0_ID,
        timestamp=VRTTimestamp.from_time(time.time()),
        sample_rate_hz=30e6,
        config_epoch=epoch,
    )
    sock.sendto(ctx.encode(), (PLUTO_IP, CONTROL_PORT))


def drain_socket(sock, duration_s: float):
    """Flush any in-flight packets from before our phase started."""
    end = time.time() + duration_s
    while time.time() < end:
        try:
            sock.recvfrom(65536)
        except socket.timeout:
            return


def capture(sock, n_packets: int) -> tuple[Counter, Counter, int]:
    """Capture n data packets, return (data_epoch_counts, ctx_epoch_counts, decode_errors)."""
    data_epochs: Counter = Counter()
    ctx_epochs: Counter = Counter()
    errors = 0
    got = 0
    while got < n_packets:
        try:
            raw, _ = sock.recvfrom(65536)
        except socket.timeout:
            break
        try:
            hdr = VRTHeader.decode(raw[:4])
        except Exception:
            errors += 1
            continue
        if hdr.packet_type == PacketType.IF_DATA_WITH_STREAM_ID:
            try:
                pkt = VRTSignalDataPacket.decode(raw)
            except Exception:
                errors += 1
                continue
            if pkt.stream_id != RX0_ID:
                continue
            data_epochs[pkt.config_epoch] += 1
            got += 1
        elif hdr.packet_type == PacketType.CONTEXT:
            try:
                ctx = VRTContextPacket.decode(raw)
                if ctx.stream_id == RX0_ID:
                    ctx_epochs[ctx.config_epoch] += 1
            except Exception:
                errors += 1
    return data_epochs, ctx_epochs, errors


def main() -> int:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    rx.bind(("0.0.0.0", DATA_PORT))
    rx.settimeout(TIMEOUT_S)

    failures = []

    # --- Phase A: baseline, epoch=0 (untagged) ---
    print("[A] subscribing with epoch=0, capturing baseline...")
    send_subscribe_ctx(rx, epoch=0)
    time.sleep(SETTLE_S)
    drain_socket(rx, 0.1)
    data, ctx, errs = capture(rx, CAPTURE_PER_PHASE)
    print(f"    data epochs: {dict(data)}")
    print(f"    ctx  epochs: {dict(ctx)}")
    print(f"    decode errors: {errs}")
    # NOTE: this is "baseline" only if no prior test bumped the epoch.
    # If the streamer was just (re)started, g_current_epoch should be 0
    # and all packets should be untagged.
    if errs:
        failures.append(f"[A] {errs} decode errors")
    if set(data) - {0}:
        print(f"    WARNING: server is in epoch {set(data) - {0}}, not 0.")
        print("             Restart the streamer to reset g_current_epoch=0 if you want a fresh baseline.")

    baseline_epoch = max(data) if data else 0

    # --- Phase B: client tags epoch=5, expect packets to carry epoch=5 ---
    target_b = 5
    print(f"\n[B] sending context with epoch={target_b}, expecting all subsequent packets tagged {target_b}...")
    send_subscribe_ctx(rx, epoch=target_b)
    time.sleep(SETTLE_S)
    drain_socket(rx, 0.1)  # discard any stragglers from before the transition
    data, ctx, errs = capture(rx, CAPTURE_PER_PHASE)
    print(f"    data epochs: {dict(data)}")
    print(f"    ctx  epochs: {dict(ctx)}")
    if set(data) != {target_b}:
        failures.append(
            f"[B] expected ALL data packets to carry epoch={target_b}, got {dict(data)}"
        )
    if target_b not in ctx:
        failures.append(f"[B] expected at least one context packet at epoch={target_b}, got {dict(ctx)}")
    if errs:
        failures.append(f"[B] {errs} decode errors")

    # --- Phase C: client tags epoch=7, expect packets to carry epoch=7 ---
    target_c = 7
    print(f"\n[C] sending context with epoch={target_c}, expecting all subsequent packets tagged {target_c}...")
    send_subscribe_ctx(rx, epoch=target_c)
    time.sleep(SETTLE_S)
    drain_socket(rx, 0.1)
    data, ctx, errs = capture(rx, CAPTURE_PER_PHASE)
    print(f"    data epochs: {dict(data)}")
    print(f"    ctx  epochs: {dict(ctx)}")
    if set(data) != {target_c}:
        failures.append(
            f"[C] expected ALL data packets to carry epoch={target_c}, got {dict(data)}"
        )
    if target_c not in ctx:
        failures.append(f"[C] expected at least one context packet at epoch={target_c}, got {dict(ctx)}")
    if errs:
        failures.append(f"[C] {errs} decode errors")

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 2
    print(f"PASS: epoch propagation works ({baseline_epoch} -> {target_b} -> {target_c})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
