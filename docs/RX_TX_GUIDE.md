# RX / TX Quickstart

How to drive the `vita49_streamer` binary on an AD9361-based SDR (Pluto+, ADRV9361, etc.) for the new dual-RX and bidirectional features. Assumes the radio is reachable from the host that runs `libiio`.

## Wire contract at a glance

| Resource | Value | Direction | Purpose |
|---|---|---|---|
| UDP **4990** | inbound | host → SDR | VITA-49 Context packets (subscribe + reconfig) |
| UDP **4991** | outbound | SDR → host | VITA-49 IF Data packets (RX samples) |
| UDP **4992** | inbound | host → SDR | VITA-49 IF Data packets (TX samples) |
| Stream ID `0x0100000N` | — | RX | N = 0 or 1 (channel index) |
| Stream ID `0x0200000N` | — | TX | N = 0 or 1 (channel index) |

A single UDP datagram carries one VITA-49 packet. Both RX channels multiplex on port 4991 distinguished by stream_id.

## Build & deploy

```bash
# From the repo root (uses Docker for the ARM cross-compile):
./scripts/build-with-docker.sh        # or build-with-docker.bat on Windows
scp vita49_streamer root@<pluto-ip>:/root/vita49_streamer
ssh root@<pluto-ip> "chmod +x /root/vita49_streamer"
```

Verify the binary on the SDR has the new features (the literal string `epoch=` is in the Phase-7 debug printf):

```bash
ssh root@<pluto-ip> "strings /root/vita49_streamer | grep -c epoch="
# Expect: >= 1
```

## RX operations

### Default — single channel (backward compatible)

```bash
ssh root@<pluto-ip> "/root/vita49_streamer"
```

Streams RX0 only with stream_id `0x01000000`. Bit-identical to the pre-feature wire format. Any consumer that was working before will keep working.

### Dual RX — both channels

```bash
ssh root@<pluto-ip> "/root/vita49_streamer --rx-channels 0,1"
```

Emits **two interleaved streams** on UDP 4991:
- `0x01000000` from RX0
- `0x01000001` from RX1

Both streams share the same LO and sample rate (AD9361 hardware constraint). Consumer-side filter by stream_id to demultiplex.

### Single RX1 only

```bash
ssh root@<pluto-ip> "/root/vita49_streamer --rx-channels 1"
```

Emits only `0x01000001`.

### Subscribing as a consumer

The SDR maintains a subscriber list. A host gets added automatically when it sends any VITA-49 Context packet to UDP 4990 — the source IP of that packet becomes the subscription target on UDP 4991.

The minimum-effort Python subscribe:

```python
import socket, time
from vita49.packets import VRTContextPacket, VRTTimestamp

rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.bind(("0.0.0.0", 4991))
# Send a no-op context (matches current SDR config -> no reconfig triggered)
ctx = VRTContextPacket(
    stream_id=0x01000000,
    timestamp=VRTTimestamp.from_time(time.time()),
    sample_rate_hz=30e6,  # must match current SDR rate, else triggers reconfig
)
rx.sendto(ctx.encode(), ("<pluto-ip>", 4990))
# Now read RX packets from rx
```

Working example: `scripts/validate_pluto_dual_rx.py`.

### Reconfiguring the SDR

Send a Context packet to UDP 4990 with the fields you want changed. The SDR diffs against current state and only reapplies if something actually changed:

```python
ctx = VRTContextPacket(
    stream_id=0x01000000,
    timestamp=VRTTimestamp.from_time(time.time()),
    rf_reference_frequency_hz=915e6,   # new center freq
    sample_rate_hz=20e6,                # new SR (shared by RX+TX)
    gain_db=30.0,                       # new RX gain
)
control.sendto(ctx.encode(), ("<pluto-ip>", 4990))
```

The SDR destroys + recreates its libiio buffer (~10–25 ms gap), then resumes streaming with the new config.

### config_epoch — knowing your samples are post-reconfig

A naive reconfig has a race: samples taken under the OLD config can still be in flight from the libiio buffer / UDP queue when the new config is applied. The `config_epoch` tag solves this — every reconfig gets a monotonic generation number, and every IF Data packet carries it via a VRT Class ID.

**Tag a reconfig with an epoch:**

```python
ctx = VRTContextPacket(
    stream_id=0x01000000,
    timestamp=VRTTimestamp.from_time(time.time()),
    rf_reference_frequency_hz=915e6,
    config_epoch=42,           # NEW: monotonic generation tag
)
control.sendto(ctx.encode(), ("<pluto-ip>", 4990))
```

The SDR adopts epoch 42, flushes its RX buffer, and tags every subsequent packet with epoch 42.

**Consumer filter:**

```python
from vita49.packets import VRTSignalDataPacket
pkt = VRTSignalDataPacket.decode(raw_udp_bytes)
if pkt.config_epoch < 42:
    continue   # stale — taken under prior config, discard
```

Epoch `0` means "untagged" — wire-compat behavior. Consumers that don't care can ignore the field.

## TX operations

TX is **opt-in** — pass `--tx-channels` to enable it. RX continues to run alongside unless you stop it.

### Live mode — push IQ over UDP

```bash
ssh root@<pluto-ip> "/root/vita49_streamer \
    --tx-channels 0 \
    --tx-freq 915000000 \
    --tx-gain -10"
```

Listens on UDP 4992. From the host, encode IQ as a VITA-49 IF Data packet with stream_id `0x02000000` (TX0) or `0x02000001` (TX1):

```python
import socket, time, numpy as np
from vita49.packets import VRTSignalDataPacket, create_stream_id

tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Generate any IQ you want — here, a 1 MHz tone at 30 MSPS
t = np.arange(1024) / 30e6
iq = (0.7 * np.exp(1j * 2 * np.pi * 1e6 * t)).astype(np.complex64)

pkt = VRTSignalDataPacket.from_iq_samples(
    iq_samples=iq,
    stream_id=create_stream_id(channel=0, device_id=2),  # 0x02000000 = TX0
    sample_rate=30e6,
    timestamp=time.time(),
)
tx.sendto(pkt.encode(), ("<pluto-ip>", 4992))
```

Working example: `scripts/validate_pluto_tx.py`.

**TX caveats:**
- v1 supports **one TX channel at a time** (`--tx-channels 0` OR `--tx-channels 1`, not both).
- Sample rate is **shared with RX**. To change it for TX you must reconfigure RX too — there is one BBPLL on the AD9361.
- TX gain is attenuation: range −89.75 to 0 dB. `-10` ≈ moderate output. `0` is the strongest.
- TX LO (`--tx-freq`) is independent of RX LO.

### File replay mode — loop an IQ file

Useful for canned waveforms (radar pulses, calibration tones, captured signals):

**On the host**, generate or capture a raw int16 IQ file (interleaved I,Q, little-endian, no header). A helper is provided for tone generation:

```bash
python scripts/make_test_iq_file.py -o tone.iq16le
```

**Deploy to the SDR**:

```bash
scp tone.iq16le root@<pluto-ip>:/tmp/
```

**Run**:

```bash
ssh root@<pluto-ip> "/root/vita49_streamer \
    --tx-channels 0 \
    --tx-freq 915000000 \
    --tx-replay /tmp/tone.iq16le \
    --tx-loop"
```

- `--tx-replay <path>` reads from disk instead of UDP 4992.
- `--tx-loop` restarts from the beginning on EOF (otherwise the TX thread exits when the file ends).
- File format: raw `int16_t` interleaved I,Q, little-endian, no header.

The UDP TX path is bypassed when `--tx-replay` is set — they're mutually exclusive.

## Operational notes

### Health monitoring

The SDR prints stats every 5 s to stdout. Watch for:
- `Refill Fails`, `PushFails` — non-zero means libiio is dropping work. Investigate USB/CPU saturation.
- `Underflows` (RX), `Overflows` — timing-jitter warnings. Brief spikes during reconfig (~25 ms) are expected.
- `[TX] PushFails` non-zero — TX DAC isn't keeping up; reduce input rate.

### When RX is doing nothing useful

If you're doing pure TX, the RX thread still runs and competes for USB bandwidth. There's no `--no-rx` flag yet — TODO. For now, simply ignore the [Stats] RX counters when no consumer is subscribed.

### Stopping

`Ctrl+C` (or `killall vita49_streamer`) cleanly tears down all threads, flushes TX, and closes libiio cleanly.

### When to bump `config_epoch`

- Anytime you've changed center freq, sample rate, gain, or any other property where samples taken under the old config would be misleading.
- Anytime a downstream consumer needs to *prove* it's looking at samples from a known config (e.g., a dwell scheduler that records "freq X at epoch N").
- Pick any 16-bit monotonic counter. Don't reuse old values — consumers may filter by `>= N`.
- Epoch `0` is reserved for "not in use" — packets stay untagged.

## Validation scripts

The `scripts/` directory has live-Pluto test scripts you can adapt:

| Script | What it proves |
|---|---|
| `validate_pluto_wire_compat.py` | Default-mode RX0 emits the expected stream_id, untagged, no regressions |
| `validate_pluto_dual_rx.py` | `--rx-channels 0,1` emits both streams interleaved with the correct stream_ids |
| `validate_pluto_tx.py` | UDP TX path receives and pushes packets to the DAC |
| `validate_pluto_epoch.py` | Client-tagged epoch propagates through reconfig and is stamped on subsequent packets |
| `validate_pluto_bist_loopback.py` | (Optional, needs `iio_attr bist_loopback 1`) Internal digital loopback shows the TX tone in the RX spectrum |
| `make_test_iq_file.py` | Generate a raw int16 I/Q tone file for `--tx-replay` |

Each one expects the SDR at `192.168.0.17` by default — edit the constant at the top to point elsewhere.
