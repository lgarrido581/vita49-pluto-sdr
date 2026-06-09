"""Generate a raw int16 I/Q test file for --tx-replay.

Format (matches pluto_vita49_streamer.c's expected --tx-replay input):
    Interleaved I,Q,I,Q,...
    Each sample = signed 16-bit, little-endian (native on Pluto+ ARM)
    No header

Default produces 8 TX-buffer-aligned blocks of a 1 MHz CW tone at 30
MSPS, sized so a looping replay hits exact buffer boundaries (no zero-
padding glitches at the loop seam).
"""

import argparse
import hashlib
import sys

import numpy as np

TX_BUFFER_SAMPLES = 4096  # matches DEFAULT_TX_BUFFER_SIZE in the C streamer


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default="tone_1mhz_30msps.iq16le",
                    help="Output file path (default: %(default)s)")
    ap.add_argument("--sample-rate-hz", type=float, default=30e6,
                    help="Sample rate in Hz (default: %(default)g)")
    ap.add_argument("--tone-hz", type=float, default=1e6,
                    help="Complex tone frequency (default: %(default)g)")
    ap.add_argument("--amplitude", type=float, default=0.7,
                    help="Tone amplitude in [-1, 1] (default: %(default)s)")
    ap.add_argument("--buffers", type=int, default=8,
                    help="Number of TX buffers (4096 samples each) to generate. "
                         "Larger = longer file. (default: %(default)s)")
    args = ap.parse_args()

    n_samples = args.buffers * TX_BUFFER_SAMPLES
    duration_s = n_samples / args.sample_rate_hz
    t = np.arange(n_samples) / args.sample_rate_hz
    iq = args.amplitude * np.exp(1j * 2 * np.pi * args.tone_hz * t)

    # Scale to int16 range. The C streamer scales RX samples by 2**14
    # via the Python packets.py convention; matching here keeps amplitudes
    # symmetric between RX and TX paths.
    scale = 2 ** 14
    i_int16 = np.clip(np.real(iq) * scale, -32768, 32767).astype("<i2")  # little-endian
    q_int16 = np.clip(np.imag(iq) * scale, -32768, 32767).astype("<i2")

    interleaved = np.empty(n_samples * 2, dtype="<i2")
    interleaved[0::2] = i_int16
    interleaved[1::2] = q_int16

    raw = interleaved.tobytes()
    with open(args.output, "wb") as f:
        f.write(raw)

    sha = hashlib.sha256(raw).hexdigest()
    print(f"Wrote {args.output}")
    print(f"  samples per channel: {n_samples}  ({args.buffers} buffers × {TX_BUFFER_SAMPLES})")
    print(f"  bytes: {len(raw)}  ({len(raw) / 1024:.1f} KiB)")
    print(f"  duration: {duration_s*1000:.3f} ms  @ {args.sample_rate_hz/1e6:.1f} MSPS")
    print(f"  tone: {args.tone_hz/1e6:.3f} MHz, amplitude {args.amplitude}")
    print(f"  sha256: {sha}")
    print()
    print("Deploy to Pluto:")
    print(f"  scp {args.output} root@192.168.0.17:/tmp/")
    print()
    print("Run on Pluto:")
    print(f"  /root/vita49_streamer --tx-channels 0 --tx-freq 915000000 \\")
    print(f"      --tx-replay /tmp/{args.output} --tx-loop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
