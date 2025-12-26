# Buffer Health Monitoring and Underflow/Overflow Detection

## Overview

The VITA49 Pluto SDR streamer includes comprehensive buffer health monitoring to detect and report hardware buffer underflows, overflows, timing issues, and performance metrics. This system helps identify streaming issues in real-time and provides detailed diagnostics for troubleshooting.

## Architecture

### Statistics Structure

The streamer maintains a thread-safe `stream_statistics_t` structure with the following categories:

#### 1. Basic Statistics
- **packets_sent**: Total number of VITA49 data packets transmitted
- **bytes_sent**: Total bytes transmitted (includes headers and payload)
- **contexts_sent**: Number of Context packets sent
- **reconfigs**: Number of configuration changes applied

#### 2. Health Monitoring
- **underflows**: Count of detected buffer underflows (samples arriving late)
- **overflows**: Count of detected buffer overflows (samples arriving early - rare)
- **refill_failures**: Number of times `iio_buffer_refill()` failed
- **send_failures**: Number of packet send failures (reserved for future use)
- **timestamp_jumps**: Number of timestamp discontinuities detected
- **last_timestamp_us**: Last recorded timestamp in microseconds

#### 3. Performance Metrics
- **min_loop_time_us**: Minimum time for one processing loop iteration
- **max_loop_time_us**: Maximum time for one processing loop iteration
- **total_loop_time_us**: Cumulative time spent in all loop iterations
- **loop_iterations**: Total number of loop iterations completed

### Thread Safety

All statistics updates are protected by a pthread mutex (`g_stats.mutex`) to ensure thread-safe access from multiple threads:
- Control thread (configuration updates)
- Streaming thread (data processing)
- Main thread (statistics reporting)

## Detection Mechanisms

### 1. Buffer Refill Failure Detection

When `iio_buffer_refill()` fails:
```c
if (nbytes < 0) {
    pthread_mutex_lock(&g_stats.mutex);
    g_stats.refill_failures++;
    pthread_mutex_unlock(&g_stats.mutex);

    // Attempt recovery with 1ms delay
    usleep(1000);
    continue;  // Don't break - try to recover
}
```

**What it means**: The IIO library couldn't refill the buffer from hardware, indicating:
- Hardware communication issues
- Driver problems
- System resource exhaustion

**Recovery**: The system waits 1ms and retries instead of terminating.

### 2. Timestamp Discontinuity Detection

The system monitors timing between buffer refills:

```c
uint64_t expected_delta_us = (num_samples * 1000000ULL) / sample_rate;
uint64_t actual_delta_us = current_ts - last_timestamp_us;
int64_t delta_error = (int64_t)(actual_delta_us - expected_delta_us);

if (llabs(delta_error) > 10000) {  // More than 10ms discrepancy
    g_stats.timestamp_jumps++;

    if (delta_error > 0) {
        g_stats.underflows++;  // Samples arrived late
    } else {
        g_stats.overflows++;   // Samples arrived early
    }
}
```

**What it means**:
- **Positive delta (underflow)**: Processing is too slow, can't keep up with sample rate
- **Negative delta (overflow)**: Samples arriving faster than expected (unusual)
- **Threshold**: 10ms chosen to avoid false positives from normal jitter

**Causes of underflows**:
- CPU overload
- Network congestion (if samples buffered in hardware)
- Disk I/O blocking the processing thread
- Memory allocation delays

### 3. Loop Timing Analysis

Each processing loop iteration is timed:

```c
uint64_t loop_start = get_timestamp_us();
// ... processing ...
uint64_t loop_time = get_timestamp_us() - loop_start;

// Update statistics
if (loop_time < min_loop_time_us || min_loop_time_us == 0) {
    min_loop_time_us = loop_time;
}
if (loop_time > max_loop_time_us) {
    max_loop_time_us = loop_time;
}
total_loop_time_us += loop_time;
loop_iterations++;
```

**What it means**:
- **min_loop_time_us**: Best-case performance (ideal conditions)
- **max_loop_time_us**: Worst-case performance (potential bottleneck indicator)
- **average**: `total_loop_time_us / loop_iterations`

**Interpretation**:
- Large gap between min and max suggests inconsistent performance
- Average should be less than `(num_samples / sample_rate)` to maintain real-time
- Increasing max over time suggests resource leaks or degradation

## VITA49 State Event Indicators

### Context Packet Enhancement

Health status is embedded in VITA49 Context packets using CIF bit 19 (State/Event Indicators):

```c
uint32_t state_event = 0;
state_event |= (1U << 31);  // Calibrated Time
if (overflows > 0) {
    state_event |= (1 << 19);  // Overrange indicator
}
if (underflows > 0) {
    state_event |= (1 << 18);  // Sample Loss indicator
}
```

**VITA49 Compliance**: This follows VITA49.0 specification section 9.5.8:
- **Bit 31**: Calibrated Time (indicates timestamp is synchronized)
- **Bit 19**: Overrange (maps to overflow detection)
- **Bit 18**: Sample Loss (maps to underflow/sample loss detection)

**Receiver Integration**: Downstream receivers can parse Context packets to:
- Monitor streamer health remotely
- Implement automatic failover
- Log quality metrics
- Trigger alerts on state changes

### Parsing State Events

Example Python code to extract state events:

```python
state_event = struct.unpack('>I', data[offset:offset+4])[0]
calibrated_time = bool(state_event & (1 << 31))
overrange = bool(state_event & (1 << 19))
sample_loss = bool(state_event & (1 << 18))

if sample_loss:
    print("WARNING: Streamer reporting sample loss (underflow)")
if overrange:
    print("WARNING: Streamer reporting overrange (overflow)")
```

## Monitoring Output

The main monitoring thread displays comprehensive statistics every 5 seconds:

```
[Stats] Packets: 125000, Bytes: 450 MB, Contexts: 1250, Subs: 2
[Health] Underflows: 0, Overflows: 0, Refill Fails: 0, TS Jumps: 0
[Timing] Loop: avg=245.3 us, min=180 us, max=1250 us
```

### Interpreting the Output

#### Stats Line
- **Packets**: Total VITA49 data packets sent (should increase steadily)
- **Bytes**: Total MB transmitted (verify against expected data rate)
- **Contexts**: Context packets sent (periodic + config changes)
- **Subs**: Active subscribers (clients receiving data)

#### Health Line
- **Underflows**: Should be 0 in healthy operation
  - Non-zero indicates processing can't keep up with sample rate
  - Common causes: high sample rate, CPU overload, network issues

- **Overflows**: Should always be 0
  - Non-zero is unusual and indicates timing anomalies

- **Refill Fails**: Should be 0 in healthy operation
  - Non-zero indicates hardware communication issues
  - Check USB connection, driver status, system resources

- **TS Jumps**: Should be 0 or very low
  - Occasional jumps may occur during config changes
  - Frequent jumps indicate timing instability

#### Timing Line
- **avg**: Average loop time
  - Should be `< (buffer_size / sample_rate)` for real-time
  - Example: 16384 samples @ 30.72 MSPS = 533 us maximum

- **min**: Best-case performance (baseline)
  - Useful for capacity planning

- **max**: Worst-case performance
  - High values indicate jitter or blocking operations
  - Watch for increasing trend over time

## Performance Guidelines

### Sample Rate Limits

Based on loop timing, recommended maximum sample rates:

| Platform | Max Sample Rate | Notes |
|----------|-----------------|-------|
| Pluto (ARM Cortex-A9) | 30.72 MSPS | Conservative for reliable operation |
| Pluto (overclocked) | 40-50 MSPS | May experience occasional underflows |
| x86 Linux (USB 2.0) | 61.44 MSPS | Limited by USB 2.0 bandwidth |
| x86 Linux (USB 3.0) | 61.44 MSPS | Limited by AD9361 hardware |

### Detecting Issues

#### Scenario 1: High Sample Rate
```
[Health] Underflows: 127, Overflows: 0, Refill Fails: 0, TS Jumps: 127
[Timing] Loop: avg=850.5 us, min=200 us, max=2400 us
```
**Diagnosis**: Average loop time too high, can't keep up with sample rate
**Solution**: Reduce sample rate or optimize processing

#### Scenario 2: Hardware Issues
```
[Health] Underflows: 0, Overflows: 0, Refill Fails: 45, TS Jumps: 0
[Timing] Loop: avg=245.3 us, min=180 us, max=1250 us
```
**Diagnosis**: Buffer refill failures indicate hardware communication issues
**Solution**: Check USB connection, verify driver, check system resources

#### Scenario 3: Network Congestion
```
[Health] Underflows: 0, Overflows: 0, Refill Fails: 0, TS Jumps: 0
[Timing] Loop: avg=245.3 us, min=180 us, max=15000 us
```
**Diagnosis**: Very high max loop time suggests blocking on send operations
**Solution**: Check network bandwidth, reduce number of subscribers

## Testing

### Automated Testing

Use the provided test script to verify underflow detection:

```bash
python3 tests/test_underflow_detection.py --pluto-ip 192.168.2.1
```

The test:
1. Configures high sample rate (61.44 MSPS)
2. Introduces artificial network congestion
3. Monitors Context packets for state events
4. Verifies underflow counters increase
5. Tests recovery after clearing congestion

### Manual Testing

#### Test 1: High Sample Rate Stress
```bash
# Set sample rate beyond capacity
# Monitor for underflows
./vita49_streamer
# In another terminal:
python3 -c "
from tests.test_underflow_detection import VITA49Receiver
rx = VITA49Receiver()
rx.send_config('192.168.2.1', 2400000000, 61440000, 20.0)
"
```

#### Test 2: CPU Overload
```bash
# Start streamer
./vita49_streamer &

# Create CPU load
stress --cpu 4 --timeout 60s

# Monitor health statistics
# Should see underflows increase
```

#### Test 3: Network Congestion
```bash
# Use tc (traffic control) to limit bandwidth
tc qdisc add dev eth0 root tbf rate 1mbit burst 32kbit latency 400ms

# Start streaming
./vita49_streamer

# Monitor for send delays in timing statistics
```

## Best Practices

### 1. Monitoring Setup
- Enable periodic statistics logging
- Set up alerts for non-zero underflow/overflow counts
- Graph timing metrics to detect trends

### 2. Capacity Planning
- Test with expected sample rate
- Verify average loop time < required period
- Leave 20% headroom for jitter

### 3. Troubleshooting Workflow
1. Check Health line for non-zero counters
2. Analyze Timing line for bottlenecks
3. Correlate with system metrics (CPU, network, disk)
4. Adjust sample rate or system resources
5. Re-test and verify

### 4. Production Deployment
- Start with conservative sample rates
- Monitor for 24 hours before increasing
- Keep underflow rate < 0.01% of packets
- Set up automated recovery (restart on repeated failures)

## Future Enhancements

Potential improvements to the health monitoring system:

1. **Adaptive Sample Rate**: Automatically reduce sample rate on persistent underflows
2. **Histogram Metrics**: Track distribution of loop times
3. **Remote Monitoring API**: HTTP endpoint for statistics
4. **Graceful Degradation**: Drop to lower quality on congestion
5. **Detailed Error Logging**: Per-error-type timestamps and context

## References

- VITA49.0 Specification: Section 9.5.8 (State and Event Indicators)
- libiio Documentation: https://analogdevicesinc.github.io/libiio/
- AD9361 Maximum Sample Rate: 61.44 MSPS
- VITA49 Pluto Project: https://github.com/lgarrido581/vita49-pluto-sdr
