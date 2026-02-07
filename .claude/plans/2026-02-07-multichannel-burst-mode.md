# VITA49 Multi-Channel Burst Mode Implementation Plan

## Overview

Extend the VITA49 Pluto streamer to support:
1. **Single-channel burst mode** (>11 MSPS): Maximum throughput streaming where network capacity is the limiting factor
2. **Dual-channel burst mode**: Stream RX0+RX1 simultaneously with shared configuration
3. **Discontinuity tracking**: Proper VITA49 flags for sample loss when buffer overflow occurs

## User Requirements

**Operating Modes:**
- Single-channel continuous (≤11 MSPS) - EXISTING, maintain as-is
- Single-channel burst (>11 MSPS) - NEW
- Dual-channel burst (>5.5 MSPS each) - NEW

**Key Design Decisions:**
- **Burst strategy**: Software dropping - Pluto streams continuously, ARM sends packets as fast as possible, drops samples when network/buffer can't keep up
- **Dual-channel config**: Shared configuration (both channels use same frequency/gain/bandwidth)
- **Burst timing**: No artificial duty cycle - send as fast as possible, memory/network capacity determines limits
- **Buffer overflow**: When ring buffer fills, drop samples and set VITA49 sample_loss flags

## Architecture Overview

### Stream ID Strategy
- **RX0**: `stream_id = 0x01000001` (device=1, channel=1)
- **RX1**: `stream_id = 0x01000002` (device=1, channel=2)
- Both channels on same UDP port (4991) - receivers demultiplex by stream_id
- Alternating packet pattern: RX0, RX1, RX0, RX1... for lower latency

### Ring Buffer Design
- Existing single ring buffer for single-channel mode
- Add second ring buffer for dual-channel mode
- Each ring buffer tracks its own channel's data independently
- Shared sample pool (no malloc) remains efficient

### Burst Mode Operation

**Two Operating Modes:**

1. **Streaming Mode** (≤11 MSPS continuous):
   - DMA → Ring Buffer → Network (immediate transmission)
   - Low latency (~3-6ms)
   - Continuous packet flow

2. **Burst Mode** (>11 MSPS or user-requested):
   - **Accumulation Phase**: DMA → Burst Buffer (collect until full)
   - **Transmission Phase**: Burst Buffer → Network (rapid fire all packets)
   - **Sample Loss Indication**: Set flags when gaps occur between bursts
   - High latency (accumulation time) but maximum contiguous IQ samples

**Burst Buffer Architecture:**
- Large accumulation buffer: 10M samples (40 MB) per channel
- Trigger: Transmit when buffer reaches target fill level
- Burst transmission: Send all packets without delay between them
- Gap insertion: Natural gaps occur between bursts (marked with sample_loss flags)

## Implementation Plan

### Phase 1: C Streamer - Dual-Channel Hardware Support

**File**: [pluto_vita49_streamer.c](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c)

#### 1.1 Add Channel Mode Configuration

**Location**: After line 141 (global configuration structures)

Add channel mode enum and extend `sdr_config_t`:
```c
typedef enum {
    CHANNEL_MODE_SINGLE_RX0 = 0x00,
    CHANNEL_MODE_SINGLE_RX1 = 0x01,
    CHANNEL_MODE_DUAL = 0x02
} channel_mode_t;

// Add to sdr_config_t:
channel_mode_t channel_mode;  // Default: CHANNEL_MODE_SINGLE_RX0
```

#### 1.2 Enable RX1 Hardware Channels

**Location**: [pluto_vita49_streamer.c:1092-1097](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c#L1092-L1097) in `configure_sdr()`

Currently only enables voltage0/voltage1 (RX0). Add:
- Find voltage2/voltage3 channels (RX1)
- Enable channels based on `channel_mode`
- Disable/enable sequence: disable all → configure → re-enable selected

#### 1.3 Dual Ring Buffer Infrastructure

**Location**: After line 96 (global state)

Add:
```c
static lock_free_ring_buffer_t g_ring_buffer_rx0;  // RX0 or single-channel
static lock_free_ring_buffer_t g_ring_buffer_rx1;  // RX1 (dual-channel only)
static atomic_uint g_sequence_counter_rx0 = ATOMIC_VAR_INIT(0);
static atomic_uint g_sequence_counter_rx1 = ATOMIC_VAR_INIT(0);
```

Initialize both ring buffers in `main()`.

#### 1.4 Interleaved Buffer Parsing

**Location**: [pluto_vita49_streamer.c:825-860](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c#L825-L860) in `dma_reader_thread()`

Current code assumes 4-byte format (I, Q pairs). When dual-channel enabled:
- Buffer format: 8 bytes (RX0_I, RX0_Q, RX1_I, RX1_Q) interleaved
- De-interleave into two separate buffers
- Push to separate ring buffers (g_ring_buffer_rx0, g_ring_buffer_rx1)
- Track sequence numbers independently

#### 1.5 Stream ID Parameterization

**Location**: [pluto_vita49_streamer.c:608-666](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c#L608-L666)

Currently hardcoded at line 660:
```c
hdr->stream_id = htonl_custom(0x01000000);
```

Changes:
- Add `uint32_t stream_id` parameter to `encode_data_packet()`
- Add `uint32_t stream_id` parameter to `encode_context_packet()`
- Define constants:
  ```c
  #define STREAM_ID_RX0  0x01000001
  #define STREAM_ID_RX1  0x01000002
  ```

#### 1.6 Network Thread: Multi-Channel Transmission

**Location**: [pluto_vita49_streamer.c:992-1044](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c#L992-L1044) in `network_thread()`

Modify packet sending logic:
- Check `channel_mode` to determine single vs dual-channel
- Dual-channel: alternate packets (RX0, RX1, RX0, RX1...)
- Maintain separate `packet_count` per channel (4-bit counter, wraps at 16)
- Use existing batch sending with `sendmmsg()`

#### 1.7 Sample Loss Detection

**Location**: Throughout network thread and packet encoding

Track sequence numbers:
- Detect gaps in DMA sequence numbers when popping from ring buffer
- Set boolean `sample_loss` flag when gap detected
- Pass flag to `encode_data_packet()` to set trailer bit 24

Trailer format update:
```c
uint32_t trailer_val = 0x40000000;  // valid_data = 1
if (sample_loss) {
    trailer_val |= (1 << 24);  // Set sample_loss bit
}
```

Update context packets to include sample_loss in state_event field (bit 18).

### Phase 2: Configuration Protocol Extension

**Files**:
- [pluto_vita49_streamer.c](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c) - parsing
- [config_client.py](c:\git-repos\vita49-pluto\src\vita49\config_client.py) - encoding

#### 2.1 C Streamer: Parse Channel Mode

**Location**: [pluto_vita49_streamer.c:669-719](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c#L669-L719) in `parse_context_packet()`

Extend context packet CIF:
- Use CIF bit 16 for channel_mode field
- Parse 1-byte channel_mode value
- Update `g_sdr_config.channel_mode` under mutex

#### 2.2 Python: Encode Channel Mode

**Location**: [config_client.py](c:\git-repos\vita49-pluto\src\vita49\config_client.py)

Add to `encode_context()` method:
- Add `channel_mode` parameter (default: None)
- Set CIF bit 16 when channel_mode provided
- Encode as 32-bit field (1 byte value, 3 bytes padding)

Add CLI argument:
```python
parser.add_argument('--channels', '-c',
                   choices=['rx0', 'rx1', 'dual'],
                   default='rx0')

CHANNEL_MODE_MAP = {'rx0': 0x00, 'rx1': 0x01, 'dual': 0x02}
```

### Phase 3: Python Receiver - Multi-Stream Demultiplexing

**File**: [stream_server.py](c:\git-repos\vita49-pluto\src\vita49\stream_server.py)

#### 3.1 Per-Stream Buffering

**Location**: `VITA49StreamClient` class (lines 665-777)

Current implementation uses single sample buffer. Change to:
- `self.stream_buffers = {}` - dict of stream_id → deque
- `self.stream_stats = {}` - dict of stream_id → stats
- Initialize buffers dynamically when new stream_id seen

#### 3.2 Gap Detection

Track packet_count per stream (4-bit counter):
- Store `last_packet_count` per stream_id
- Detect when `expected_count != actual_count`
- Increment `gaps_detected` counter in stream stats
- Log warning with stream_id

Check trailer `sample_loss` flag:
- Parse trailer from packet
- Extract bit 24 (sample_loss indicator)
- Log when detected

#### 3.3 Stream Access API

Add method:
```python
def get_samples(self, stream_id, count):
    """Get samples from specific stream"""
    if stream_id not in self.stream_buffers:
        return np.array([], dtype=np.complex64)
    buffer = self.stream_buffers[stream_id]
    samples = [buffer.popleft() for _ in range(min(count, len(buffer)))]
    return np.array(samples, dtype=np.complex64)
```

Modify callback signature:
```python
# Old: on_samples(packet, iq_samples)
# New: on_samples(stream_id, packet, iq_samples)
```

### Phase 4: Web UI - Multi-Channel Visualization

**Files**:
- [web_server.py](c:\git-repos\vita49-pluto\src\vita49\web_server.py) - backend
- [App.jsx](c:\git-repos\vita49-pluto\src\vita49\web\src\App.jsx) - frontend coordination
- [SpectrumPlot.jsx](c:\git-repos\vita49-pluto\src\vita49\web\src\components\SpectrumPlot.jsx) - visualization

#### 4.1 Backend: Multi-Stream Handler

**Location**: `VITA49WebHandler` class in web_server.py

Change from single buffer to per-stream:
```python
self.stream_buffers = {}  # stream_id → deque
self.stream_metadata = {} # stream_id → metadata dict
self.stream_stats = {}    # stream_id → stats dict
```

Update callback:
```python
def _on_samples_received(self, stream_id, packet, samples):
    # Initialize stream if new
    if stream_id not in self.stream_buffers:
        self.stream_buffers[stream_id] = deque(maxlen=self.fft_size * 4)
        self.stream_stats[stream_id] = {
            'packets_received': 0,
            'samples_received': 0,
            'gaps_detected': 0
        }

    # Store samples and check for gaps
    for s in samples:
        self.stream_buffers[stream_id].append(s)

    if packet.trailer and packet.trailer.sample_loss:
        self.stream_stats[stream_id]['gaps_detected'] += 1
```

Broadcast multi-stream data:
```python
async def _process_and_broadcast_multistream(self):
    spectrum_data = {}
    for stream_id, buffer in self.stream_buffers.items():
        if len(buffer) < self.fft_size:
            continue

        # Compute FFT
        samples = np.array(buffer)[-self.fft_size:]
        spectrum = compute_spectrum(samples)

        spectrum_data[f"0x{stream_id:08X}"] = {
            'frequencies': freq_bins.tolist(),
            'spectrum': spectrum_db.tolist(),
            'channel': 'RX0' if stream_id & 0xFF == 1 else 'RX1',
            'gaps_detected': self.stream_stats[stream_id]['gaps_detected']
        }

    await self.manager.broadcast({
        'type': 'spectrum_multistream',
        'streams': spectrum_data,
        'timestamp': time.time()
    })
```

#### 4.2 Frontend: Dual-Channel Display

**Location**: SpectrumPlot.jsx

Handle multi-stream message type:
```javascript
if (data && data.streams) {
    const traces = Object.entries(data.streams).map(([stream_id, stream_data]) => ({
        x: stream_data.frequencies,
        y: stream_data.spectrum,
        name: `${stream_data.channel} (${stream_id})`,
        type: 'scatter',
        mode: 'lines',
        line: {
            width: 1,
            color: stream_data.channel === 'RX0' ? '#00ff00' : '#ff9900'
        }
    }));

    return <Plot data={traces} layout={{...}} />;
}
```

Add gap indicators in statistics component:
```javascript
<div className={stats.gaps_detected > 0 ? 'warning' : ''}>
    Gaps Detected: {stats.gaps_detected}
</div>
```

### Phase 5: Testing & Validation

## Testing Workflow Overview

The testing workflow follows a **Build → Deploy → Verify** pattern with increasing complexity:

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: BUILD                                               │
│ - Docker cross-compile for ARM                              │
│ - Run Python unit tests (no hardware)                       │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 2: DEPLOY                                              │
│ - Deploy to Pluto via SSH                                   │
│ - Start streamer in background                              │
│ - Verify process running                                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 3: INTEGRATION TESTS (Hardware Required)              │
│ - Test configuration protocol                               │
│ - Test single-channel modes (RX0, RX1)                      │
│ - Test dual-channel mode                                    │
│ - Test burst mode (high sample rates)                       │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 4: END-TO-END VALIDATION                              │
│ - Full pipeline: config → stream → receive → visualize     │
│ - Sample loss detection verification                        │
│ - Performance benchmarks                                    │
└─────────────────────────────────────────────────────────────┘
```

## Testing Tools & Commands

### Tool 1: Build & Unit Test
**Command**: `./scripts/test-build.sh`
**Purpose**: Cross-compile and run Python unit tests
**No Hardware Required**

```bash
#!/bin/bash
# Build with Docker
./scripts/build-with-docker.sh

# Run Python unit tests (no Pluto needed)
pytest tests/test_vita49.py -v
pytest tests/test_pluto_config.py -v

# NEW: Test dual-channel packet handling
pytest tests/test_dual_channel_packets.py -v
```

### Tool 2: Deploy & Start
**Command**: `./scripts/test-deploy.sh [mode]`
**Purpose**: Deploy and start streamer on Pluto
**Modes**: `single-rx0`, `single-rx1`, `dual`, `burst`

```bash
#!/bin/bash
# Deploy binary
make deploy-binary PLUTO_IP=${PLUTO_IP:-pluto.local}

# Start streamer in background
ssh root@pluto.local "killall vita49_streamer 2>/dev/null; nohup ./vita49_streamer > /tmp/streamer.log 2>&1 &"

# Wait for startup
sleep 2

# Verify running
ssh root@pluto.local "pidof vita49_streamer"
```

### Tool 3: Configure Channel Mode
**Command**: `python src/vita49/config_client.py --channels [rx0|rx1|dual]`
**Purpose**: Switch between channel modes at runtime

```bash
# Configure single-channel RX0 (default)
python src/vita49/config_client.py --pluto pluto.local --channels rx0 --freq 2.4e9 --rate 10e6

# Configure dual-channel mode
python src/vita49/config_client.py --pluto pluto.local --channels dual --freq 2.4e9 --rate 5e6

# Configure burst mode (high rate)
python src/vita49/config_client.py --pluto pluto.local --channels rx0 --rate 30e6
```

### Tool 4: Integration Test Suite
**Command**: `pytest tests/integration/ -v --pluto-ip=pluto.local`
**Purpose**: Test all channel modes with hardware

```bash
# Run all integration tests
pytest tests/integration/test_channel_modes.py -v --pluto-ip=pluto.local

# Test specific mode
pytest tests/integration/test_dual_channel_streaming.py -v
pytest tests/integration/test_burst_mode.py -v
pytest tests/integration/test_sample_loss_detection.py -v
```

### Tool 5: Performance Benchmarks
**Command**: `python tests/benchmark_throughput.py`
**Purpose**: Measure throughput and sample loss rates

```bash
# Benchmark single-channel at various rates
python tests/benchmark_throughput.py --mode single --rates 5,10,15,20,30

# Benchmark dual-channel
python tests/benchmark_throughput.py --mode dual --rates 5,10,15

# Generate report
python tests/generate_benchmark_report.py
```

### Tool 6: End-to-End Validation
**Command**: `./scripts/test-full-pipeline.sh`
**Purpose**: Complete system test with visualization

```bash
#!/bin/bash
# 1. Deploy and start
./scripts/test-deploy.sh

# 2. Configure dual-channel
python src/vita49/config_client.py --pluto pluto.local --channels dual --rate 10e6

# 3. Run E2E test
python tests/e2e/test_dual_channel_pipeline.py --duration 30

# 4. Verify results
python tests/e2e/verify_results.py
```

## Test Files to Create

#### 5.1 Unit Tests (No Hardware)

**File**: `tests/test_dual_channel_packets.py` (new)
```python
"""Unit tests for dual-channel packet handling"""

def test_stream_id_rx0():
    """Verify RX0 stream ID format"""
    assert create_stream_id(channel=1, device_id=1) == 0x01000001

def test_stream_id_rx1():
    """Verify RX1 stream ID format"""
    assert create_stream_id(channel=2, device_id=1) == 0x01000002

def test_parse_stream_id():
    """Verify stream ID parsing"""
    parsed = parse_stream_id(0x01000001)
    assert parsed['device_id'] == 1
    assert parsed['channel'] == 1

def test_sample_loss_trailer():
    """Verify trailer sample_loss bit encoding"""
    packet = VRTSignalDataPacket(...)
    packet.trailer.sample_loss = True
    encoded = packet.encode()
    decoded = VRTSignalDataPacket.decode(encoded)
    assert decoded.trailer.sample_loss == True
```

#### 5.2 Integration Tests (Hardware Required)

**File**: `tests/integration/test_channel_modes.py` (new)
```python
"""Test channel mode switching"""
import pytest

@pytest.mark.hardware
def test_single_rx0_mode(pluto_ip):
    """Test single-channel RX0 mode"""
    # Configure
    config_client.configure(pluto_ip, channels='rx0', rate=10e6)

    # Receive
    client = VITA49StreamClient(port=4991)
    client.start()
    time.sleep(5)

    # Verify only RX0 stream present
    assert 0x01000001 in client.stream_buffers
    assert 0x01000002 not in client.stream_buffers

@pytest.mark.hardware
def test_dual_channel_mode(pluto_ip):
    """Test dual-channel mode"""
    # Configure
    config_client.configure(pluto_ip, channels='dual', rate=5e6)

    # Receive
    client = VITA49StreamClient(port=4991)
    client.start()
    time.sleep(5)

    # Verify both streams present
    assert 0x01000001 in client.stream_buffers  # RX0
    assert 0x01000002 in client.stream_buffers  # RX1

    # Verify independent data
    rx0_samples = client.get_samples(0x01000001, 1000)
    rx1_samples = client.get_samples(0x01000002, 1000)
    correlation = np.corrcoef(np.abs(rx0_samples), np.abs(rx1_samples))[0,1]
    assert correlation < 0.9  # Channels should be independent
```

**File**: `tests/integration/test_burst_mode.py` (new)
```python
"""Test burst mode (high sample rate with gaps)"""

@pytest.mark.hardware
def test_burst_mode_30msps(pluto_ip):
    """Test burst mode at 30 MSPS"""
    # Configure high rate
    config_client.configure(pluto_ip, channels='rx0', rate=30e6)

    # Receive with intentional slow processing
    client = VITA49StreamClient(port=4991)
    client.start()

    # Process slowly to induce buffer overflow
    for _ in range(100):
        time.sleep(0.1)  # Simulate slow processing

    # Verify sample loss detected
    assert client.stream_stats[0x01000001]['gaps_detected'] > 0

@pytest.mark.hardware
def test_sample_loss_flags(pluto_ip):
    """Verify sample_loss flags in packets"""
    config_client.configure(pluto_ip, rate=30e6)

    client = VITA49StreamClient(port=4991)
    sample_loss_detected = False

    def on_packet(stream_id, packet, samples):
        nonlocal sample_loss_detected
        if packet.trailer and packet.trailer.sample_loss:
            sample_loss_detected = True

    client.set_callback(on_packet)
    client.start()
    time.sleep(10)

    assert sample_loss_detected, "Expected sample_loss flag in burst mode"
```

#### 5.3 End-to-End Tests

**File**: `tests/e2e/test_dual_channel_pipeline.py` (new)
```python
"""Full pipeline test with dual-channel streaming"""

def test_full_dual_channel_pipeline(pluto_ip, duration=30):
    """Test complete dual-channel streaming pipeline"""

    # 1. Configure dual-channel mode
    config_client.configure(
        pluto_ip,
        channels='dual',
        freq=2.4e9,
        rate=10e6,
        gain=40
    )

    # 2. Start receiver
    receiver = VITA49StreamClient(port=4991)
    receiver.start()

    # 3. Collect data
    time.sleep(duration)
    receiver.stop()

    # 4. Verify results
    results = {
        'rx0_packets': receiver.stream_stats[0x01000001]['packets'],
        'rx1_packets': receiver.stream_stats[0x01000002]['packets'],
        'rx0_gaps': receiver.stream_stats[0x01000001]['gaps_detected'],
        'rx1_gaps': receiver.stream_stats[0x01000002]['gaps_detected'],
    }

    # Assert both channels received data
    assert results['rx0_packets'] > 1000
    assert results['rx1_packets'] > 1000

    # Assert minimal gaps (should be <1% for 10 MSPS)
    gap_rate_rx0 = results['rx0_gaps'] / results['rx0_packets']
    gap_rate_rx1 = results['rx1_gaps'] / results['rx1_packets']
    assert gap_rate_rx0 < 0.01
    assert gap_rate_rx1 < 0.01

    return results
```

#### 5.4 Performance Benchmarks

**File**: `tests/benchmark_throughput.py` (new)
```python
"""Benchmark throughput across different modes and sample rates"""

def benchmark_mode(pluto_ip, mode, rate, duration=30):
    """Benchmark single configuration"""

    # Configure
    config_client.configure(
        pluto_ip,
        channels=mode,
        rate=rate
    )

    # Measure
    receiver = VITA49StreamClient(port=4991)
    receiver.start()

    start_time = time.time()
    time.sleep(duration)
    end_time = time.time()

    receiver.stop()

    # Calculate metrics
    total_samples = sum(
        stats['samples_received']
        for stats in receiver.stream_stats.values()
    )

    achieved_rate = total_samples / (end_time - start_time)
    expected_rate = rate * (2 if mode == 'dual' else 1)

    sample_efficiency = achieved_rate / expected_rate * 100

    return {
        'mode': mode,
        'config_rate': rate,
        'achieved_rate': achieved_rate,
        'efficiency': sample_efficiency,
        'gaps_total': sum(
            stats['gaps_detected']
            for stats in receiver.stream_stats.values()
        )
    }

def main():
    """Run comprehensive benchmark suite"""
    results = []

    # Single-channel tests
    for rate in [5e6, 10e6, 15e6, 20e6, 30e6]:
        result = benchmark_mode('pluto.local', 'rx0', rate)
        results.append(result)

    # Dual-channel tests
    for rate in [5e6, 10e6, 15e6]:
        result = benchmark_mode('pluto.local', 'dual', rate)
        results.append(result)

    # Generate report
    generate_report(results)
```

## Automated Testing Workflow

### Master Test Script: `scripts/run-all-tests.sh`

```bash
#!/bin/bash
set -e

PLUTO_IP=${PLUTO_IP:-pluto.local}

echo "=========================================="
echo "VITA49 Multi-Channel Test Suite"
echo "=========================================="

# Phase 1: Build & Unit Tests
echo ""
echo "[1/4] Building and running unit tests..."
./scripts/build-with-docker.sh
pytest tests/test_vita49.py tests/test_dual_channel_packets.py -v

# Phase 2: Deploy
echo ""
echo "[2/4] Deploying to Pluto..."
./scripts/test-deploy.sh

# Phase 3: Integration Tests
echo ""
echo "[3/4] Running integration tests..."
pytest tests/integration/ -v --pluto-ip=$PLUTO_IP --hardware

# Phase 4: Performance Benchmarks
echo ""
echo "[4/4] Running performance benchmarks..."
python tests/benchmark_throughput.py --pluto-ip=$PLUTO_IP

echo ""
echo "=========================================="
echo "✓ All tests completed!"
echo "=========================================="
```

## Success Criteria Checklist

### Build Phase
- [ ] C streamer cross-compiles without errors
- [ ] Binary is ARM ELF format (verified with `file`)
- [ ] Python unit tests pass (100% pass rate)
- [ ] No import errors in Python library

### Deploy Phase
- [ ] Binary deploys to Pluto successfully
- [ ] Streamer process starts and stays running
- [ ] Control port (4990) and data port (4991) are listening
- [ ] No crashes in first 60 seconds

### Integration Phase - Single-Channel RX0
- [ ] Receives packets on port 4991
- [ ] Stream ID = 0x01000001
- [ ] Context packets received
- [ ] Sample rate matches configuration
- [ ] Continuous streaming at ≤11 MSPS

### Integration Phase - Single-Channel RX1
- [ ] Stream ID = 0x01000002
- [ ] Independent from RX0
- [ ] Same performance as RX0

### Integration Phase - Dual-Channel
- [ ] Both stream IDs present (RX0 and RX1)
- [ ] Packets alternate (RX0, RX1, RX0, RX1...)
- [ ] Independent data streams (low correlation)
- [ ] Synchronized timestamps
- [ ] <1% gap rate at 10 MSPS total

### Burst Mode
- [ ] Streams at 30 MSPS (>11 MSPS threshold)
- [ ] Sample loss flags set when buffer overflows
- [ ] Gaps detected and logged
- [ ] No crashes under overload
- [ ] Automatic recovery after gap

### Performance
- [ ] Single-channel @30 MSPS: ~240 Mbps throughput
- [ ] Dual-channel @10 MSPS: >95% sample delivery
- [ ] CPU usage <95% per core on Pluto
- [ ] Ring buffer drops detected within 100ms

## Testing Workflow for Claude

When testing the implementation, Claude should follow this sequence:

1. **Build Verification**
   ```bash
   Bash: ./scripts/build-with-docker.sh
   Bash: pytest tests/test_vita49.py tests/test_dual_channel_packets.py -v
   ```

2. **Deploy to Pluto**
   ```bash
   Bash: make deploy-binary PLUTO_IP=pluto.local
   Bash: ssh root@pluto.local "killall vita49_streamer 2>/dev/null; nohup ./vita49_streamer > /tmp/streamer.log 2>&1 &"
   ```

3. **Test Single-Channel RX0** (baseline)
   ```bash
   Bash: python src/vita49/config_client.py --pluto pluto.local --channels rx0 --rate 10e6
   Bash: pytest tests/integration/test_channel_modes.py::test_single_rx0_mode -v
   ```

4. **Test Dual-Channel**
   ```bash
   Bash: python src/vita49/config_client.py --pluto pluto.local --channels dual --rate 5e6
   Bash: pytest tests/integration/test_channel_modes.py::test_dual_channel_mode -v
   ```

5. **Test Burst Mode**
   ```bash
   Bash: python src/vita49/config_client.py --pluto pluto.local --rate 30e6
   Bash: pytest tests/integration/test_burst_mode.py -v
   ```

6. **Performance Benchmark**
   ```bash
   Bash: python tests/benchmark_throughput.py --pluto-ip pluto.local
   ```

7. **Analyze Results**
   ```bash
   Read: benchmark_results.json
   Verify: All success criteria met
   ```

## Critical Files to Modify

**C Streamer:**
- [pluto_vita49_streamer.c](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c) - All dual-channel logic, packet encoding, ring buffers

**Python Library:**
- [packets.py](c:\git-repos\vita49-pluto\src\vita49\packets.py) - Stream ID utilities (already exists, minor updates for discontinuity flags)
- [stream_server.py](c:\git-repos\vita49-pluto\src\vita49\stream_server.py) - Multi-stream receiver, gap detection
- [config_client.py](c:\git-repos\vita49-pluto\src\vita49\config_client.py) - Channel mode parameter

**Web Interface:**
- [web_server.py](c:\git-repos\vita49-pluto\src\vita49\web_server.py) - Multi-stream backend handler
- [App.jsx](c:\git-repos\vita49-pluto\src\vita49\web\src\App.jsx) - Frontend coordination
- [SpectrumPlot.jsx](c:\git-repos\vita49-pluto\src\vita49\web\src\components\SpectrumPlot.jsx) - Dual-channel visualization

**Supporting:**
- [lock_free_ring_buffer.h](c:\git-repos\vita49-pluto\src\lock_free_ring_buffer.h) - May need minor updates for dual-buffer mode

## Master Test Command

**Command**: `./test-vita49`
**Purpose**: Single command to run full test pipeline

Create `test-vita49` script in project root:
```bash
#!/bin/bash
# Master test command for VITA49 multi-channel implementation
# Usage: ./test-vita49 [--skip-build] [--skip-deploy] [--quick]

set -e

PLUTO_IP=${PLUTO_IP:-pluto.local}
SKIP_BUILD=false
SKIP_DEPLOY=false
QUICK_MODE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-build) SKIP_BUILD=true; shift ;;
        --skip-deploy) SKIP_DEPLOY=true; shift ;;
        --quick) QUICK_MODE=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║         VITA49 Multi-Channel Test Pipeline                    ║"
echo "╚════════════════════════════════════════════════════════════════╝"

# Phase 1: Build
if [ "$SKIP_BUILD" = false ]; then
    echo ""
    echo ">>> Phase 1: BUILD & UNIT TESTS"
    ./scripts/build-with-docker.sh
    pytest tests/test_vita49.py tests/test_dual_channel_packets.py -v --tb=short
fi

# Phase 2: Deploy
if [ "$SKIP_DEPLOY" = false ]; then
    echo ""
    echo ">>> Phase 2: DEPLOY TO PLUTO"
    make deploy-binary PLUTO_IP=$PLUTO_IP
    ssh root@$PLUTO_IP "killall vita49_streamer 2>/dev/null || true; nohup ./vita49_streamer > /tmp/streamer.log 2>&1 &"
    sleep 3
    ssh root@$PLUTO_IP "pidof vita49_streamer" || { echo "ERROR: Streamer failed to start"; exit 1; }
fi

# Phase 3: Integration Tests
echo ""
echo ">>> Phase 3: INTEGRATION TESTS"

if [ "$QUICK_MODE" = false ]; then
    pytest tests/integration/ -v --pluto-ip=$PLUTO_IP --tb=short
else
    pytest tests/integration/test_channel_modes.py -v --pluto-ip=$PLUTO_IP --tb=short
fi

# Phase 4: Performance
if [ "$QUICK_MODE" = false ]; then
    echo ""
    echo ">>> Phase 4: PERFORMANCE BENCHMARK"
    python tests/benchmark_throughput.py --pluto-ip $PLUTO_IP
fi

# Summary
echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  ✓ ALL TESTS PASSED                                           ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "View logs:"
echo "  ssh root@$PLUTO_IP cat /tmp/streamer.log"
echo ""
```

Make executable:
```bash
chmod +x test-vita49
```

**Usage Examples:**
```bash
./test-vita49                          # Full test suite
./test-vita49 --quick                  # Fast: build + deploy + basic tests
./test-vita49 --skip-build             # Use existing binary
./test-vita49 --skip-deploy            # Assume streamer already running
PLUTO_IP=192.168.2.1 ./test-vita49     # Test with specific IP
```

## Implementation Sequence

### Phase 1: C Streamer Core

#### ✅ Phase 1A: Dual-Channel Hardware Support (COMPLETED)
1. **Phase 1.1-1.3**: Channel mode config, RX1 hardware, dual ring buffers
2. **Phase 1.4**: Interleaved buffer parsing in DMA thread
3. **Phase 1.5-1.6**: Stream ID parameterization and dual-channel transmission
4. **Phase 1.7**: Sample loss detection and VITA49 flags

**Commits Made:**
- c69754e - Dual-channel infrastructure
- eb6f9b9 - Interleaved buffer parsing
- 8a3aff8 - Stream ID parameterization
- 88ef2d5 - Dual-channel network transmission
- 34739dd - Sample loss detection and flags

**Status**: Dual-channel hardware support complete. Current implementation supports streaming mode (immediate packet transmission) for both single and dual-channel configurations.

#### ⏸️ Phase 1B: Burst Mode Buffering (PENDING)

**Goal**: Implement true burst mode with accumulation buffer for high sample rates (>11 MSPS).

**File**: [pluto_vita49_streamer.c](c:\git-repos\vita49-pluto\src\pluto_vita49_streamer.c)

**Changes Required:**

1. **Add Burst Buffer Infrastructure** (after global state):
```c
// Burst mode buffers (10M samples = 40 MB per channel)
#define BURST_BUFFER_SIZE (10 * 1024 * 1024)  // 10M samples
static int16_t *g_burst_buffer_rx0 = NULL;
static int16_t *g_burst_buffer_rx1 = NULL;
static atomic_size_t g_burst_fill_rx0 = ATOMIC_VAR_INIT(0);
static atomic_size_t g_burst_fill_rx1 = ATOMIC_VAR_INIT(0);
static bool g_burst_mode = false;  // Set based on sample rate threshold
```

2. **Modify DMA Reader Thread** (dma_reader_thread):
- Detect operating mode: if (sample_rate > 11e6) use burst mode, else streaming mode
- **Streaming Mode**: Current behavior (push to ring buffer immediately)
- **Burst Mode**: Accumulate samples in burst buffer until full, then signal network thread

3. **Modify Network Thread** (network_thread):
- **Streaming Mode**: Current behavior (pop from ring buffer, send packets continuously)
- **Burst Mode**: Wait for burst buffer full signal, then rapid-fire transmit all packets
- Set sample_loss flag on first packet after each burst (indicates gap from previous burst)

4. **Buffer Allocation** (main):
```c
// Allocate burst buffers
g_burst_buffer_rx0 = calloc(BURST_BUFFER_SIZE, sizeof(int16_t));
g_burst_buffer_rx1 = calloc(BURST_BUFFER_SIZE, sizeof(int16_t));
```

**Rationale**: Separate streaming vs burst mode allows:
- Low latency continuous streaming at ≤11 MSPS (existing use case)
- Maximum contiguous IQ samples at >11 MSPS (new burst use case)
- Automatic mode selection based on sample rate

### Phase 2: Configuration Protocol Extension (Python + C)
5. **C Streamer**: Parse channel_mode from context packets (CIF bit 16)
6. **Python config_client.py**: Add `--channels` CLI argument and encoding
7. **Commit**: Configuration protocol for channel selection

### Phase 3: Python Receiver Multi-Stream Support
8. **stream_server.py**: Per-stream buffering and gap detection
9. **stream_server.py**: Stream access API with `get_samples(stream_id, count)`
10. **Commit**: Multi-stream receiver implementation

### Phase 4: Web UI Multi-Channel Visualization
11. **web_server.py**: Multi-stream handler backend
12. **SpectrumPlot.jsx**: Dual-channel display frontend
13. **Commit**: Web UI multi-channel support

### Phase 5: Testing Infrastructure
14. **Create test files**: All test scripts and benchmarks
15. **Create master test command**: `test-vita49` wrapper script
16. **Create helper scripts**: `scripts/test-*.sh` utilities
17. **Run full test suite**: Verify implementation end-to-end
18. **Commit**: Testing infrastructure and validation

## Test Files to Create

### Unit Tests (No Hardware)
- `tests/test_dual_channel_packets.py` - Stream ID and packet format tests
- Update `tests/test_vita49.py` - Add sample_loss trailer tests

### Integration Tests (Hardware Required)
- `tests/integration/__init__.py` - Test package init
- `tests/integration/conftest.py` - Pytest fixtures for hardware tests
- `tests/integration/test_channel_modes.py` - Test RX0, RX1, DUAL modes
- `tests/integration/test_burst_mode.py` - High sample rate tests
- `tests/integration/test_sample_loss_detection.py` - Gap detection tests

### E2E Tests
- `tests/e2e/test_dual_channel_pipeline.py` - Full pipeline validation

### Benchmarks
- `tests/benchmark_throughput.py` - Performance measurements
- `tests/generate_benchmark_report.py` - Report generator

### Helper Scripts
- `test-vita49` - Master test command (project root)
- `scripts/test-build.sh` - Build and unit test only
- `scripts/test-deploy.sh` - Deploy and start streamer
- `scripts/run-all-tests.sh` - Comprehensive test suite

## Verification Strategy

**Success Criteria:**
- [ ] Single-channel continuous (≤11 MSPS) works unchanged
- [ ] Single-channel burst (>11 MSPS) achieves near-Gigabit throughput (~240 Mbps)
- [ ] Dual-channel burst sends both RX0+RX1 with distinct stream IDs
- [ ] Sample loss flags set correctly during buffer overflow
- [ ] Python receiver demultiplexes streams independently
- [ ] Web UI displays both channels with gap indicators

**Testing Approach:**
1. Unit tests for buffer parsing and stream demultiplexing
2. Burst mode overload test with intentional slowdown
3. Dual-channel integrity test verifying independent streams
4. Performance benchmarks measuring throughput and CPU usage
5. End-to-end web UI test with live dual-channel visualization

**Key Metrics:**
- Dual-channel @10 MSPS: >95% sample delivery rate
- Single-channel @30 MSPS: ~240 Mbps throughput (near Gigabit limit)
- CPU usage on Pluto: <95% per core
- Ring buffer drops: detected and reported within 100ms

## Design Rationale

**Why alternating packets (RX0, RX1, RX0, RX1)?**
- Lower latency for both channels
- More predictable receiver buffering
- Simpler round-robin scheduling

**Why same UDP port for both channels?**
- Single socket, single subscriber list (simplicity)
- VITA49 stream_id designed for demultiplexing
- Easier NAT traversal

**Why software dropping vs DMA rate control?**
- User requirement: "send as fast as possible"
- Pluto DMA runs at fixed hardware rate
- Natural backpressure via ring buffer
- Simpler implementation

**Why shared config for dual-channel?**
- User requirement specified
- AD9361 hardware shares LO and sample rate between RX paths
- Simpler configuration protocol
