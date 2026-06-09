# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VITA49 Pluto Streamer turns ADALM-Pluto SDR into a networked VITA49 streaming server. It consists of:
- **C streamer** (`pluto_vita49_streamer.c`) running on Pluto ARM processor  
- **Python library** (`vita49/`) for VITA49 packet encoding/decoding
- **Web UI** for real-time spectrum visualization and control
- **Test suite** and examples for verification and development

## Essential Commands

### Build Commands
```bash
# Cross-compile for ARM (recommended)
make cross
make deploy                    # Build and deploy to Pluto
make deploy PLUTO_IP=192.168.2.1  # Deploy to specific IP

# Docker build (works on all platforms)
./scripts/build-with-docker.sh     # Linux/macOS
.\scripts\build-with-docker.bat    # Windows

# Build diagnostic tool
make diagnostic
make deploy-all                # Deploy both streamer and diagnostic
```

### Testing Commands
```bash
# Unit tests
pytest tests/ -v
pytest tests/test_vita49.py -v        # VITA49 packet tests
pytest tests/test_pluto_config.py -v  # Config client tests

# End-to-end tests (requires Pluto hardware)
python tests/e2e/test_full_pipeline.py --pluto-uri ip:192.168.2.1
python tests/e2e/test_receive_from_pluto.py --uri ip:192.168.2.1
python tests/e2e/test_plotting_receiver.py --port 4991

# Rate control and bandwidth testing
python tests/test_rate_control.py --pluto 192.168.2.1
python tests/verify_bandwidth_scaling.py --pluto 192.168.2.1
```

### Development Setup
```bash
# Install Python package in editable mode (required for development)
pip install -e .               # Basic installation
pip install -e ".[dev]"        # With testing dependencies
pip install -e ".[examples]"   # With matplotlib for examples

# Verify installation
python -c "from vita49 import packets; print('✓ vita49 library installed')"
```

### Running the System
```bash
# 1. Deploy and start streamer on Pluto
make deploy
ssh root@pluto.local
./vita49_streamer &

# 2. Configure Pluto from PC
python src/vita49/config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6 --gain 40

# 3. Receive data
python tests/e2e/test_plotting_receiver.py --port 4991
```

### Web UI Commands
```bash
# Terminal 1 - Backend server
python -m vita49.web_server --host 0.0.0.0 --port 8001

# Terminal 2 - Frontend dev server  
cd src/vita49/web
npm install  # First time only
npm run dev

# Access at http://localhost:3000
```

## Architecture

### Core Components

**C Streamer (src/pluto_vita49_streamer.c)**
- Multi-threaded: Control thread (port 4990) + Streaming thread (port 4991)
- DMA-paced architecture: `iio_buffer_refill()` blocks until buffer ready (~2ms)
- Thread synchronization via mutexes for config and subscriber management
- MTU optimization: Automatically sizes packets to prevent fragmentation

**Python Library (src/vita49/)**
- `packets.py`: VITA49 packet encoding/decoding (Data + Context packets)
- `stream_server.py`: Streaming server and client implementations  
- `config_client.py`: Remote configuration via VITA49 Context packets
- `web_server.py`: FastAPI backend for web interface

**Key Architectural Patterns**
- Subscriber management: Multiple receivers can listen to same stream
- Context packet synchronization: Stream ID must match between Data and Context packets
- Packet optimization: 364 samples/packet for 98.7% MTU efficiency
- Thread-safe configuration updates via atomic operations

### VITA49 Packet Format

**Data Packet Structure:**
```
Header (32-bit): Type[31:28] | Flags[27:24] | Count[19:16] | Size[15:0]
Stream ID (32-bit): Channel identifier (0x00000001)  
Timestamp (64-bit): UTC seconds + picoseconds
Payload: int16 I/Q pairs (big-endian, I first then Q)
Trailer (32-bit): Valid data indicator
```

**Context Packet (sent every 100 data packets):**
- Same Stream ID as corresponding data packets (critical for receiver association)
- Contains: Sample rate, RF frequency, bandwidth, gain
- Triggered by configuration changes or periodic updates

## Testing Strategy

### Test Hierarchy
- **Unit tests**: VITA49 packet encode/decode, config client
- **Integration tests**: Simple streaming without hardware  
- **E2E tests**: Full pipeline with Pluto hardware
- **Performance tests**: Rate control, bandwidth scaling, timing analysis

### Critical Tests
- `test_vita49.py`: Packet format compliance and encode/decode correctness
- `test_rate_control.py`: DMA streaming performance across sample rates
- `test_subscriber_management.py`: Multi-receiver scenarios
- `test_packet_optimization.py`: MTU efficiency verification

## Important Implementation Details

### Context Packet Stream ID Fix
**Critical Bug:** Context packets must use same Stream ID as data packets (not 0). This was fixed in `packets.py:encode()` method. Without this fix, receivers cannot associate context with data streams.

### DMA-Paced Streaming
The C streamer uses `iio_buffer_refill()` as the natural pacing mechanism rather than artificial delays. This results in:
- Bursty packet transmission (normal behavior)
- High timing variance but zero sample loss
- CPU usage scales linearly with sample rate (20-30% at 30 MSPS)

### Performance Characteristics
- **Binary size**: 50KB (300x smaller than Python equivalent)
- **Memory**: 2MB RAM usage
- **Throughput**: ~240 Mbps @ 30 MSPS (near Gigabit Ethernet limit)
- **Latency**: 1-2ms (UDP + buffering)

## Development Guidelines

### Code Organization
- **Prefer editing existing files** over creating new ones
- **C code**: Follow Linux kernel style (4-space indent, K&R braces)
- **Python code**: Follow PEP 8, use snake_case for functions
- **Thread safety**: Always protect shared data with mutexes

### Testing Requirements  
- **Always run tests** before submitting changes
- **Hardware tests** require actual Pluto SDR
- **Simulation mode** available for testing without hardware
- **Performance tests** validate streaming behavior under load

### Documentation Updates
When making changes, update relevant docs:
- `docs/BUILD.md`: Build system changes
- `docs/USAGE.md`: Usage and deployment changes  
- `docs/DEVELOPMENT.md`: Architecture or testing changes
- `README.md`: Feature additions or major changes