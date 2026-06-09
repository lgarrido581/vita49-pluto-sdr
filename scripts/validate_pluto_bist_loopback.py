"""BIST (digital) loopback validation: end-to-end TX -> RX inside the AD9361.

The AD9361's bist_loopback=1 setting routes the TX DAC output directly
to the RX ADC input, bypassing the RF front-end entirely. With:
  - the streamer running TX0 in replay mode (1 MHz CW tone), AND
  - the streamer ALSO running RX0 (default), AND
  - bist_loopback=1 set on ad9361-phy

the RX stream should carry the TX tone at +1 MHz baseband.

This script subscribes to the RX stream, captures a chunk of IQ, FFTs
it, and asserts the spectral peak sits at the expected bin (within a
small tolerance for FFT bin resolution).

Prereq commands (run from your SSH session BEFORE this script):
  1. Start the streamer with RX0 + TX0 + replay:
       /root/vita49_streamer --tx-channels 0 --tx-freq 915000000 \\
           --tx-replay /tmp/tone_1mhz_30msps.iq16le --tx-loop
  2. Enable BIST loopback in another shell on the Pluto:
       iio_attr -d ad9361-phy bist_loopback 1
     (Disable when done: iio_attr -d ad9361-phy bist_loopback 0)
"""

import socket
import sys
import time
from pathlib import Path

import numpy as np

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

SAMPLE_RATE_HZ = 30e6
EXPECTED_TONE_HZ = 1e6
CAPTURE_PACKETS = 300        # ~108k IQ samples; FFT bin width ~280 Hz
TIMEOUT_S = 5.0
PEAK_TOLERANCE_HZ = 20e3     # generous: bin resolution + AD9361 quirks
MIN_PEAK_DB_OVER_MEDIAN = 30 # tone must be >= 30 dB over the noise floor


def subscribe(sock):
    ctx = VRTContextPacket(
        stream_id=RX0_ID,
        timestamp=VRTTimestamp.from_time(time.time()),
        sample_rate_hz=SAMPLE_RATE_HZ,
    )
    sock.sendto(ctx.encode(), (PLUTO_IP, CONTROL_PORT))


def main() -> int:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    rx.bind(("0.0.0.0", DATA_PORT))
    rx.settimeout(TIMEOUT_S)
    subscribe(rx)
    print(f"[subscribe] context sent, capturing {CAPTURE_PACKETS} RX packets")

    iq_chunks = []
    rx0 = 0
    other_streams = 0
    while rx0 < CAPTURE_PACKETS:
        try:
            raw, _ = rx.recvfrom(65536)
        except socket.timeout:
            print(f"[capture] timeout after {rx0} packets")
            break
        try:
            header = VRTHeader.decode(raw[:4])
        except Exception:
            continue
        if header.packet_type != PacketType.IF_DATA_WITH_STREAM_ID:
            continue
        try:
            pkt = VRTSignalDataPacket.decode(raw)
        except Exception:
            continue
        if pkt.stream_id != RX0_ID:
            other_streams += 1
            continue
        iq_chunks.append(pkt.to_iq_samples())
        rx0 += 1

    if rx0 == 0:
        print("FAIL: no RX0 packets received. Is the streamer running with RX0 enabled?")
        return 1

    iq = np.concatenate(iq_chunks).astype(np.complex64)
    print(f"[capture] got {rx0} RX0 packets, {len(iq)} samples "
          f"({1000 * len(iq) / SAMPLE_RATE_HZ:.1f} ms)")
    if other_streams:
        print(f"[capture] (also dropped {other_streams} non-RX0 packets)")

    # Window + FFT. Use full length, complex (one-sided)
    window = np.hanning(len(iq))
    spec = np.fft.fftshift(np.fft.fft(iq * window))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(iq), d=1 / SAMPLE_RATE_HZ))
    mag_db = 20 * np.log10(np.abs(spec) + 1e-12)

    peak_idx = int(np.argmax(mag_db))
    peak_freq_hz = float(freqs[peak_idx])
    peak_db = float(mag_db[peak_idx])
    median_db = float(np.median(mag_db))
    bin_hz = SAMPLE_RATE_HZ / len(iq)

    print()
    print(f"[fft] N={len(iq)}, bin_width={bin_hz:.1f} Hz")
    print(f"[fft] peak: {peak_freq_hz/1e3:+8.2f} kHz   {peak_db:7.2f} dB")
    print(f"[fft] median noise floor: {median_db:.2f} dB")
    print(f"[fft] peak-to-median: {peak_db - median_db:.1f} dB")

    # Find the top 3 strongest bins above 1% of peak amplitude — useful for diagnosis
    # if the test fails (we'll see what IS there).
    top3 = np.argsort(mag_db)[-3:][::-1]
    print(f"[fft] top 3 bins:")
    for idx in top3:
        print(f"        {freqs[idx]/1e3:+8.2f} kHz   {mag_db[idx]:7.2f} dB")

    failures = []
    if abs(peak_freq_hz - EXPECTED_TONE_HZ) > PEAK_TOLERANCE_HZ:
        failures.append(
            f"peak at {peak_freq_hz/1e3:+.2f} kHz, expected +{EXPECTED_TONE_HZ/1e3:.1f} kHz "
            f"(±{PEAK_TOLERANCE_HZ/1e3:.1f} kHz). "
            f"BIST loopback enabled? Try: iio_attr -d ad9361-phy bist_loopback 1"
        )
    if peak_db - median_db < MIN_PEAK_DB_OVER_MEDIAN:
        failures.append(
            f"peak only {peak_db - median_db:.1f} dB above noise floor "
            f"(need >= {MIN_PEAK_DB_OVER_MEDIAN} dB). "
            "Either no tone reaching RX, or BIST loopback off."
        )

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 2
    print(f"PASS: BIST loopback shows the TX tone at {peak_freq_hz/1e3:+.2f} kHz, "
          f"{peak_db - median_db:.1f} dB above noise floor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
