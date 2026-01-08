# TROUBLESHOOTING GUIDE - VITA49 Pluto Streamer

This guide documents common issues, debugging steps, and solutions for the VITA49 Pluto Streamer, especially during multicore optimization development.

## Table of Contents

- [General Issues](#general-issues)
- [Multicore Optimization Issues](#multicore-optimization-issues)
- [Performance Issues](#performance-issues)
- [Memory and Threading Issues](#memory-and-threading-issues)
- [Build and Deployment Issues](#build-and-deployment-issues)
- [Debugging Tools](#debugging-tools)

---

## General Issues

### Issue: Pluto Not Responding
**Symptoms**: Cannot connect to Pluto, timeouts on SSH/SCP
**Solutions**:
1. Check network connectivity: `ping pluto.local` or `ping 192.168.2.1`
2. Verify Pluto is powered and booted (LED solid)
3. Reset network interface: `sudo ifconfig eth0 down && sudo ifconfig eth0 up`
4. Try different IP: Sometimes Pluto uses `192.168.3.1` instead

### Issue: "Device not found" Error
**Symptoms**: `cf-ad9361-lpc device not found`
**Root Cause**: IIO context initialization failure
**Solutions**:
1. Verify Pluto firmware supports libiio: `ssh root@pluto.local 'ls /usr/lib/libiio*'`
2. Check device list: `ssh root@pluto.local 'iio_info -s'`
3. Restart IIO daemon: `ssh root@pluto.local '/etc/init.d/iiod restart'`

### Issue: Permission Denied on Deployment
**Symptoms**: SCP fails with permission errors
**Solutions**:
1. Verify SSH access: `ssh root@pluto.local` (password: `analog`)
2. Check binary permissions: `chmod +x vita49_streamer`
3. Try explicit password auth: `scp -o PreferredAuthentications=password vita49_streamer root@pluto.local:/root/`

---

## Multicore Optimization Issues

### Issue: Ring Buffer Corruption (Phase 1)
**Symptoms**: Segmentation faults, data corruption, invalid packet headers
**Root Cause**: Race conditions in lock-free ring buffer
**Debug Steps**:
1. Enable debug build: `make clean && make cross CFLAGS="-g -O0 -DDEBUG"`
2. Check atomic operations: Verify `memory_order_acquire/release` usage
3. Validate buffer indices: Add bounds checking in debug mode
**Prevention**:
- Always use power-of-2 buffer sizes for efficient modulo operations
- Use proper memory barriers (`atomic_thread_fence()` if needed)
- Test with stress testing: rapid producer/consumer cycles

### Issue: DMA Thread Starvation (Phase 2)  
**Symptoms**: High packet loss, irregular timing, `iio_buffer_refill()` timeouts
**Root Cause**: Incorrect CPU affinity or thread priorities
**Debug Steps**:
1. Verify CPU affinity: `cat /proc/PID/task/*/stat | awk '{print $2, $39}'`
2. Check thread priorities: `ps -eTo pid,lwp,pri,ni,rtprio,cls,comm | grep vita49`
3. Monitor DMA performance: Add timing logs around `iio_buffer_refill()`
**Solutions**:
- Ensure DMA thread has `SCHED_FIFO` priority 90+
- Pin to Core 0 only: `CPU_SET(0, &cpuset)`
- Disable kernel load balancing during runtime

### Issue: Network Thread Overload (Phase 3)
**Symptoms**: High CPU on Core 1, packet drops, sendmmsg() failures  
**Root Cause**: Core 1 handling too many responsibilities
**Debug Steps**:
1. Profile Core 1 load: Split timing between VITA49 encoding vs network TX
2. Monitor ring buffer depth: Track producer/consumer rates
3. Check socket buffer utilization: `ss -u -a | grep 4991`
**Solutions**:
- Increase socket send buffer: `setsockopt(SO_SNDBUF, 8MB)`
- Batch larger groups: Increase `SEND_BATCH_SIZE` from 64 to 128
- Consider moving config handling to separate thread if overloaded

### Issue: Memory Leaks in Ring Buffer
**Symptoms**: Gradual memory increase, eventual OOM killer
**Root Cause**: Incorrect buffer pool management
**Debug Steps**:
1. Track memory usage: `watch -n1 'cat /proc/meminfo | grep Available'`
2. Use valgrind (if available): `valgrind --leak-check=full ./vita49_streamer`
3. Add memory tracking: Count malloc/free calls in debug build
**Prevention**:
- Use static allocation for ring buffers (avoid malloc/free)
- Implement buffer pool with fixed-size pre-allocated blocks
- Add memory usage monitoring to stats output

---

## Performance Issues

### Issue: Lower Than Expected Throughput
**Symptoms**: <400 Mbps despite dual-core optimization
**Debug Steps**:
1. Check per-core utilization: `top -p PID -H` (show threads)
2. Monitor ring buffer efficiency: Track full/empty events
3. Profile network stack: `tcpdump -i eth0 -c 1000 port 4991`
**Common Causes**:
- CPU governor not set to 'performance'
- Network IRQs not pinned to Core 1  
- Inefficient VITA49 encoding (avoid byte-by-byte operations)
- Socket buffer too small for burst sending

### Issue: High Latency Despite Optimization
**Symptoms**: >1ms DMA-to-network latency
**Debug Steps**:
1. Add timestamps at each pipeline stage
2. Check ring buffer depth: High depth = producer faster than consumer
3. Monitor context switches: `pidstat -w 1`
**Solutions**:
- Reduce ring buffer size if over-buffering
- Increase network thread priority
- Consider real-time kernel patches for Pluto

### Issue: Packet Loss at High Sample Rates
**Symptoms**: Dropped packets at 30+ MSPS, sequence number gaps
**Debug Steps**:
1. Check which stage drops packets: DMA, ring buffer, or network
2. Monitor socket errors: Track `sendmmsg()` return values
3. Verify MTU settings: Ensure packets fit in MTU
**Solutions**:
- Increase ring buffer depth (more pre-allocated buffers)
- Tune network stack: `echo 8388608 > /proc/sys/net/core/wmem_max`
- Consider packet aggregation: Multiple VITA49 packets per UDP datagram

---

## Memory and Threading Issues

### Issue: Segmentation Faults on Startup
**Symptoms**: Crash during thread creation or initialization
**Debug Steps**:
1. Enable core dumps: `ulimit -c unlimited`
2. Use GDB: `gdb ./vita49_streamer core`
3. Check stack size: `ulimit -s` (should be >8MB for libiio)
**Common Causes**:
- Stack overflow in thread initialization
- Invalid IIO context (hardware not ready)
- Race condition in global variable initialization

### Issue: Deadlocks Between Threads
**Symptoms**: Threads hang, no packet transmission, high CPU but no progress
**Debug Steps**:
1. Attach debugger: `gdb -p PID` then `info threads`
2. Check lock contention: Look for threads waiting on mutexes
3. Add lock debugging: Time how long mutexes are held
**Prevention**:
- Minimize shared state between cores
- Use lock-free structures where possible (atomic operations)
- Always acquire locks in consistent order

### Issue: Cache Thrashing Between Cores  
**Symptoms**: Lower performance than single-core despite distribution
**Debug Steps**:
1. Monitor cache misses: `perf stat -e cache-misses ./vita49_streamer` (if available)
2. Check data sharing: Ensure ring buffer entries are cache-line aligned
3. Profile memory access patterns
**Solutions**:
- Align ring buffer to cache line boundaries (64 bytes)
- Use separate data structures per core
- Consider NUMA-aware memory allocation

---

## Build and Deployment Issues

### Issue: Cross-Compilation Fails
**Symptoms**: `arm-linux-gnueabihf-gcc: command not found`
**Solutions**:
1. Install cross-compiler: `sudo apt install gcc-arm-linux-gnueabihf`
2. Use Docker build: `./scripts/build-with-docker.sh`
3. Verify toolchain: `arm-linux-gnueabihf-gcc --version`

### Issue: Runtime Library Errors on Pluto
**Symptoms**: `error while loading shared libraries: libiio.so.0`
**Solutions**:
1. Check library path: `ssh root@pluto.local 'ldconfig -p | grep iio'`
2. Install missing libs: `ssh root@pluto.local 'opkg update && opkg install libiio0'`
3. Verify binary architecture: `file vita49_streamer` (should show ARM)

---

## Debugging Tools

### Enable Debug Build
```bash
# Add debug symbols and enable debug prints
make clean
make cross CFLAGS="-g -O0 -DDEBUG -DMULTICORE_DEBUG"
```

### Real-time Monitoring
```bash
# Monitor CPU usage per core
watch -n1 'grep "cpu[01]" /proc/stat'

# Monitor memory usage  
watch -n1 'cat /proc/meminfo | grep -E "(MemTotal|MemAvailable|Buffers|Cached)"'

# Monitor network statistics
watch -n1 'ss -u -a | grep 499'
```

### Performance Profiling
```bash
# Check thread affinity (on Pluto)
for tid in $(ps -T -p $(pgrep vita49_streamer) -o lwp= ); do
    echo "Thread $tid: CPU $(cat /proc/$tid/stat | awk '{print $39}')"
done

# Monitor context switches
pidstat -w 1 $(pgrep vita49_streamer)

# Check IRQ distribution
cat /proc/interrupts | grep eth0
```

### Memory Debugging
```bash
# Track memory usage over time
while true; do
    echo "$(date): $(cat /proc/meminfo | grep MemAvailable)" >> memory_log.txt
    sleep 1
done

# Check for memory leaks (basic)
ps -o pid,vsz,rss,comm | grep vita49_streamer
```

---

## Reporting Issues

When reporting issues, please include:

1. **Branch and commit**: `git log --oneline -1`
2. **Build info**: How was it compiled (native, cross, Docker)
3. **Hardware**: Pluto firmware version, host OS
4. **Logs**: Full output with debug enabled
5. **Reproduction steps**: Exact commands used
6. **Performance data**: CPU usage, memory usage, packet rates

### Log Format for Bug Reports
```
Date: 2025-01-08
Branch: feature/multicore-optimization  
Commit: abc1234
Build: Docker cross-compilation
Pluto FW: v0.38
Host: Ubuntu 22.04

Issue: Ring buffer corruption after 30 seconds
Reproduction: ./vita49_streamer --rate 30e6
Logs: [attach full debug output]
CPU Usage: Core0: 95%, Core1: 45%
```

---

## Known Issues (Current Development)

### Phase 1 Issues
- **Ring buffer size**: Must be power of 2 for efficient modulo
- **Memory alignment**: Ring buffer should be cache-line aligned
- **Atomic operations**: Some ARM versions need explicit memory barriers

### Phase 2 Issues  
- **DMA thread priority**: Requires root privileges for SCHED_FIFO
- **CPU isolation**: May conflict with system processes

### Phase 3 Issues
- **Network stack tuning**: Default socket buffers may be too small
- **IRQ affinity**: Requires root access to /proc/irq/*/smp_affinity

---

*This document will be updated as new issues are discovered and resolved during development.*