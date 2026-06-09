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

## Implementation Status

### ✅ Phase 1A: Dual-Channel Hardware Support (COMPLETED)
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

### ⏸️ Phase 1B: Burst Mode Buffering (PENDING)
Implementation of true burst mode with 10M sample accumulation buffer.

### ⏸️ Phase 2: Configuration Protocol (PENDING)
C streamer parsing and Python config_client.py channel mode support.

### ⏸️ Phase 3: Python Receiver (PENDING)
Multi-stream demultiplexing and gap detection.

### ⏸️ Phase 4: Web UI (PENDING)
Dual-channel visualization.

### ⏸️ Phase 5: Testing (PENDING)
Comprehensive test suite and validation.

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

---

For complete implementation details, test plans, and code examples, see the full plan at: `C:\Users\Luis\.claude\plans\giggly-moseying-toucan.md`
