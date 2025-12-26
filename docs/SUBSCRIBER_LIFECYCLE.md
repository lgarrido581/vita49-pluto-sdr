# VITA49 Subscriber Lifecycle Documentation

## Overview

The VITA49 PlutSDR streamer implements intelligent subscriber management with automatic health tracking, error handling, and resource cleanup. This document describes the complete lifecycle of subscribers from registration through removal.

## Subscriber States

A subscriber can be in one of three states:

### 1. **Active** (Initial State)
- Subscriber is registered and receiving data
- `active = true`
- `consecutive_failures = 0`
- Packets are being successfully sent

### 2. **Failing** (Degraded State)
- Subscriber is experiencing send errors
- `active = true` (still trying to send)
- `consecutive_failures > 0` and `< MAX_CONSECUTIVE_FAILURES`
- Warning logged every 10 failures

### 3. **Inactive** (Terminal State)
- Subscriber has been marked for removal
- `active = false`
- `consecutive_failures >= MAX_CONSECUTIVE_FAILURES` OR timed out
- No longer receiving packets
- Will be removed on next cleanup cycle

## Subscriber Lifecycle Stages

### Stage 1: Registration

**Trigger:** Client sends VITA49 Context packet to control port (4990)

**Process:**
```c
1. Receive context packet on control socket
2. Parse configuration (frequency, sample rate, gain)
3. Extract client IP from incoming packet
4. Call add_subscriber() with client IP:4991 (data port)
5. Initialize subscriber structure:
   - addr: Client socket address
   - active: true
   - consecutive_failures: 0
   - packets_sent: 0
   - bytes_sent: 0
   - last_seen_us: current timestamp
   - first_seen_us: current timestamp
   - total_failures: 0
```

**Limits:**
- Maximum 16 simultaneous subscribers (`MAX_SUBSCRIBERS`)
- If already at limit, new subscriber is rejected with error message
- Existing inactive subscribers are reused before rejecting

**Reactivation:**
If a subscriber with the same IP:port already exists:
- If `active == false`: Reactivate it (reset `consecutive_failures`, set `active = true`)
- If `active == true`: Ignore (already subscribed)

### Stage 2: Active Streaming

**Behavior:**
- Subscriber receives VITA49 data packets on UDP port 4991
- Each successful `sendto()` updates subscriber health:
  - `consecutive_failures` reset to 0
  - `last_seen_us` updated to current timestamp
  - `packets_sent` incremented
  - `bytes_sent` incremented by packet size

**Health Tracking:**
```c
Per-subscriber metrics:
- packets_sent:          Total packets successfully sent
- bytes_sent:            Total bytes successfully sent
- consecutive_failures:  Current failure streak (0 if healthy)
- total_failures:        Lifetime failure count
- last_seen_us:          Timestamp of last successful send
- first_seen_us:         Timestamp when subscriber was added
```

**Context Packets:**
- Sent every 100 data packets (`CONTEXT_INTERVAL`)
- Contains current SDR configuration
- Sent to ALL active subscribers
- Also sent immediately after configuration changes

### Stage 3: Error Handling

**Failure Detection:**

When `sendto()` fails (returns < 0):
```c
1. Increment consecutive_failures
2. Increment total_failures
3. Increment global g_stats.send_failures

4. If consecutive_failures % 10 == 0:
   - Log warning with subscriber IP and failure counts

5. If consecutive_failures >= MAX_CONSECUTIVE_FAILURES (10):
   - Mark subscriber as inactive (active = false)
   - Log removal message
   - Subscriber will be removed on next cleanup
```

**Common Failure Causes:**
- Network unreachable (ENETUNREACH)
- Host unreachable (EHOSTUNREACH)
- Connection refused (ECONNREFUSED) - subscriber closed port
- No buffer space available (ENOBUFS) - network congestion
- Message too long (EMSGSIZE) - packet exceeds MTU

**Failure Thresholds:**
```c
#define MAX_CONSECUTIVE_FAILURES 10  /* Mark inactive after 10 failures */
```

With 30 MSPS and ~360 samples/packet, this equals:
- ~83 packets/second
- 10 failures = ~120 milliseconds before marking inactive

### Stage 4: Timeout Detection

**Timeout Mechanism:**

Even if `sendto()` succeeds (doesn't return error), a subscriber can timeout if no successful sends occur for an extended period.

**Timeout Check (in `cleanup_dead_subscribers()`):**
```c
if (current_time - sub->last_seen_us > SUBSCRIBER_TIMEOUT_US) {
    // 30 seconds without successful send
    // Remove subscriber
}
```

**Timeout Configuration:**
```c
#define SUBSCRIBER_TIMEOUT_US 30000000  /* 30 seconds */
```

**Timeout Scenarios:**
1. **Silent network failure:** Router drops packets, no ICMP errors
2. **Client paused:** Application stopped reading, buffer full, no errors
3. **Firewall changes:** Packets silently dropped by firewall

**Note:** `last_seen_us` is only updated on **successful** `sendto()` calls, not just attempts.

### Stage 5: Cleanup and Removal

**Cleanup Trigger:**

Cleanup runs periodically in the streaming thread:
```c
if (packets_sent % SUBSCRIBER_CLEANUP_INTERVAL == 0) {
    cleanup_dead_subscribers();
}
```

**Cleanup Interval:**
```c
#define SUBSCRIBER_CLEANUP_INTERVAL 100  /* Every 100 packets */
```

At 83 packets/second, cleanup runs every ~1.2 seconds.

**Cleanup Process:**

1. **Lock subscriber list** (pthread_mutex)

2. **Iterate through all subscribers**, checking:
   - If `active == false` → Mark for removal
   - If `(current_time - last_seen_us) > SUBSCRIBER_TIMEOUT_US` → Mark for removal

3. **Compact array** (remove gaps):
   ```c
   write_idx = 0
   for each subscriber:
       if should_remove:
           skip (don't copy)
       else:
           subscribers[write_idx++] = subscribers[read_idx]
   subscriber_count = write_idx
   ```

4. **Log removal** if any subscribers removed

5. **Unlock subscriber list**

**Array Compaction Example:**
```
Before: [A, B(dead), C, D(dead), E]  count=5
After:  [A, C, E, ?, ?]              count=3
```

Freed slots (?, ?) are available for new subscribers.

## Subscriber Statistics

### Per-Subscriber Statistics

Available via main() monitoring loop (printed every 5 seconds):

```
[Subscribers] Active: 3/16
  [0] 192.168.1.100:4991 - Pkts: 125847, Fails: 0/0, Uptime: 152s
  [1] 192.168.1.101:4991 - Pkts: 125840, Fails: 2/15, Uptime: 151s
  [2] 192.168.1.102:4991 - Pkts: 98234, Fails: 0/2, Uptime: 118s
```

**Fields:**
- **Index:** Subscriber position in array
- **IP:Port:** Subscriber address
- **Pkts:** Total packets successfully sent to this subscriber
- **Fails:** `consecutive_failures` / `total_failures`
- **Uptime:** Time since subscriber was added (seconds)

### Global Statistics

```
[Stats] Packets: 1258470, Bytes: 4821 MB, Contexts: 12584, Subs: 3
[Health] Underflows: 0, Overflows: 0, Refill Fails: 0, TS Jumps: 0
```

**send_failures:** Available in `g_stats.send_failures`, tracks total send errors across all subscribers.

## Configuration Parameters

All subscriber management parameters can be tuned in `pluto_vita49_streamer.c`:

### Subscriber Limits
```c
#define MAX_SUBSCRIBERS 16
```
Maximum simultaneous subscribers. Limited by:
- Memory (~200 bytes per subscriber)
- UDP send performance
- Network bandwidth

### Failure Threshold
```c
#define MAX_CONSECUTIVE_FAILURES 10
```
Number of consecutive `sendto()` failures before marking inactive.

**Tuning:**
- **Lower (5):** Faster removal, less tolerance for transient errors
- **Higher (20):** More tolerance, slower removal
- **Recommended:** 10 (good balance for ~120ms detection)

### Timeout Period
```c
#define SUBSCRIBER_TIMEOUT_US 30000000  /* 30 seconds */
```
Microseconds without successful send before timeout removal.

**Tuning:**
- **Lower (10s):** Faster cleanup, less tolerance for slow networks
- **Higher (60s):** More tolerance, slower cleanup
- **Recommended:** 30s (good balance for network issues)

### Cleanup Interval
```c
#define SUBSCRIBER_CLEANUP_INTERVAL 100  /* Check every 100 packets */
```
How often (in packets) to run cleanup.

**Tuning:**
- **Lower (50):** More frequent cleanup, higher CPU usage
- **Higher (500):** Less frequent cleanup, slower removal
- **Recommended:** 100 (cleanup every ~1.2 seconds)

## Reactivation Mechanism

When a subscriber that was previously marked inactive or removed sends a new configuration:

```c
// In add_subscriber():
for each existing subscriber:
    if IP:port matches:
        if inactive:
            // REACTIVATE
            active = true
            consecutive_failures = 0
            log "Reactivated subscriber"
        return
```

**Reactivation Scenarios:**
1. **Application restart:** Client crashed and restarted
2. **Network recovery:** Network was down, now restored
3. **Port reuse:** Same client reconnecting after timeout

**Reactivation Benefits:**
- Maintains subscriber slot (no need to find new slot)
- Preserves historical statistics (total_failures, first_seen_us)
- Instant recovery without waiting for cleanup

## Best Practices

### For Streamer Operators

1. **Monitor subscriber health:**
   - Watch for high `consecutive_failures`
   - Check for frequent removals
   - Monitor `g_stats.send_failures`

2. **Network optimization:**
   - Use wired connections when possible
   - Ensure sufficient bandwidth (30 MSPS ≈ 120 Mbps)
   - Avoid network congestion

3. **Capacity planning:**
   - 16 subscribers × 120 Mbps = ~2 Gbps theoretical max
   - Consider network interface limits
   - Monitor for ENOBUFS errors (buffer exhaustion)

### For Client Applications

1. **Handle disconnections gracefully:**
   - Detect packet loss (missing sequence numbers)
   - Resend configuration to reactivate
   - Don't assume permanent connection

2. **Monitor receive rate:**
   - Expected: ~83 packets/second (30 MSPS, 360 samples/packet)
   - Alert if rate drops significantly
   - Check for context packets (every 100 data packets)

3. **Implement keepalive:**
   - Send periodic configuration updates (< 30s intervals)
   - Prevents timeout removal during quiet periods
   - Alternative: Monitor packet timestamps

## Troubleshooting

### Subscriber not receiving data

**Symptoms:** Client registered but packets_sent = 0

**Causes:**
1. **Firewall blocking:** Check client firewall on port 4991/UDP
2. **Wrong IP:** Verify streamer has route to client IP
3. **Inactive subscriber:** Check if marked inactive due to failures

**Solutions:**
```bash
# Check firewall
sudo iptables -L -n | grep 4991

# Test UDP connectivity
nc -u -l 4991  # On client
echo "test" | nc -u CLIENT_IP 4991  # From streamer

# Check subscriber status in streamer logs
[Subscribers] Active: X/16
  [N] IP:4991 - Pkts: 0, Fails: X/Y, Uptime: Zs
```

### Subscriber repeatedly removed

**Symptoms:** Subscriber added, then quickly removed and re-added

**Causes:**
1. **Network instability:** Packet loss causing sendto() failures
2. **Client buffer overflow:** Client not reading fast enough
3. **MTU issues:** Packets too large, fragmentation/drops

**Solutions:**
```bash
# Check MTU
ip link show  # Verify MTU >= 1500

# Monitor packet loss
# On client:
tcpdump -i any -n udp port 4991 -c 1000 | grep "ICMP"

# Adjust failure threshold (in code):
#define MAX_CONSECUTIVE_FAILURES 20  /* More tolerant */

# Or reduce timeout:
#define SUBSCRIBER_TIMEOUT_US 10000000  /* 10 seconds */
```

### Hit subscriber limit (16)

**Symptoms:** "ERROR: Maximum subscribers reached"

**Causes:**
1. **Dead subscribers not cleaned up:** Inactive but not timed out
2. **Slow cleanup:** Long timeout periods
3. **Genuine limit:** Actually have 16 active subscribers

**Solutions:**
```bash
# Check subscriber statistics
[Subscribers] Active: 16/16
  # Look for subscribers with very low Pkts count or high Fails

# Manual cleanup (restart streamer)
sudo systemctl restart vita49_streamer

# Increase limit (in code):
#define MAX_SUBSCRIBERS 32  /* Warning: more memory/CPU */

# Faster cleanup (in code):
#define SUBSCRIBER_TIMEOUT_US 10000000     /* 10s instead of 30s */
#define SUBSCRIBER_CLEANUP_INTERVAL 50     /* Check every 50 packets */
```

## Security Considerations

### Denial of Service (DoS)

**Attack Vector:** Attacker sends 16 config packets from different IPs, filling subscriber slots.

**Mitigations:**
1. **Firewall:** Restrict control port (4990) to known subnets
2. **Authentication:** Add HMAC or signature to context packets (future)
3. **Rate limiting:** Limit config packets per IP (future)

### Resource Exhaustion

**Attack Vector:** Attacker causes high send failure rates, consuming CPU in error handling.

**Mitigations:**
1. **Failure limiting:** Subscribers marked inactive quickly (10 failures)
2. **Cleanup limiting:** Runs periodically, not on every failure
3. **Logging limiting:** Only logs every 10th failure

## Future Enhancements

1. **Heartbeat mechanism:** Explicit keepalive packets from clients
2. **Priority levels:** Guarantee bandwidth to certain subscribers
3. **Statistics API:** Query subscriber health via HTTP/REST
4. **Dynamic limits:** Adjust MAX_SUBSCRIBERS based on bandwidth
5. **Selective multicast:** Group subscribers by configuration
6. **Persistent subscribers:** Remember subscribers across restarts

## Summary

The subscriber lifecycle ensures:
- ✅ **Automatic registration** via config packets
- ✅ **Health tracking** with per-subscriber statistics
- ✅ **Error tolerance** with configurable failure threshold
- ✅ **Automatic cleanup** of dead subscribers
- ✅ **Resource management** with 16-subscriber limit
- ✅ **Reactivation support** for recovered clients
- ✅ **Detailed monitoring** via statistics output

This design provides robust, production-ready subscriber management for the VITA49 PlutSDR streamer.
