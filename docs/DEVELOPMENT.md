# VITA49 Pluto - Development Guide

This guide covers the architecture, testing, and development workflows for the VITA49 Pluto project.

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────┐
│                    Pluto+ SDR                           │
│  ┌──────────────┐         ┌──────────────────────────┐  │
│  │   AD9361     │────IIO──│  vita49_streamer (C)     │  │
│  │  RF Frontend │         │  • Config thread (4990)  │  │
│  └──────────────┘         │  • Stream thread (4991)  │  │
│                           └──────────┬───────────────┘  │
└────────────────────────────────────┼──────────────────┘
                                      │ UDP/Ethernet
                                      ▼
                    ┌────────────────────────────────┐
                    │       Network / Internet       │
                    └────────┬───────────────────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
    ┌──────────────┐  ┌──────────────┐  ┌────────────┐
    │ Config       │  │ Plotter      │  │ Custom     │
    │ Client       │  │ Receiver     │  │ Receiver   │
    │ (Python)     │  │ (Python)     │  │ (Python)   │
    └──────────────┘  └──────────────┘  └────────────┘
```

### C Streamer Architecture

The `pluto_vita49_streamer.c` implementation uses a multi-threaded architecture:

**Main Thread:**
- Initializes libiio context
- Creates control and streaming threads
- Handles graceful shutdown

**Control Thread (Port 4990):**
- Listens for VITA49 Context packets
- Decodes configuration (frequency, sample rate, gain)
- Updates SDR parameters via libiio
- Maintains subscriber list (IP addresses to stream to)
- Thread-safe via mutex locks

**Streaming Thread (Port 4991):**
- Reads IQ samples from AD9361 via libiio
- Encodes samples into VITA49 Data packets
- Sends UDP packets to all subscribers
- Periodically sends Context packets
- Thread-safe buffer management

**Thread Synchronization:**
```c
pthread_mutex_t config_mutex;  // Protects SDR configuration
pthread_mutex_t subscriber_mutex;  // Protects subscriber list
```

---

## VITA49 Packet Format

### Data Packet Structure

```
Offset  Size    Field               Description
------  ----    -----               -----------
0       4       Header              Packet type, flags, size
4       4       Stream ID           Channel identifier
8       4       Integer Timestamp   UTC seconds since epoch
12      8       Fractional Timestamp Picoseconds
20      N*4     Payload             int16 I/Q pairs (big-endian)
20+N*4  4       Trailer             Valid data indicator
```

**Header Breakdown (32 bits):**
```
[31:28] Packet Type (0x0 = Data, 0x4 = Context)
[27:24] Flags (C, T, TSI, TSF indicators)
[23:20] Reserved
[19:16] Packet count (rolling counter)
[15:0]  Packet size in 32-bit words
```

**Payload Format:**
- Samples are int16 I/Q pairs
- Big-endian byte order (network order)
- I sample first, then Q sample
- Typical packet: 360 samples = 720 int16 values = 1440 bytes

### Context Packet Structure

```
Offset  Size    Field                   Description
------  ----    -----                   -----------
0       4       Header                  Packet type 0x4
4       4       Stream ID               Channel identifier
8       4       Integer Timestamp       UTC seconds
12      8       Fractional Timestamp    Picoseconds
20      4       Context Indicator       Which fields are present
24      8       Sample Rate             Hz (double)
32      8       RF Reference Frequency  Hz (double)
40      8       RF Bandwidth            Hz (double)
48      4       Gain                    dB (float)
```

**Context Packet Usage:**
- Sent every 100 data packets
- Sent immediately when config changes
- Allows receivers to know stream parameters

---

## Key Bugfixes and Improvements

### Bugfix: Context Packet Stream ID Mismatch

**Problem:** Context packets were being sent with stream ID 0, while data packets used channel-specific stream IDs (0x00000001, 0x00000002, etc.). This caused receivers to not associate context with data.

**Solution:** Context packets now use the same stream ID as their corresponding data packets.

**File:** `vita49_packets.py`

**Before:**
```python
def encode(self):
    # ... context packet encoding
    stream_id = 0  # Wrong - doesn't match data packets
```

**After:**
```python
def encode(self, stream_id=0x00000001):
    # ... context packet encoding
    # stream_id now matches the channel
```

**Impact:**
- Receivers can now properly decode context packets
- Sample rate, frequency, and gain updates work correctly
- Multi-channel streaming context is per-channel

**See:** `BUGFIX_CONTEXT_PACKETS.md` in docs/archive/ for detailed analysis

---

## Testing Strategy

### Test Hierarchy

```
tests/
├── test_vita49.py              # Unit tests (VITA49 packet encode/decode)
├── test_pluto_config.py        # Unit tests (Config client)
├── test_streaming_simple.py    # Integration (Simple streaming)
└── e2e/                        # End-to-end tests
    ├── test_full_pipeline.py           # Complete pipeline
    ├── test_receive_from_pluto.py      # Pluto reception only
    ├── test_vita49_restreamer.py       # VITA49 re-streaming
    └── test_plotting_receiver.py       # Real-time visualization
```

### Running Tests

**All Tests:**
```bash
pytest tests/ -v
```

**Unit Tests Only:**
```bash
pytest tests/test_vita49.py -v
pytest tests/test_pluto_config.py -v
```

**Integration Tests:**
```bash
pytest tests/test_streaming_simple.py -v
```

**End-to-End Tests (requires Pluto hardware):**
```bash
# Full pipeline
python tests/e2e/test_full_pipeline.py --pluto-uri ip:192.168.2.1

# Step by step
python tests/e2e/test_receive_from_pluto.py --uri ip:192.168.2.1
python tests/e2e/test_vita49_restreamer.py --pluto-uri ip:192.168.2.1
python tests/e2e/test_plotting_receiver.py --port 4991
```

---

## End-to-End Test Suite

### Test 1: Basic Pluto Reception

**Purpose:** Verify connectivity to Pluto and IQ sample reception

**Command:**
```bash
python tests/e2e/test_receive_from_pluto.py --uri ip:192.168.2.1 --freq 2.4e9
```

**Expected Output:**
```
INFO - Connecting to Pluto+ at ip:192.168.2.1
INFO - Connected successfully!
INFO - Sample Rate: 30.0 MSPS
INFO - RX LO: 2.400 GHz
INFO - Received 100 buffers, 1638400 samples, 30.0 MSPS
```

**What it Tests:**
- pyadi-iio connectivity
- Pluto hardware functionality
- Sample streaming from AD9361

### Test 2: VITA49 Re-Streamer

**Purpose:** Verify VITA49 packet encoding and UDP streaming

**Command:**
```bash
python tests/e2e/test_vita49_restreamer.py --pluto-uri ip:192.168.2.1 --dest 127.0.0.1
```

**Expected Output:**
```
============================================================
VITA49 Re-Streamer Running
============================================================
  Pluto+ URI: ip:192.168.2.1
  Frequency: 2.400 GHz
  Sample Rate: 30.0 MSPS
  VITA49 Destination: 127.0.0.1:4991
============================================================

[Stats] SDR: 100 buffers, 30.0 MSPS | VITA49: 4551 packets, 23.45 Mbps
```

**What it Tests:**
- VITA49 packet encoding
- UDP transmission
- Context packet generation
- Packet rate calculations

### Test 3: Plotting Receiver

**Purpose:** Verify VITA49 decoding and real-time visualization

**Command:**
```bash
# In one terminal, run restreamer
python tests/e2e/test_vita49_restreamer.py --pluto-uri ip:192.168.2.1

# In another terminal, run plotter
python tests/e2e/test_plotting_receiver.py --port 4991
```

**Expected Behavior:**
- Matplotlib window opens with 4 subplots
- Real-time updates at ~20 Hz
- Time domain, FFT, waterfall, and stats

**What it Tests:**
- VITA49 packet decoding
- UDP reception
- Sample reconstruction
- Real-time processing

### Test 4: Full Pipeline

**Purpose:** Automated end-to-end test

**Command:**
```bash
python tests/e2e/test_full_pipeline.py --pluto-uri ip:192.168.2.1 --freq 2.4e9
```

**What it Does:**
1. Connects to Pluto
2. Starts VITA49 streaming
3. Opens plotting receiver
4. Runs until you close the window

**Use Cases:**
- Quick verification after changes
- Demo for users
- Signal analysis

---

## Development Workflows

### Adding a New Feature

1. **Create a branch:**
   ```bash
   git checkout -b feature/my-new-feature
   ```

2. **Implement the feature:**
   - Update source files
   - Add tests
   - Update documentation

3. **Test locally:**
   ```bash
   # Unit tests
   pytest tests/test_vita49.py -v

   # E2E test
   python tests/e2e/test_full_pipeline.py
   ```

4. **Commit and push:**
   ```bash
   git add .
   git commit -m "Add new feature: description"
   git push origin feature/my-new-feature
   ```

5. **Create pull request**

### Fixing a Bug

1. **Reproduce the bug:**
   - Create a minimal test case
   - Document expected vs actual behavior

2. **Write a failing test:**
   ```python
   def test_bugfix_issue_123():
       # Test that should fail with the bug
       result = buggy_function()
       assert result == expected  # Fails before fix
   ```

3. **Fix the bug:**
   - Update source code
   - Verify test now passes

4. **Document the fix:**
   - Add comments explaining why
   - Update CHANGELOG.md

### Updating Documentation

Documentation follows the structure:
```
docs/
├── BUILD.md         # Building and compilation
├── USAGE.md         # Usage and deployment
├── DEVELOPMENT.md   # This file - architecture and testing
└── ARCHITECTURE.md  # Detailed technical specs
```

**When to update:**
- New features → All relevant docs
- Bug fixes → DEVELOPMENT.md if architectural
- Configuration changes → USAGE.md
- Build changes → BUILD.md

---

## Code Style Guidelines

### Python

Follow PEP 8:
```python
# Good
def process_samples(self, samples):
    """Process IQ samples and return power estimate."""
    power = np.mean(np.abs(samples)**2)
    return 10 * np.log10(power)

# Bad
def ProcessSamples(samples):  # CamelCase for function names
    power=np.mean(np.abs(samples)**2)  # No spaces around operators
    return 10*np.log10(power)
```

**Naming Conventions:**
- Functions: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_CASE`
- Private: `_leading_underscore`

### C

Follow Linux kernel style:
```c
/* Good */
static void process_samples(int16_t *samples, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        // Process sample
    }
}

/* Bad */
static void ProcessSamples(int16_t* samples,size_t count){  // No space before brace
    for(size_t i=0;i<count;i++){  // No spaces
        // Process sample
    }
}
```

**Conventions:**
- Indent: 4 spaces (or tabs if project uses tabs)
- Braces: K&R style
- Functions: `snake_case`
- Structs: `snake_case_t`

---

## Performance Considerations

### Pluto (C Streamer)

**CPU Usage:**
- 20-30% at 30 MSPS
- Scales linearly with sample rate
- Mostly in IIO buffer management and UDP transmission

**Memory:**
- ~2 MB total
- Minimal heap allocations
- Stack-based buffers where possible

**Optimization Opportunities:**
- Use larger IIO buffers (reduces context switches)
- Batch UDP sends (reduces syscall overhead)
- SIMD for sample packing (not implemented yet)

### Receiver (Python)

**CPU Usage:**
- ~30-40% at 30 MSPS for plotting
- Mostly in matplotlib rendering
- numpy operations are efficient

**Memory:**
- ~50-100 MB for plotting with history
- Grows with waterfall line count

**Optimization:**
- Reduce plot update rate (`--update-rate 100`)
- Smaller FFT size (`--fft-size 512`)
- Limit waterfall history (`--waterfall-lines 50`)

---

## Known Issues

### Issue: Simulation Mode Connection Timeout

**File:** `NOTES.md` (archived)

**Problem:**
When running `signal_processing_harness.py` in simulation mode, it doesn't receive any packets from `vita49_stream_server.py` also in simulation mode.

**Expected:**
```
Stats: 0 pkts, 0 samples, 0 detections, 0.0 ms/block
```

**Cause:**
The server and client are both trying to simulate, but not actually sending/receiving UDP packets to each other.

**Solution:**
This is expected behavior. Simulation mode is for testing without hardware, not for inter-process communication. For testing server-client communication:
```bash
pytest tests/test_vita49.py -k "test_server_client_communication"
```

### Issue: Windows Plotting Freeze

**Problem:**
On Windows, the plotting receiver sometimes freezes when updating too fast.

**Cause:**
Matplotlib backend issue on Windows with tight update loops.

**Workaround:**
```bash
python tests/e2e/test_plotting_receiver.py --update-rate 100
```

This slows updates to 10 Hz instead of 20 Hz.

---

## Debugging Tips

### C Streamer Debug Build

```bash
# Edit Makefile
CFLAGS = -Wall -Wextra -g -O0 -std=gnu99 -DDEBUG

# Rebuild
make clean && make cross

# Deploy and run with logging
ssh root@pluto.local './vita49_streamer 2>&1 | tee streamer.log'
```

### Python Debugging

```bash
# Enable verbose logging
python tests/e2e/test_vita49_restreamer.py --log-level DEBUG

# Or in code
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Network Debugging

```bash
# Capture VITA49 packets
sudo tcpdump -i any -w vita49.pcap port 4991

# Analyze in Wireshark
wireshark vita49.pcap
```

### Pluto Debugging

```bash
# Check IIO devices
ssh root@pluto.local
iio_info -s

# Monitor libiio
ssh root@pluto.local
LD_LIBRARY_PATH=/usr/lib iio_readdev -s 16384 cf-ad9361-lpc
```

---

## Contributing

### Pull Request Process

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Update documentation
6. Submit PR with clear description

### Code Review Checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Code follows style guidelines
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
- [ ] Commit messages are clear

---

## Additional Resources

- [VITA 49.0 Specification](https://www.vita.com) - Official standard
- [libiio Documentation](https://wiki.analog.com/resources/tools-software/linux-software/libiio) - SDR interface
- [AD9361 Documentation](https://www.analog.com/en/products/ad9361.html) - RF transceiver
- [BUILD.md](BUILD.md) - Build instructions
- [USAGE.md](USAGE.md) - Usage guide

---

## Archived Documents

Historical notes and bugfix details are in `docs/archive/`:
- `bugfix-context-packets.md` - Detailed analysis of stream ID mismatch fix
- `development-notes.md` - Early development observations
