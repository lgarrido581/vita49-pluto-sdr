# VITA49 Subscriber Management Troubleshooting Guide

## Quick Diagnosis

Use this flowchart to quickly identify and resolve subscriber issues:

```
Subscriber not receiving data?
│
├─ Subscriber listed in [Subscribers]?
│  ├─ NO → Registration problem (Section 1)
│  └─ YES → Continue
│
├─ Pkts count increasing?
│  ├─ NO → Send failures (Section 2)
│  └─ YES → Client problem (Section 5)
│
├─ Fails count increasing?
│  ├─ YES → Network/connectivity issues (Section 3)
│  └─ NO → Continue
│
└─ Subscriber disappearing?
   ├─ YES → Timeout or cleanup issues (Section 4)
   └─ NO → Performance issues (Section 6)
```

---

## Section 1: Registration Problems

### Problem: Subscriber not appearing in list

**Symptoms:**
- Client sends config packet
- No entry in `[Subscribers]` output
- No "Added subscriber" log message

**Diagnostic Steps:**

1. **Verify control port is reachable:**
```bash
# On client machine:
nc -vuz STREAMER_IP 4990

# On streamer machine:
sudo netstat -ulnp | grep 4990
```

Expected output:
```
udp  0  0  0.0.0.0:4990  0.0.0.0:*  PID/vita49_streamer
```

2. **Check firewall rules:**
```bash
# On streamer:
sudo iptables -L INPUT -n | grep 4990

# Should show ACCEPT or no rule (default ACCEPT)
```

3. **Verify config packet format:**
```bash
# Capture on streamer:
sudo tcpdump -i any -n 'udp port 4990' -X -c 1

# Look for:
# - Packet size >= 28 bytes (minimum context packet)
# - Header byte 1 = 0x4X (context packet type)
```

4. **Check subscriber limit:**
```
[Subscribers] Active: 16/16
```

If showing `16/16`, all slots are full.

**Solutions:**

- **Firewall blocking:**
```bash
sudo iptables -I INPUT -p udp --dport 4990 -j ACCEPT
sudo iptables -I INPUT -p udp --dport 4991 -j ACCEPT
```

- **Subscriber limit reached:**
  - Wait for inactive subscribers to be cleaned up (~30s)
  - Or restart streamer: `sudo systemctl restart vita49_streamer`
  - Or increase limit in code: `#define MAX_SUBSCRIBERS 32`

- **Invalid packet format:**
  - Use provided client libraries
  - Verify VITA49 context packet structure
  - Check byte ordering (network byte order/big-endian)

---

## Section 2: Send Failures

### Problem: Subscriber registered but Pkts = 0

**Symptoms:**
```
[Subscribers] Active: 1/16
  [0] 192.168.1.100:4991 - Pkts: 0, Fails: 10/10, Uptime: 2s
```

**Diagnostic Steps:**

1. **Check streamer logs for send errors:**
```
[Streaming] WARNING: Send to 192.168.1.100:4991 failed 10 times (total: 10)
[Streaming] Marking subscriber 192.168.1.100:4991 as inactive after 10 failures
```

2. **Identify error type:**
```bash
# Run streamer in foreground to see errors:
./build/vita49_streamer

# Common errors:
# ENETUNREACH (101) - No route to host
# EHOSTUNREACH (113) - Host unreachable
# ECONNREFUSED (111) - Port not listening
# ENOBUFS (105) - No buffer space available
```

3. **Test UDP connectivity:**
```bash
# On client (terminal 1):
nc -u -l 4991

# On streamer:
echo "test" | nc -u CLIENT_IP 4991

# Should see "test" on client terminal
```

4. **Verify routing:**
```bash
# On streamer:
ip route get CLIENT_IP

# Should show valid route, not "unreachable"
```

**Solutions:**

- **ENETUNREACH / EHOSTUNREACH:**
```bash
# Add route:
sudo ip route add CLIENT_SUBNET/24 via GATEWAY_IP

# Or check default gateway:
ip route show default
```

- **ECONNREFUSED:**
```bash
# On client, verify port is open:
sudo netstat -ulnp | grep 4991

# If not, start client receiver first before registering
```

- **ENOBUFS:**
```bash
# Increase UDP buffer size:
sudo sysctl -w net.core.wmem_max=26214400
sudo sysctl -w net.core.wmem_default=26214400

# Make permanent:
echo "net.core.wmem_max=26214400" | sudo tee -a /etc/sysctl.conf
echo "net.core.wmem_default=26214400" | sudo tee -a /etc/sysctl.conf
```

---

## Section 3: Intermittent Failures

### Problem: Subscriber works but has increasing Fails count

**Symptoms:**
```
[Subscribers] Active: 1/16
  [0] 192.168.1.100:4991 - Pkts: 12584, Fails: 3/157, Uptime: 152s
```

Failures happening but subscriber still active (consecutive failures reset).

**Diagnostic Steps:**

1. **Check for packet loss:**
```bash
# On client:
sudo tcpdump -i any -n 'udp port 4991' -c 1000 | wc -l

# Should see ~83 packets/second (30 MSPS, 360 samples/packet)
# If much lower, packets are being lost
```

2. **Monitor for ICMP errors:**
```bash
# On streamer:
sudo tcpdump -i any -n 'icmp and (icmp[0] = 3)' -v

# Look for "Destination Unreachable" messages
```

3. **Check network quality:**
```bash
# Ping test:
ping -c 100 CLIENT_IP

# Look for:
# - Packet loss percentage
# - High latency variance (jitter)
```

4. **Verify MTU:**
```bash
# On both streamer and client:
ip link show | grep mtu

# Should be 1500 or higher
# Packet size with standard MTU: ~1472 bytes
```

**Solutions:**

- **Wireless interference:**
  - Use wired connection if possible
  - Change WiFi channel to less congested one
  - Reduce distance to access point

- **Network congestion:**
```bash
# Check interface statistics:
ip -s link show eth0

# Look for:
# - RX/TX errors
# - Dropped packets
# - Overruns

# Reduce bandwidth if needed (lower sample rate):
# Send config with lower sample rate (e.g., 10 MSPS instead of 30 MSPS)
```

- **MTU mismatch:**
```bash
# Set MTU to 1500 on all interfaces:
sudo ip link set eth0 mtu 1500

# Or use --mtu flag when starting streamer:
./build/vita49_streamer --mtu 1472
```

---

## Section 4: Subscriber Disappearing

### Problem: Subscriber removed unexpectedly

**Symptoms:**
```
[Streaming] Removing subscriber 192.168.1.100:4991 (timeout)
[Streaming] Removed 1 dead subscriber(s), 3 active remain
```

**Diagnostic Steps:**

1. **Check timeout setting:**
```c
// In pluto_vita49_streamer.c:
#define SUBSCRIBER_TIMEOUT_US 30000000  /* 30 seconds */
```

2. **Verify last_seen_us is updating:**
```bash
# Enable debug logging (modify code temporarily):
printf("Sub last_seen: %llu, current: %llu, delta: %llu\n",
       sub->last_seen_us, current_time, current_time - sub->last_seen_us);
```

3. **Check for consecutive failures:**
```
[Streaming] Marking subscriber 192.168.1.100:4991 as inactive after 10 failures
```

**Root Causes:**

1. **Timeout (30s no successful sends):**
   - Client not receiving (firewall changed)
   - Silent packet loss (no ICMP errors)
   - Client buffer full (not reading)

2. **Consecutive failures (10 in a row):**
   - Network path failed
   - Client port closed
   - Routing changed

**Solutions:**

- **Increase timeout tolerance:**
```c
// In code:
#define SUBSCRIBER_TIMEOUT_US 60000000  /* 60 seconds */
```

- **Increase failure tolerance:**
```c
// In code:
#define MAX_CONSECUTIVE_FAILURES 20  /* 20 failures before inactive */
```

- **Implement client keepalive:**
```python
# Client should periodically resend config (< 30s):
import time
while True:
    send_config(streamer_ip)
    time.sleep(20)  # Resend every 20 seconds
```

---

## Section 5: Client Not Receiving Data

### Problem: Subscriber shows Pkts increasing but client sees nothing

**Symptoms:**
- Streamer shows: `Pkts: 125847, Fails: 0/0`
- Client receives 0 packets

**This is a CLIENT-side problem, not streamer problem.**

**Diagnostic Steps:**

1. **Verify client is listening:**
```bash
# On client:
sudo netstat -ulnp | grep 4991

# Should show:
# udp  0  0  0.0.0.0:4991  0.0.0.0:*  PID/your_app
```

2. **Check client firewall:**
```bash
# On client:
sudo iptables -L INPUT -n | grep 4991

# Should show ACCEPT or no rule
```

3. **Capture packets at client:**
```bash
# On client:
sudo tcpdump -i any -n 'udp port 4991' -c 10 -v

# Should see packets arriving from STREAMER_IP
```

4. **Check receive buffer:**
```bash
# On client:
sudo sysctl net.core.rmem_max
sudo sysctl net.core.rmem_default

# Should be >= 1MB for 30 MSPS
```

**Solutions:**

- **Client not listening:**
  - Start client application
  - Bind to correct port (4991)
  - Use `0.0.0.0` not `127.0.0.1`

- **Client firewall:**
```bash
# On client:
sudo iptables -I INPUT -p udp --dport 4991 -j ACCEPT
```

- **Receive buffer too small:**
```bash
# On client:
sudo sysctl -w net.core.rmem_max=26214400
sudo sysctl -w net.core.rmem_default=26214400
```

- **Application not reading fast enough:**
```python
# Increase socket buffer:
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 26214400)

# Read in loop continuously:
while True:
    data, addr = sock.recvfrom(65536)
    process_packet(data)  # Don't block here!
```

---

## Section 6: Performance Issues

### Problem: Packet loss or gaps in data

**Symptoms:**
- Missing sequence numbers in VITA49 packets
- Underflow warnings in streamer logs
- Lower than expected packet rate

**Diagnostic Steps:**

1. **Check streamer health:**
```
[Health] Underflows: 15, Overflows: 0, Refill Fails: 0, TS Jumps: 12
```

If underflows > 0, streamer is not keeping up.

2. **Check loop timing:**
```
[Timing] Loop: avg=8432.1 us, min=7821 us, max=45621 us
```

If max is very high, streamer has timing jitter.

3. **Check CPU usage:**
```bash
top -p $(pgrep vita49_streamer)

# CPU should be < 80% on single core
```

4. **Check network bandwidth:**
```bash
iftop -i eth0

# For 30 MSPS with 16 subscribers:
# Expected: ~1.9 Gbps (120 Mbps per subscriber)
```

**Solutions:**

- **High underflows:**
```bash
# Reduce sample rate:
# Send config with sample_rate_hz = 10000000  # 10 MSPS

# Or reduce subscribers (max bandwidth)
```

- **High loop timing variance:**
```bash
# Run streamer with higher priority:
sudo nice -n -10 ./build/vita49_streamer

# Or use real-time scheduling:
sudo chrt -f 50 ./build/vita49_streamer
```

- **Network bandwidth limit:**
```bash
# Check interface speed:
ethtool eth0 | grep Speed

# For 16 subscribers at 30 MSPS, need >= 2 Gbps
# Use fewer subscribers or lower sample rate
```

---

## Common Error Messages

### "[Control] ERROR: Maximum subscribers reached (16)"

**Cause:** All 16 subscriber slots are full.

**Solutions:**
1. Wait for inactive subscribers to be cleaned up (~30s)
2. Check subscriber list for dead subscribers with Pkts = 0
3. Restart streamer to clear all subscribers
4. Increase MAX_SUBSCRIBERS in code (recompile required)

---

### "[Streaming] WARNING: Send to X.X.X.X:4991 failed N times"

**Cause:** `sendto()` system call failed N times.

**Solutions:**
1. Check network connectivity to X.X.X.X
2. Verify client is listening on port 4991
3. Check for firewall rules blocking traffic
4. Verify routing table has path to X.X.X.X

---

### "[Streaming] Marking subscriber X.X.X.X:4991 as inactive after 10 failures"

**Cause:** 10 consecutive `sendto()` failures.

**Solutions:**
1. Fix network connectivity (see Section 2)
2. Subscriber will be removed on next cleanup
3. Client can resend config to reactivate (if issue fixed)

---

### "[Streaming] Removing subscriber X.X.X.X:4991 (timeout)"

**Cause:** No successful sends for 30+ seconds.

**Solutions:**
1. Fix network connectivity
2. Increase SUBSCRIBER_TIMEOUT_US if needed
3. Implement client keepalive (resend config periodically)

---

### "[Streaming] ERROR: Buffer refill failed"

**Cause:** libiio buffer refill failed (SDR issue, not subscriber issue).

**Solutions:**
1. Check PlutoSDR connection (USB or network)
2. Verify PlutoSDR is powered and booted
3. Check iio_info output
4. Restart PlutoSDR

---

## Monitoring Best Practices

### 1. Watch subscriber statistics

```bash
# Every 5 seconds, check:
[Subscribers] Active: X/16

# Look for:
# - Subscribers with Pkts = 0 (send failures)
# - Subscribers with high Fails count (network issues)
# - Subscribers disappearing (timeouts)
```

### 2. Monitor global health

```bash
[Health] Underflows: X, Overflows: Y, Refill Fails: Z, TS Jumps: W

# All should be 0 or very low
# If increasing, streamer is unhealthy
```

### 3. Track send failures

```bash
# Add to monitoring loop (modify code):
pthread_mutex_lock(&g_stats.mutex);
printf("[Debug] Total send failures: %llu\n", g_stats.send_failures);
pthread_mutex_unlock(&g_stats.mutex);

# Should be 0 or very low
# If increasing rapidly, network issues
```

### 4. Use logs effectively

```bash
# Run with output to file:
./build/vita49_streamer 2>&1 | tee vita49.log

# Search for issues:
grep "WARNING" vita49.log
grep "ERROR" vita49.log
grep "Removing subscriber" vita49.log
```

---

## Testing Checklist

Before deploying to production, verify:

- [ ] Single subscriber receives data reliably
- [ ] Multiple subscribers (2-3) receive data simultaneously
- [ ] Subscriber survives network hiccup (1-2 packet loss)
- [ ] Dead subscriber is removed within 2 minutes
- [ ] New subscriber can join after old one removed
- [ ] Reactivation works (stop client, restart, resend config)
- [ ] Full capacity test (16 subscribers)
- [ ] Streamer survives client crashes (no memory leaks)
- [ ] Streamer survives network outages (recovers when back)

---

## Advanced Debugging

### Enable verbose logging

```c
// In send_to_subscriber(), log every send:
fprintf(stderr, "[DEBUG] Send to %s: %zd bytes, result=%zd, errno=%d\n",
        ip_str, len, sent, errno);
```

### Monitor with strace

```bash
# Trace sendto calls:
sudo strace -p $(pgrep vita49_streamer) -e trace=sendto -f 2>&1 | grep sendto

# Look for:
# - sendto(...) = -1 ENETUNREACH (Network is unreachable)
# - sendto(...) = 1472 (success)
```

### Capture traffic

```bash
# On streamer:
sudo tcpdump -i any -n 'udp port 4991' -w capture.pcap

# Analyze:
tcpdump -r capture.pcap -n | head -20
wireshark capture.pcap  # GUI analysis
```

### Memory debugging

```bash
# Check for memory leaks:
valgrind --leak-check=full ./build/vita49_streamer

# Monitor memory usage:
watch -n 1 'pmap $(pgrep vita49_streamer) | tail -1'
```

---

## Getting Help

If you've tried everything and still have issues:

1. **Collect diagnostics:**
```bash
# System info
uname -a
ip addr show
ip route show

# Streamer info
./build/vita49_streamer --help
ldd ./build/vita49_streamer

# Network info
sudo netstat -ulnp | grep vita49
sudo iptables -L -n

# Logs
tail -100 vita49.log
```

2. **Reproduce issue:**
   - Document exact steps to trigger problem
   - Note timing (immediate, after 30s, random, etc.)
   - Capture any error messages

3. **Open issue:**
   - https://github.com/lgarrido581/vita49-pluto-sdr/issues
   - Include all diagnostic info
   - Describe expected vs actual behavior

---

## Summary

Most subscriber issues fall into these categories:

1. **Registration:** Firewall, network connectivity, subscriber limit
2. **Send failures:** Routing, client not listening, buffer exhaustion
3. **Intermittent failures:** Packet loss, network quality, MTU issues
4. **Timeouts:** Client gone, silent failures, need keepalive
5. **Client-side:** Firewall, not listening, buffer too small
6. **Performance:** Too many subscribers, CPU limit, bandwidth limit

Use the diagnostic steps and solutions in this guide to systematically identify and resolve issues.
