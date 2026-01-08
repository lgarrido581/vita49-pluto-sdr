# Multicore Optimization Testing Guide

This document outlines comprehensive testing criteria for each phase of the VITA49 multicore optimization implementation.

## Overview

The multicore optimization splits the single-threaded VITA49 streamer into a dual-core architecture:
- **Core 0**: DMA reader (producer) - High priority IQ sample acquisition  
- **Core 1**: Network transmitter + Config handler (consumer) - VITA49 encoding and transmission

## Testing Strategy

### Hardware Requirements
- **ADALM-Pluto SDR** with libiio support
- **Dual-core ARM environment** (Pluto's Zynq-7010)
- **Network connectivity** to host PC for packet reception
- **SSH access** to Pluto for deployment and monitoring

### Software Requirements  
- **Cross-compilation toolchain** (arm-linux-gnueabihf-gcc)
- **Python VITA49 library** for packet validation
- **Network monitoring tools** (tcpdump, ss, iperf)
- **Performance monitoring** (top, htop, /proc/stat)

---

## Phase 1: Lock-Free Ring Buffer Testing

### Test Objectives
- Validate ring buffer correctness and performance
- Ensure thread-safe single-producer, single-consumer operation
- Verify memory ordering and cache-line alignment
- Test under stress conditions with high throughput

### Test Cases

#### 1.1 Unit Testing (Host Environment)
```bash
# Compile and run ring buffer unit tests
make test-ring-buffer

# Expected results:
# - All 4 tests pass (Basic, Wrap-around, Stress, Performance)  
# - >1M operations/sec throughput
# - 0% data corruption in multithread stress test
# - <1ms latency per operation
```

**Success Criteria:**
- ✅ All unit tests pass
- ✅ Performance >1M ops/sec  
- ✅ Zero data corruption
- ✅ Memory usage stable over time

#### 1.2 Integration Testing (On Pluto)
```bash
# Deploy test binary to Pluto
scp tests/test_ring_buffer root@pluto.local:/root/
ssh root@pluto.local './test_ring_buffer'

# Monitor memory usage during test  
watch -n1 'cat /proc/meminfo | grep Available'
```

**Success Criteria:**
- ✅ Tests pass on ARM architecture
- ✅ No memory leaks detected
- ✅ Cache-aligned structures verified

### Debugging Phase 1 Issues

**Ring Buffer Corruption:**
- Check atomic memory ordering (acquire/release semantics)
- Verify power-of-2 buffer sizing
- Add bounds checking in debug builds

**Performance Issues:**
- Verify cache line alignment (64 bytes)
- Check for false sharing between producer/consumer
- Monitor context switches with `pidstat -w`

---

## Phase 2: DMA Reader Thread Testing

### Test Objectives
- Validate DMA reader isolation on Core 0
- Ensure ring buffer integration works correctly
- Test thread priority and CPU affinity
- Verify reconfiguration handling

### Test Cases

#### 2.1 Basic DMA Thread Functionality
```bash
# Deploy optimized streamer to Pluto
make deploy

# Start with basic monitoring
ssh root@pluto.local
./vita49_streamer &

# Monitor thread affinity
for tid in $(ps -T -p $(pgrep vita49_streamer) -o lwp= ); do
    echo "Thread $tid: CPU $(cat /proc/$tid/stat | awk '{print $39}')"
done
```

**Expected Output:**
```
Ring buffer initialized: 64 entries, 32 MB
[DMA Reader] Started - pinned to Core 0, priority 90
[DMA Reader] Buffer: 32768 samples (1.09 ms at 30.0 MSPS)
[Control] Listening on port 4990 - pinned to Core 1
```

**Success Criteria:**
- ✅ DMA Reader thread pinned to Core 0
- ✅ High priority (90) successfully set
- ✅ Ring buffer initialization succeeds
- ✅ Buffer size calculated correctly

#### 2.2 Ring Buffer Population Test
```bash
# Send configuration to start streaming
python src/vita49/config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6 --gain 40

# Monitor ring buffer stats (should appear every 5 seconds)
# Expected:
# [Multicore] DMA: 1000+ bufs, Ring: 5-15% full, Drops: 0
```

**Success Criteria:**
- ✅ DMA buffers processed counter increases steadily
- ✅ Ring buffer utilization 5-20% (healthy buffering)
- ✅ Zero ring buffer drops under normal load
- ✅ No refill failures reported

#### 2.3 CPU Utilization Validation
```bash
# Monitor per-core CPU usage
while true; do
    echo "$(date): $(grep 'cpu[01]' /proc/stat)"
    sleep 1
done

# Expected:
# Core 0: 85-95% (DMA + memcpy)
# Core 1: 5-15% (control only)
```

**Success Criteria:**
- ✅ Core 0 utilization >80% (was 50% in single-core)
- ✅ Core 1 utilization <20% (mostly idle)
- ✅ No context switches between cores for DMA thread

#### 2.4 Reconfiguration Stress Test
```bash
# Rapid reconfiguration test
for rate in 5e6 10e6 20e6 30e6; do
    python src/vita49/config_client.py --pluto pluto.local --rate $rate
    sleep 2
    echo "Ring buffer stats at $rate:"
    # Check ring buffer drops, should remain 0
done
```

**Success Criteria:**  
- ✅ All reconfigurations succeed
- ✅ Ring buffer drops remain 0 during transitions
- ✅ DMA thread handles config changes gracefully
- ✅ No memory leaks during reconfig cycles

### Debugging Phase 2 Issues

**High Ring Buffer Drops:**
- Consumer (control thread) too slow
- Increase ring buffer size or reduce DMA buffer size
- Check for Core 1 overload

**DMA Thread Starvation:**
- Verify SCHED_FIFO priority set correctly (requires root)
- Check CPU isolation settings
- Monitor IRQ distribution

**Memory Issues:**
- Validate buffer pool bounds checking
- Check for memcpy overflow
- Monitor RSS memory growth

---

## Phase 3: Network Thread Integration (Future)

### Test Objectives (Planned)
- Merge configuration handling into Core 1
- Implement ring buffer consumption 
- Validate VITA49 encoding performance
- Test combined Core 1 workload

### Test Cases (Phase 3)

#### 3.1 Network Thread Consumer Test
```bash
# Start with network thread consuming ring buffer
./vita49_streamer &

# Validate packet reception on host
python tests/e2e/test_plotting_receiver.py --port 4991

# Expected Core 1 utilization: 90-95%
```

#### 3.2 Full Dual-Core Performance Test
```bash
# Run bandwidth scaling test
python tests/test_rate_control.py --pluto pluto.local --rates 5e6 10e6 20e6 30e6

# Expected results:
# - Linear bandwidth scaling  
# - Core 0: 95% utilization
# - Core 1: 95% utilization
# - Total bandwidth >400 Mbps
```

---

## Performance Benchmarks

### Target Performance (After Phase 2)
| Metric | Single-Core (Before) | Phase 2 (DMA Split) | Phase 3 (Full) |
|--------|---------------------|-------------------|----------------|
| **Core 0 CPU** | 50% | 90% | 95% |
| **Core 1 CPU** | 50% | 10% | 95% |
| **Total CPU** | 50% | 50% | 95% |
| **Bandwidth** | 240 Mbps | 240 Mbps | 400+ Mbps |
| **Latency** | 2-3ms | 1-2ms | <500μs |
| **Ring Buffer Drops** | N/A | 0 | 0 |

### Real-time Monitoring Commands

**CPU Usage Per Core:**
```bash
grep 'cpu[01]' /proc/stat
```

**Thread Affinity:**
```bash
ps -eTo pid,lwp,psr,comm | grep vita49
```

**Ring Buffer Performance:**
```bash
# Available in streamer stats output every 5 seconds
# [Multicore] DMA: X bufs, Ring: Y% full, Drops: Z
```

**Network Performance:**
```bash
# Monitor UDP socket buffer usage  
ss -u -a | grep 4991

# Capture packet timing
tcpdump -i eth0 -c 100 -tt port 4991
```

### Performance Regression Detection

**Acceptable Ranges:**
- Ring buffer utilization: 5-30% (too low = underutilized, too high = consumer slow)
- DMA buffers/sec: Should scale linearly with sample rate  
- Ring buffer drops: Must remain 0 under steady-state
- Memory usage: Should be stable (no leaks)

**Red Flags:**
- Ring buffer drops >0 consistently
- Core 0 utilization <80% (not fully utilizing DMA optimization)
- Memory usage increasing over time
- Context switches between cores for DMA thread

---

## Test Automation

### Continuous Integration Tests
```bash
# Phase 1: Ring buffer validation
make test-ring-buffer || exit 1

# Phase 2: Cross-compilation check
make cross || exit 1

# Phase 2: Static analysis (if available)
cppcheck src/pluto_vita49_streamer.c || exit 1
```

### Hardware-in-Loop Tests (Requires Pluto)
```bash
# Deploy and basic functionality
make deploy
ssh root@pluto.local 'timeout 30 ./vita49_streamer &'

# Configuration test
python src/vita49/config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6

# Packet reception test  
timeout 10 python tests/e2e/test_plotting_receiver.py --port 4991
```

### Load Testing
```bash
# Extended operation test (1 hour)
ssh root@pluto.local 'timeout 3600 ./vita49_streamer > /tmp/vita49.log 2>&1 &'

# Memory leak detection
while true; do
    echo "$(date): $(cat /proc/meminfo | grep MemAvailable)" >> memory_log.txt
    sleep 60
done
```

---

## Troubleshooting Quick Reference

### Common Issues

**Issue: Ring buffer always full**
- Network thread not consuming fast enough
- Check Core 1 CPU utilization and socket buffers

**Issue: DMA thread low priority**  
- Verify root privileges for SCHED_FIFO
- Check kernel RT capabilities

**Issue: High context switches**
- Verify CPU affinity properly set
- Check for unnecessary mutex contention

**Issue: Memory usage growing**
- Ring buffer not properly cycling through buffer pool
- Check for proper buffer_idx rotation

### Debug Build
```bash
make clean
make cross CFLAGS="-g -O0 -DDEBUG -DMULTICORE_DEBUG"
```

### Performance Profiling
```bash
# Monitor memory access patterns
perf stat -e cache-misses ./vita49_streamer

# Check lock contention
strace -c -p $(pgrep vita49_streamer)
```

---

This testing framework ensures each phase can be validated independently while building toward the full dual-core optimization goal.