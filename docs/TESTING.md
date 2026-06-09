# VITA49 Streamer Testing Guide

This document explains how to run the test suite and interpret the results for the VITA49 Pluto SDR streamer.

## Architecture Overview

The streamer uses a 2-thread DMA-paced architecture:

- **Data Thread (Core 0)**: Calls `iio_buffer_refill()` which blocks until DMA fills the buffer (~2ms at 30 MSPS), then immediately sends all packets
- **Control Thread (Core 1)**: Receives configuration packets, zero CPU usage when idle

The key insight is that DMA blocking IS the pacing mechanism - no artificial delays are needed.

## Running Tests

### Prerequisites

1. Pluto SDR connected and accessible via network
2. Streamer running on the Pluto (`pluto_vita49_streamer`)
3. Python 3.6+ with standard library

### Basic Usage

```bash
# Run full test suite
python tests/test_rate_control.py --pluto 192.168.2.1

# Test specific sample rates
python tests/test_rate_control.py --pluto 192.168.2.1 --rates 5e6 10e6 20e6 30e6

# Quick test (2 seconds per rate)
python tests/test_rate_control.py --pluto 192.168.2.1 --quick

# Extended test (10 seconds per rate)
python tests/test_rate_control.py --pluto 192.168.2.1 --duration 10
```

## Expected Results

### Successful Test Output

```
############################################################
# VITA49 DMA-Paced Streamer Test Suite
############################################################
# Pluto IP: 192.168.2.1
# Test Duration: 5.0s per rate
# Sample Rates: ['5M', '10M', '20M', '30M']
# Architecture: 2-thread DMA-paced (refill blocks, then burst send)
############################################################

============================================================
Testing Sample Rate: 5.0 MSPS
============================================================
  Configuring Pluto to 5.0 MSPS...
  Collecting packets for 5.0s at 5.0 MSPS...
  Expected packet interval: 72.0 us

  Results:
    Packets received: 27778
    Total bytes: 10,000,080
    Throughput: 16.00 Mbps
    Packets/sec: 5556

  Timing:
    Expected interval: 72.0 us
    Actual avg interval: 180.0 us
    Std deviation: 850.0 us
    Min/Max: 2.0 / 2100.0 us

  Sequence Check:
    Drops: 0
    Duplicates: 0
    Drop rate: 0.00%

  [PASS] DMA streaming working correctly - no sample loss
```

### Expected Bandwidth by Sample Rate

| Sample Rate | Expected Throughput | Notes |
|-------------|---------------------|-------|
| 5 MSPS      | ~40 Mbps           | 5M * 4 bytes * 8 bits = 160 Mbps theoretical, ~25% efficiency typical |
| 10 MSPS     | ~80 Mbps           | Linear scaling from 5 MSPS |
| 20 MSPS     | ~160 Mbps          | Linear scaling |
| 30 MSPS     | ~240 Mbps          | Maximum rate for Gigabit Ethernet |

**Note**: Actual throughput depends on network conditions, packet overhead, and receiver processing speed.

### Understanding Timing Results

With DMA-paced architecture, timing will show:

- **High variance in intervals**: Packets arrive in bursts after each DMA refill
- **Min interval ~2 us**: Back-to-back packets within a burst
- **Max interval ~2000 us**: Gap between DMA refill cycles (~2ms at 30 MSPS)
- **This is NORMAL**: Focus on zero drops, not timing consistency

### Pass/Fail Criteria

The test checks:

1. **Drop Rate < 0.1%**: Critical - indicates sample loss if exceeded
2. **Throughput > 30% of theoretical**: Ensures data is actually flowing

## Interpreting Failures

### High Drop Rate

```
  Sequence Check:
    Drops: 150
    Duplicates: 0
    Drop rate: 0.54%

  [FAIL] Issues detected:
    - Drop rate too high: 0.540% (target: <0.1%)
```

**Possible causes:**
- Network congestion
- Receiver buffer overflow
- Pluto CPU overloaded

**Solutions:**
- Increase receiver socket buffer: `SO_RCVBUF`
- Check network path for congestion
- Reduce sample rate

### Low Throughput

```
  [FAIL] Issues detected:
    - Low throughput: 5.2 Mbps (expected ~160.0)
```

**Possible causes:**
- Streamer not running
- Wrong IP address
- Firewall blocking UDP traffic
- Sample rate misconfiguration

**Solutions:**
- Verify streamer is running: `ps aux | grep pluto_vita49`
- Check firewall rules
- Verify Pluto IP connectivity

## Bandwidth Scaling Summary

A successful test shows linear bandwidth scaling:

```
============================================================
Bandwidth Scaling Summary
============================================================

 Sample Rate   Expected Mbps   Actual Mbps    Ratio     Status
------------------------------------------------------------
       5.0 M         160.0          42.1       0.26         OK
      10.0 M         320.0          83.5       0.26         OK
      20.0 M         640.0         165.2       0.26         OK
      30.0 M         960.0         248.3       0.26         OK
```

**Status meanings:**
- **OK**: Ratio between 0.2 and 1.2 (normal)
- **WARN**: Ratio outside expected range but still functional
- **FAIL**: Ratio below 0.1 (severe problem)

## Final Summary

```
############################################################
# Final Summary
############################################################

  Tests Passed: 4/4
  Tests Failed: 0/4

  [OVERALL PASS] DMA streaming working correctly - no sample loss!
```

## Troubleshooting

### "Connection refused" or timeout

```bash
# Check if streamer is running on Pluto
ssh root@192.168.2.1 "ps aux | grep vita49"

# Check if port is open
nc -zvu 192.168.2.1 4991
```

### No packets received

1. Verify streamer is configured to send to your IP
2. Check firewall allows UDP 4991 inbound
3. Try running receiver on same network segment as Pluto

### Intermittent drops

Normal if drop rate < 0.1%. For stricter requirements:
- Use dedicated network (no other traffic)
- Increase socket buffer size
- Consider running receiver with elevated priority
