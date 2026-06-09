# LibIIO Multi-Core ARM Development Guide

**Building High-Performance Signal Processing Applications on ADALM-Pluto**

This guide details how to build multi-core, multi-threaded applications on the ADALM-Pluto ARM processor using the LibIIO stack. It's based on the battle-tested VITA49 streamer implementation and provides patterns for building applications like target emulators, delay lines, and real-time signal processors.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [LibIIO Stack Fundamentals](#libiio-stack-fundamentals)
3. [Multi-Core Threading Architecture](#multi-core-threading-architecture)
4. [Lock-Free Ring Buffer Implementation](#lock-free-ring-buffer-implementation)
5. [DMA Operations and Memory Management](#dma-operations-and-memory-management)
6. [RX Path: Capturing Samples](#rx-path-capturing-samples)
7. [TX Path: Transmitting Samples](#tx-path-transmitting-samples)
8. [Delay Line Implementation](#delay-line-implementation)
9. [Performance Optimization Techniques](#performance-optimization-techniques)
10. [Complete Example: Target Emulator](#complete-example-target-emulator)
11. [Build System Configuration](#build-system-configuration)

---

## Architecture Overview

### Pluto Hardware Platform

- **Processor**: Xilinx Zynq-7000 (ARM Cortex-A9 dual-core @ 667 MHz)
- **RF Frontend**: AD9361 (70 MHz - 6 GHz, up to 61.44 MSPS)
- **DMA Engine**: Hardware DMA for zero-copy sample transfers
- **Memory**: 512 MB DDR3 RAM
- **Network**: 1 Gbps Ethernet (USB-Ethernet)

### Multi-Core Design Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                    ARM Dual-Core System                     │
├──────────────────────────────┬──────────────────────────────┤
│         CORE 0               │         CORE 1               │
│    (High Priority DMA)       │   (Processing/Network)       │
├──────────────────────────────┼──────────────────────────────┤
│                              │                              │
│  ┌────────────────────┐      │   ┌────────────────────┐    │
│  │  DMA Reader Thread │      │   │  Processing Thread │    │
│  │  Priority: 90      │      │   │  Priority: Normal  │    │
│  │  SCHED_FIFO        │      │   │                    │    │
│  └─────────┬──────────┘      │   └─────────┬──────────┘    │
│            │                 │             │               │
│            │ iio_buffer_     │             │               │
│            │ refill()        │             │               │
│            │ (blocks ~2ms)   │             │               │
│            ▼                 │             ▼               │
│  ┌────────────────────┐      │   ┌────────────────────┐    │
│  │   Fast memcpy      │      │   │   Consume Data     │    │
│  │   to Ring Buffer   │      │   │   Process/TX       │    │
│  └─────────┬──────────┘      │   └─────────▲──────────┘    │
│            │                 │             │               │
└────────────┼─────────────────┴─────────────┼───────────────┘
             │                               │
             └───────► Ring Buffer ◄─────────┘
                    (Lock-Free,
                     Cache-Aligned)
```

**Key Principles**:
- **Producer-Consumer Pattern**: Core 0 produces data, Core 1 consumes
- **Lock-Free Communication**: Ring buffer with atomic operations
- **DMA Pacing**: Natural flow control via blocking DMA refill
- **Zero-Copy Design**: Pre-allocated buffers, pointer passing only

---

## LibIIO Stack Fundamentals

### Context Creation

The IIO context represents the connection to the hardware:

```c
#include <iio.h>

/* Try local context first (when running on Pluto) */
struct iio_context *ctx = iio_create_local_context();

/* Fall back to network context (when running from PC) */
if (!ctx) {
    ctx = iio_create_network_context("192.168.2.1");
}

if (!ctx) {
    fprintf(stderr, "ERROR: Failed to create IIO context\n");
    return -1;
}

printf("IIO context created successfully\n");
```

**Context Types**:
- **Local**: Direct hardware access (use when app runs on Pluto)
- **Network**: Remote access via network (for PC-based control)

### Device Discovery

Two key devices for SDR operations:

```c
/* DMA device - for sample streaming */
struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
if (!dev) {
    fprintf(stderr, "ERROR: cf-ad9361-lpc not found\n");
    return -1;
}

/* PHY device - for RF configuration */
struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
if (!phy) {
    fprintf(stderr, "ERROR: ad9361-phy not found\n");
    return -1;
}
```

**Device Roles**:
- `cf-ad9361-lpc`: DMA buffer access (RX/TX data path)
- `ad9361-phy`: RF configuration (frequency, gain, sample rate)

### Channel Configuration

#### RX Channel Setup

```c
/* Find RX channels (I/Q pair for first antenna) */
struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);

/* Enable channels for buffer operations */
if (rx0_i) iio_channel_enable(rx0_i);
if (rx0_q) iio_channel_enable(rx0_q);
```

#### TX Channel Setup

```c
/* Find TX channels (I/Q pair for first antenna) */
struct iio_channel *tx0_i = iio_device_find_channel(dev, "voltage0", true);
struct iio_channel *tx0_q = iio_device_find_channel(dev, "voltage1", true);

/* Enable TX channels */
if (tx0_i) iio_channel_enable(tx0_i);
if (tx0_q) iio_channel_enable(tx0_q);
```

**Channel Naming**:
- `voltage0` (output=false): RX I channel
- `voltage1` (output=false): RX Q channel
- `voltage0` (output=true): TX I channel
- `voltage1` (output=true): TX Q channel

### RF Configuration

Complete SDR configuration function:

```c
typedef struct {
    uint64_t center_freq_hz;    /* RF center frequency */
    uint32_t sample_rate_hz;    /* ADC/DAC sample rate */
    uint32_t bandwidth_hz;      /* Analog filter bandwidth */
    double gain_db;             /* RX gain in dB */
    bool config_changed;        /* Reconfiguration flag */
    pthread_mutex_t mutex;      /* Thread-safe access */
} sdr_config_t;

int configure_sdr(struct iio_context *ctx,
                  struct iio_device *dev,
                  sdr_config_t *config) {

    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    if (!phy) return -1;

    char buf[64];

    /* Disable channels before rate change */
    struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
    struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);
    if (rx0_i) iio_channel_disable(rx0_i);
    if (rx0_q) iio_channel_disable(rx0_q);

    /* Set RX LO frequency */
    struct iio_channel *lo_ch = iio_device_find_channel(phy, "altvoltage0", true);
    if (lo_ch) {
        snprintf(buf, sizeof(buf), "%llu", config->center_freq_hz);
        iio_channel_attr_write(lo_ch, "frequency", buf);
    }

    /* Set sample rate */
    struct iio_channel *phy_rx = iio_device_find_channel(phy, "voltage0", false);
    if (phy_rx) {
        snprintf(buf, sizeof(buf), "%u", config->sample_rate_hz);
        iio_channel_attr_write(phy_rx, "sampling_frequency", buf);

        /* Verify actual rate */
        char verify[64];
        iio_channel_attr_read(phy_rx, "sampling_frequency", verify, sizeof(verify));
        printf("Sample rate set to: %s Hz\n", verify);

        /* Set bandwidth (typically 80% of sample rate) */
        snprintf(buf, sizeof(buf), "%u", config->bandwidth_hz);
        iio_channel_attr_write(phy_rx, "rf_bandwidth", buf);

        /* Set gain */
        snprintf(buf, sizeof(buf), "%.1f", config->gain_db);
        iio_channel_attr_write(phy_rx, "hardwaregain", buf);

        /* Manual gain control mode */
        iio_channel_attr_write(phy_rx, "gain_control_mode", "manual");
    }

    /* Allow PLLs to settle after configuration change */
    usleep(10000);  /* 10ms */

    /* Re-enable channels */
    if (rx0_i) iio_channel_enable(rx0_i);
    if (rx0_q) iio_channel_enable(rx0_q);

    return 0;
}
```

**Configuration Constraints**:
- **Sample Rate**: 2.083 MSPS to 61.44 MSPS (AD9361 limit)
- **Frequency**: 70 MHz to 6 GHz
- **Gain**: 0 to 76 dB (RX), -89.75 to 0 dB (TX)
- **Bandwidth**: Auto-calculated or manual (< sample_rate)

### Buffer Operations

#### Creating Buffers

```c
/* Calculate buffer size: ~3ms worth of samples for low latency */
#define BUFFER_TIME_MS 3
#define MIN_BUFFER_SAMPLES 4096
#define MAX_BUFFER_SAMPLES 65536

size_t buffer_samples = (config->sample_rate_hz * BUFFER_TIME_MS) / 1000;

/* Clamp to valid range */
buffer_samples = (buffer_samples < MIN_BUFFER_SAMPLES) ?
                  MIN_BUFFER_SAMPLES : buffer_samples;
buffer_samples = (buffer_samples > MAX_BUFFER_SAMPLES) ?
                  MAX_BUFFER_SAMPLES : buffer_samples;

/* Create RX buffer */
struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
if (!rxbuf) {
    fprintf(stderr, "ERROR: Failed to create RX buffer\n");
    return -1;
}

printf("RX Buffer: %zu samples (%.2f ms at %.1f MSPS)\n",
       buffer_samples,
       (double)buffer_samples * 1000.0 / config->sample_rate_hz,
       config->sample_rate_hz / 1e6);
```

**Buffer Sizing Guidelines**:
- **Latency**: Smaller buffers = lower latency (min ~0.1ms)
- **Efficiency**: Larger buffers = fewer interrupts (max ~10ms)
- **Sweet Spot**: 2-5ms provides good balance (~8K-16K samples @ 10 MSPS)

#### DMA Refill (RX)

```c
/* This call BLOCKS until DMA buffer is ready (~2-3ms typical) */
ssize_t nbytes = iio_buffer_refill(rxbuf);

if (nbytes < 0) {
    fprintf(stderr, "ERROR: iio_buffer_refill failed: %zd\n", nbytes);
    return -1;
}

/* Get pointer to first I sample */
struct iio_channel *rx_i = iio_device_find_channel(dev, "voltage0", false);
int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, rx_i);

/* samples now points to interleaved I/Q data:
 * samples[0] = I0, samples[1] = Q0,
 * samples[2] = I1, samples[3] = Q1, ...
 */
size_t num_samples = nbytes / 4;  /* 4 bytes per complex sample (I16+Q16) */
```

**DMA Pacing Characteristics**:
- `iio_buffer_refill()` blocks until hardware DMA completes
- Provides natural flow control (no manual sleep needed)
- Timing variance is normal (1-5ms range typical)
- Zero sample loss when keeping up with DMA rate

#### DMA Push (TX)

```c
/* Create TX buffer */
struct iio_buffer *txbuf = iio_device_create_buffer(dev, buffer_samples, false);

/* Get pointer to TX buffer */
struct iio_channel *tx_i = iio_device_find_channel(dev, "voltage0", true);
int16_t *tx_samples = (int16_t *)iio_buffer_first(txbuf, tx_i);

/* Fill buffer with I/Q data */
for (size_t i = 0; i < buffer_samples; i++) {
    tx_samples[i * 2 + 0] = iq_data[i].i;  /* I component */
    tx_samples[i * 2 + 1] = iq_data[i].q;  /* Q component */
}

/* Push buffer to TX DMA */
ssize_t nbytes = iio_buffer_push(txbuf);
if (nbytes < 0) {
    fprintf(stderr, "ERROR: iio_buffer_push failed: %zd\n", nbytes);
}
```

---

## Multi-Core Threading Architecture

### Thread Affinity (Core Pinning)

Pin threads to specific CPU cores to prevent context switching overhead:

```c
#define _GNU_SOURCE  /* Required for CPU_SET, pthread_setaffinity_np */
#include <pthread.h>
#include <sched.h>

/* Pin thread to Core 0 */
void pin_thread_to_core0(void) {
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(0, &cpuset);  /* Core 0 */

    int ret = pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);
    if (ret != 0) {
        fprintf(stderr, "WARNING: Failed to set CPU affinity\n");
    } else {
        printf("[Thread] Pinned to Core 0\n");
    }
}

/* Pin thread to Core 1 */
void pin_thread_to_core1(void) {
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(1, &cpuset);  /* Core 1 */

    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);
    printf("[Thread] Pinned to Core 1\n");
}
```

### Real-Time Thread Priority

Elevate DMA thread priority to minimize latency:

```c
void set_realtime_priority(int priority) {
    struct sched_param param;
    param.sched_priority = priority;  /* 1-99, higher = more priority */

    int ret = pthread_setschedparam(pthread_self(), SCHED_FIFO, &param);
    if (ret != 0) {
        fprintf(stderr, "WARNING: Failed to set real-time priority\n");
        fprintf(stderr, "         Run as root or set CAP_SYS_NICE capability\n");
    } else {
        printf("[Thread] Real-time priority set: %d\n", priority);
    }
}
```

**Priority Guidelines**:
- **90-99**: Critical DMA threads (sample loss prevention)
- **50-89**: Processing threads (important but not critical)
- **1-49**: Background tasks
- **0**: Normal scheduling (SCHED_OTHER)

### Producer Thread (Core 0 - DMA Reader)

High-priority thread dedicated to reading DMA and feeding ring buffer:

```c
void *dma_reader_thread(void *arg) {
    struct iio_context *ctx = (struct iio_context *)arg;

    /* Pin to Core 0 with high priority */
    pin_thread_to_core0();
    set_realtime_priority(90);

    /* Configure SDR */
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    configure_sdr(ctx, dev, &g_sdr_config);

    /* Create DMA buffer */
    size_t buffer_samples = calculate_buffer_size(g_sdr_config.sample_rate_hz);
    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
    struct iio_channel *rx_chan = iio_device_find_channel(dev, "voltage0", false);

    size_t buffer_idx = 0;
    uint64_t sequence = 0;

    while (g_running) {
        /* BLOCK here until DMA fills buffer - natural pacing */
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        if (nbytes < 0) {
            g_stats.refill_failures++;
            usleep(1000);
            continue;
        }

        /* Get DMA buffer pointer */
        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, rx_chan);
        size_t num_samples = nbytes / 4;

        /* Prepare ring buffer entry */
        iq_buffer_entry_t entry = {
            .data = g_ring_buffer.sample_pool[buffer_idx],
            .sample_count = num_samples,
            .timestamp_us = get_timestamp_us(),
            .sequence_num = sequence++,
            .buffer_id = buffer_idx
        };

        /* Fast memcpy - entire buffer at once (CRITICAL for performance) */
        memcpy(entry.data, samples, num_samples * 4);

        /* Push to ring buffer (lock-free, non-blocking) */
        if (!ring_buffer_push(&g_ring_buffer, &entry)) {
            g_stats.ring_buffer_drops++;
            /* Ring buffer full - consumer may be overloaded */
        } else {
            g_stats.dma_buffers_processed++;
        }

        /* Rotate to next buffer in pool */
        buffer_idx = (buffer_idx + 1) % RING_BUFFER_CAPACITY;
    }

    iio_buffer_destroy(rxbuf);
    return NULL;
}
```

**Design Rationale**:
- **Core 0 Isolation**: Dedicated to DMA, minimal interruptions
- **High Priority**: Ensures samples never lost due to scheduling
- **Fast memcpy**: Bulk copy entire buffer (no sample loops)
- **Lock-Free Push**: No mutex contention in hot path
- **Pre-allocated Pool**: Zero malloc/free in main loop

### Consumer Thread (Core 1 - Processing)

Processes data from ring buffer and handles TX:

```c
void *processing_thread(void *arg) {
    /* Pin to Core 1 */
    pin_thread_to_core1();

    printf("[Processing Thread] Started on Core 1\n");

    /* TX buffer setup */
    struct iio_context *ctx = (struct iio_context *)arg;
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");

    /* Processing variables */
    int16_t *processed_buffer = malloc(MAX_IQ_SAMPLES * 4);

    while (g_running) {
        /* Pop from ring buffer (non-blocking) */
        iq_buffer_entry_t iq_buffer;
        if (ring_buffer_pop(&g_ring_buffer, &iq_buffer)) {

            /* Process samples (your application logic here) */
            process_iq_samples(iq_buffer.data,
                             iq_buffer.sample_count,
                             processed_buffer);

            /* Update statistics */
            g_stats.buffers_processed++;

        } else {
            /* Ring buffer empty - brief sleep */
            usleep(10);  /* 10 microseconds */
        }
    }

    free(processed_buffer);
    return NULL;
}
```

---

## Lock-Free Ring Buffer Implementation

### Architecture

Single-producer, single-consumer (SPSC) lock-free ring buffer optimized for ARM Cortex-A9:

```c
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>

/* Configuration */
#define RING_BUFFER_CAPACITY    64      /* Must be power of 2 */
#define RING_BUFFER_MASK        (RING_BUFFER_CAPACITY - 1)
#define MAX_IQ_SAMPLES          65536   /* Max samples per buffer */
#define CACHE_LINE_SIZE         64      /* ARM Cortex-A9 cache line */

/* Compile-time check for power-of-2 */
_Static_assert((RING_BUFFER_CAPACITY & (RING_BUFFER_CAPACITY - 1)) == 0,
               "RING_BUFFER_CAPACITY must be power of 2");

/* Buffer entry metadata */
typedef struct {
    int16_t* data;              /* Pointer to I/Q samples */
    size_t sample_count;        /* Number of I/Q pairs */
    uint64_t timestamp_us;      /* Capture timestamp */
    uint32_t sequence_num;      /* Sequence number */
    uint32_t buffer_id;         /* Buffer pool index */
} iq_buffer_entry_t;
```

### Data Structure

```c
typedef struct {
    /* Cache-line aligned atomic indices (avoid false sharing) */
    atomic_size_t write_idx __attribute__((aligned(CACHE_LINE_SIZE)));
    atomic_size_t read_idx  __attribute__((aligned(CACHE_LINE_SIZE)));

    /* Ring buffer entries */
    iq_buffer_entry_t entries[RING_BUFFER_CAPACITY]
        __attribute__((aligned(CACHE_LINE_SIZE)));

    /* Pre-allocated sample buffer pool - NO malloc in hot path */
    int16_t sample_pool[RING_BUFFER_CAPACITY][MAX_IQ_SAMPLES * 2]
        __attribute__((aligned(CACHE_LINE_SIZE)));

    /* Statistics */
    _Atomic uint64_t pushes_attempted;
    _Atomic uint64_t pushes_successful;
    _Atomic uint64_t pops_attempted;
    _Atomic uint64_t pops_successful;
    _Atomic uint64_t buffer_full_events;
    _Atomic uint64_t buffer_empty_events;

} lock_free_ring_buffer_t;
```

**Memory Layout Optimizations**:
- **Cache-Line Alignment**: Prevents false sharing between cores
- **Pre-allocated Pool**: Eliminates malloc/free overhead
- **Power-of-2 Size**: Fast modulo via bitmask (% becomes &)
- **Separate Read/Write**: Minimizes cache coherency traffic

### Initialization

```c
void ring_buffer_init(lock_free_ring_buffer_t* rb) {
    /* Initialize atomic indices */
    atomic_init(&rb->write_idx, 0);
    atomic_init(&rb->read_idx, 0);

    /* Initialize statistics */
    atomic_init(&rb->pushes_attempted, 0);
    atomic_init(&rb->pushes_successful, 0);
    atomic_init(&rb->pops_attempted, 0);
    atomic_init(&rb->pops_successful, 0);
    atomic_init(&rb->buffer_full_events, 0);
    atomic_init(&rb->buffer_empty_events, 0);

    /* Link entries to pre-allocated buffers */
    for (size_t i = 0; i < RING_BUFFER_CAPACITY; i++) {
        rb->entries[i].data = rb->sample_pool[i];
        rb->entries[i].sample_count = 0;
        rb->entries[i].timestamp_us = 0;
        rb->entries[i].sequence_num = 0;
        rb->entries[i].buffer_id = i;
    }

    printf("Ring buffer initialized: %d entries, %zu MB total\n",
           RING_BUFFER_CAPACITY,
           sizeof(lock_free_ring_buffer_t) / (1024 * 1024));
}
```

### Push Operation (Producer)

Called by DMA reader thread on Core 0:

```c
static inline bool ring_buffer_push(lock_free_ring_buffer_t* rb,
                                    const iq_buffer_entry_t* entry) {
    atomic_fetch_add_explicit(&rb->pushes_attempted, 1, memory_order_relaxed);

    /* Load current write index (relaxed - we're the only writer) */
    const size_t write = atomic_load_explicit(&rb->write_idx, memory_order_relaxed);
    const size_t next_write = (write + 1) & RING_BUFFER_MASK;

    /* Check if buffer is full (acquire - sync with consumer) */
    if (next_write == atomic_load_explicit(&rb->read_idx, memory_order_acquire)) {
        atomic_fetch_add_explicit(&rb->buffer_full_events, 1, memory_order_relaxed);
        return false;  /* Buffer full */
    }

    /* Copy entry metadata to ring slot */
    rb->entries[write] = *entry;

    /* Update write index (release - make data visible to consumer) */
    atomic_store_explicit(&rb->write_idx, next_write, memory_order_release);

    atomic_fetch_add_explicit(&rb->pushes_successful, 1, memory_order_relaxed);
    return true;
}
```

**Memory Ordering**:
- `memory_order_relaxed`: No synchronization (stats only)
- `memory_order_acquire`: Read with synchronization from other thread
- `memory_order_release`: Write with synchronization to other thread

### Pop Operation (Consumer)

Called by processing thread on Core 1:

```c
static inline bool ring_buffer_pop(lock_free_ring_buffer_t* rb,
                                   iq_buffer_entry_t* entry) {
    atomic_fetch_add_explicit(&rb->pops_attempted, 1, memory_order_relaxed);

    /* Load current read index */
    const size_t read = atomic_load_explicit(&rb->read_idx, memory_order_relaxed);

    /* Check if buffer is empty (acquire - sync with producer) */
    if (read == atomic_load_explicit(&rb->write_idx, memory_order_acquire)) {
        atomic_fetch_add_explicit(&rb->buffer_empty_events, 1, memory_order_relaxed);
        return false;  /* Buffer empty */
    }

    /* Copy entry from ring slot */
    *entry = rb->entries[read];

    /* Update read index (release - signal we're done with data) */
    atomic_store_explicit(&rb->read_idx, (read + 1) & RING_BUFFER_MASK,
                         memory_order_release);

    atomic_fetch_add_explicit(&rb->pops_successful, 1, memory_order_relaxed);
    return true;
}
```

### Performance Monitoring

```c
typedef struct {
    uint64_t pushes_attempted;
    uint64_t pushes_successful;
    uint64_t pops_attempted;
    uint64_t pops_successful;
    uint64_t buffer_full_events;
    uint64_t buffer_empty_events;
    double push_success_rate;
    double pop_success_rate;
    double current_utilization;
    size_t current_size;
} ring_buffer_stats_t;

ring_buffer_stats_t ring_buffer_get_stats(const lock_free_ring_buffer_t* rb) {
    ring_buffer_stats_t stats;

    stats.pushes_attempted = atomic_load(&rb->pushes_attempted);
    stats.pushes_successful = atomic_load(&rb->pushes_successful);
    stats.pops_attempted = atomic_load(&rb->pops_attempted);
    stats.pops_successful = atomic_load(&rb->pops_successful);
    stats.buffer_full_events = atomic_load(&rb->buffer_full_events);
    stats.buffer_empty_events = atomic_load(&rb->buffer_empty_events);

    stats.push_success_rate = stats.pushes_attempted > 0 ?
        (double)stats.pushes_successful / stats.pushes_attempted : 0.0;
    stats.pop_success_rate = stats.pops_attempted > 0 ?
        (double)stats.pops_successful / stats.pops_attempted : 0.0;

    /* Calculate current buffer fullness */
    size_t write = atomic_load(&rb->write_idx);
    size_t read = atomic_load(&rb->read_idx);
    size_t used = (write >= read) ? (write - read) :
                  (RING_BUFFER_CAPACITY - read + write);

    stats.current_utilization = (double)used / RING_BUFFER_CAPACITY;
    stats.current_size = used;

    return stats;
}
```

**Health Indicators**:
- **Push Success Rate**: Should be >99% (drops indicate consumer too slow)
- **Pop Success Rate**: Can be lower (producer pacing)
- **Buffer Utilization**: 20-80% ideal (headroom for bursts)
- **Full Events**: Occasional OK, frequent = increase buffer size

---

## DMA Operations and Memory Management

### Fast memcpy Operations

Critical performance optimization - copy entire buffer at once:

```c
/* GOOD: Fast bulk copy */
size_t num_samples = nbytes / 4;  /* 4 bytes per I/Q pair */
memcpy(entry.data, samples, num_samples * 4);

/* BAD: Sample-by-sample loop (10x slower!) */
for (size_t i = 0; i < num_samples * 2; i++) {
    entry.data[i] = samples[i];  /* DON'T DO THIS */
}
```

**Performance Impact**:
- **Bulk memcpy**: ~1 GB/s on ARM Cortex-A9
- **Sample loop**: ~100 MB/s (10x slower)
- **NEON intrinsics**: ~1.2 GB/s (marginal gain, complex code)

### Memory Alignment

Ensure buffers are cache-line aligned for optimal DMA performance:

```c
#define CACHE_LINE_SIZE 64

/* Aligned allocation */
int16_t *buffer = aligned_alloc(CACHE_LINE_SIZE, buffer_size);
if (!buffer) {
    fprintf(stderr, "ERROR: Failed to allocate aligned buffer\n");
    return -1;
}

/* Use it... */

/* Free */
free(buffer);  /* free() works with aligned_alloc() */
```

### Zero-Copy Techniques

Avoid copying by passing pointers instead of data:

```c
/* Instead of copying entire buffers between threads,
 * pass metadata with pointers to pre-allocated pool */

typedef struct {
    int16_t *data;        /* Pointer, not copy */
    size_t sample_count;
    uint64_t timestamp;
} buffer_ref_t;

/* Producer just updates pointer */
buffer_ref_t ref = {
    .data = sample_pool[pool_idx],
    .sample_count = num_samples,
    .timestamp = get_timestamp_us()
};

ring_buffer_push(&rb, &ref);  /* Only copies 24 bytes, not samples */
```

---

## RX Path: Capturing Samples

### Complete RX Thread Example

```c
typedef struct {
    struct iio_context *ctx;
    lock_free_ring_buffer_t *ring_buffer;
    volatile bool *running;
    sdr_config_t *config;
} rx_thread_args_t;

void *rx_thread_func(void *arg) {
    rx_thread_args_t *args = (rx_thread_args_t *)arg;

    /* Pin to Core 0, high priority */
    pin_thread_to_core0();
    set_realtime_priority(90);

    printf("[RX Thread] Started on Core 0, priority 90\n");

    /* Get devices */
    struct iio_device *dev = iio_context_find_device(args->ctx, "cf-ad9361-lpc");

    /* Configure SDR */
    if (configure_sdr(args->ctx, dev, args->config) < 0) {
        fprintf(stderr, "[RX Thread] Failed to configure SDR\n");
        return NULL;
    }

    /* Calculate buffer size */
    size_t buffer_samples = (args->config->sample_rate_hz * BUFFER_TIME_MS) / 1000;
    buffer_samples = CLAMP(buffer_samples, MIN_BUFFER_SAMPLES, MAX_BUFFER_SAMPLES);

    /* Create DMA buffer */
    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
    if (!rxbuf) {
        fprintf(stderr, "[RX Thread] Failed to create buffer\n");
        return NULL;
    }

    struct iio_channel *rx_chan = iio_device_find_channel(dev, "voltage0", false);

    printf("[RX Thread] Buffer: %zu samples (%.2f ms)\n",
           buffer_samples,
           (double)buffer_samples * 1000.0 / args->config->sample_rate_hz);

    /* Main loop */
    size_t buffer_idx = 0;
    uint64_t sequence = 0;
    uint64_t total_samples = 0;

    while (*args->running) {
        /* Block until DMA ready */
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        if (nbytes < 0) {
            fprintf(stderr, "[RX Thread] Refill failed: %zd\n", nbytes);
            usleep(1000);
            continue;
        }

        /* Get samples */
        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, rx_chan);
        size_t num_samples = nbytes / 4;

        /* Prepare ring buffer entry */
        iq_buffer_entry_t entry = {
            .data = args->ring_buffer->sample_pool[buffer_idx],
            .sample_count = num_samples,
            .timestamp_us = get_timestamp_us(),
            .sequence_num = sequence++,
            .buffer_id = buffer_idx
        };

        /* Fast copy to ring buffer pool */
        memcpy(entry.data, samples, num_samples * 4);

        /* Push to ring buffer */
        if (!ring_buffer_push(args->ring_buffer, &entry)) {
            /* Ring buffer full - sample loss! */
            fprintf(stderr, "[RX Thread] WARNING: Ring buffer full, dropping samples\n");
        }

        /* Update stats */
        total_samples += num_samples;

        /* Rotate buffer pool index */
        buffer_idx = (buffer_idx + 1) % RING_BUFFER_CAPACITY;
    }

    printf("[RX Thread] Captured %llu total samples\n",
           (unsigned long long)total_samples);

    iio_buffer_destroy(rxbuf);
    return NULL;
}
```

---

## TX Path: Transmitting Samples

### Complete TX Thread Example

```c
typedef struct {
    struct iio_context *ctx;
    int16_t *tx_samples;      /* Pre-filled TX samples */
    size_t num_tx_samples;
    volatile bool *running;
    sdr_config_t *config;
} tx_thread_args_t;

void *tx_thread_func(void *arg) {
    tx_thread_args_t *args = (tx_thread_args_t *)arg;

    printf("[TX Thread] Started\n");

    /* Get device */
    struct iio_device *dev = iio_context_find_device(args->ctx, "cf-ad9361-lpc");

    /* Enable TX channels */
    struct iio_channel *tx0_i = iio_device_find_channel(dev, "voltage0", true);
    struct iio_channel *tx0_q = iio_device_find_channel(dev, "voltage1", true);
    if (tx0_i) iio_channel_enable(tx0_i);
    if (tx0_q) iio_channel_enable(tx0_q);

    /* Configure TX frequency/gain via phy device */
    struct iio_device *phy = iio_context_find_device(args->ctx, "ad9361-phy");
    struct iio_channel *tx_lo = iio_device_find_channel(phy, "altvoltage1", true);

    char buf[64];
    snprintf(buf, sizeof(buf), "%llu", args->config->center_freq_hz);
    iio_channel_attr_write(tx_lo, "frequency", buf);

    /* Set TX gain (attenuation: 0 = max power, -89.75 = min) */
    struct iio_channel *tx_ch = iio_device_find_channel(phy, "voltage0", true);
    snprintf(buf, sizeof(buf), "%.2f", -20.0);  /* -20 dB attenuation */
    iio_channel_attr_write(tx_ch, "hardwaregain", buf);

    /* Create TX buffer */
    size_t buffer_samples = args->num_tx_samples;
    struct iio_buffer *txbuf = iio_device_create_buffer(dev, buffer_samples, false);
    if (!txbuf) {
        fprintf(stderr, "[TX Thread] Failed to create TX buffer\n");
        return NULL;
    }

    /* Get pointer to TX buffer */
    int16_t *tx_ptr = (int16_t *)iio_buffer_first(txbuf, tx0_i);

    printf("[TX Thread] TX buffer: %zu samples\n", buffer_samples);

    uint64_t buffers_sent = 0;

    while (*args->running) {
        /* Copy samples to TX buffer (or generate them) */
        memcpy(tx_ptr, args->tx_samples, buffer_samples * 4);

        /* Push to TX DMA */
        ssize_t nbytes = iio_buffer_push(txbuf);
        if (nbytes < 0) {
            fprintf(stderr, "[TX Thread] Push failed: %zd\n", nbytes);
            usleep(1000);
            continue;
        }

        buffers_sent++;

        /* Optional: Rate limiting (if not naturally paced) */
        /* usleep(buffer_time_us); */
    }

    printf("[TX Thread] Sent %llu buffers\n",
           (unsigned long long)buffers_sent);

    iio_buffer_destroy(txbuf);
    return NULL;
}
```

### TX Configuration

```c
/* Configure TX parameters on ad9361-phy */
int configure_tx(struct iio_context *ctx, sdr_config_t *config) {
    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    if (!phy) return -1;

    char buf[64];

    /* TX LO frequency (altvoltage1 = TX) */
    struct iio_channel *tx_lo = iio_device_find_channel(phy, "altvoltage1", true);
    if (tx_lo) {
        snprintf(buf, sizeof(buf), "%llu", config->center_freq_hz);
        iio_channel_attr_write(tx_lo, "frequency", buf);
    }

    /* TX sample rate */
    struct iio_channel *tx_ch = iio_device_find_channel(phy, "voltage0", true);
    if (tx_ch) {
        snprintf(buf, sizeof(buf), "%u", config->sample_rate_hz);
        iio_channel_attr_write(tx_ch, "sampling_frequency", buf);

        /* TX bandwidth */
        snprintf(buf, sizeof(buf), "%u", config->bandwidth_hz);
        iio_channel_attr_write(tx_ch, "rf_bandwidth", buf);

        /* TX attenuation (negative dB, 0 = max power) */
        snprintf(buf, sizeof(buf), "%.2f", -20.0);
        iio_channel_attr_write(tx_ch, "hardwaregain", buf);
    }

    return 0;
}
```

---

## Delay Line Implementation

### Circular Buffer Delay Line

Core data structure for target emulator or echo effects:

```c
#define MAX_DELAY_SAMPLES (30000000 * 2)  /* 2 seconds @ 30 MSPS */

typedef struct {
    int16_t *buffer;          /* Circular buffer storage */
    size_t capacity;          /* Total buffer size */
    size_t write_pos;         /* Write position */
    size_t delay_samples;     /* Delay in samples */
    pthread_mutex_t mutex;    /* Thread-safe access */
} delay_line_t;

/* Initialize delay line */
int delay_line_init(delay_line_t *dl, size_t delay_samples) {
    dl->capacity = delay_samples * 2 + 4096;  /* I/Q pairs + margin */

    /* Allocate aligned buffer */
    dl->buffer = aligned_alloc(CACHE_LINE_SIZE, dl->capacity * sizeof(int16_t));
    if (!dl->buffer) {
        return -1;
    }

    /* Zero out buffer */
    memset(dl->buffer, 0, dl->capacity * sizeof(int16_t));

    dl->write_pos = 0;
    dl->delay_samples = delay_samples * 2;  /* I/Q pairs */
    pthread_mutex_init(&dl->mutex, NULL);

    printf("Delay line initialized: %zu samples (%.3f ms)\n",
           delay_samples,
           (double)delay_samples * 1000.0 / 30000000);  /* Assume 30 MSPS */

    return 0;
}

/* Write samples to delay line */
void delay_line_write(delay_line_t *dl, int16_t *samples, size_t count) {
    pthread_mutex_lock(&dl->mutex);

    for (size_t i = 0; i < count; i++) {
        dl->buffer[dl->write_pos] = samples[i];
        dl->write_pos = (dl->write_pos + 1) % dl->capacity;
    }

    pthread_mutex_unlock(&dl->mutex);
}

/* Read delayed samples from delay line */
void delay_line_read(delay_line_t *dl, int16_t *output, size_t count) {
    pthread_mutex_lock(&dl->mutex);

    /* Calculate read position (delayed from write) */
    size_t read_pos = (dl->write_pos + dl->capacity - dl->delay_samples) % dl->capacity;

    for (size_t i = 0; i < count; i++) {
        output[i] = dl->buffer[read_pos];
        read_pos = (read_pos + 1) % dl->capacity;
    }

    pthread_mutex_unlock(&dl->mutex);
}

/* Set delay amount (in samples) */
void delay_line_set_delay(delay_line_t *dl, size_t delay_samples) {
    pthread_mutex_lock(&dl->mutex);
    dl->delay_samples = delay_samples * 2;  /* I/Q pairs */
    pthread_mutex_unlock(&dl->mutex);
}

/* Cleanup */
void delay_line_destroy(delay_line_t *dl) {
    free(dl->buffer);
    pthread_mutex_destroy(&dl->mutex);
}
```

### Modulation Functions

Apply effects to delayed samples:

```c
/* Amplitude modulation (for fading simulation) */
void apply_amplitude_modulation(int16_t *samples, size_t count, float gain) {
    for (size_t i = 0; i < count; i++) {
        float val = samples[i] * gain;

        /* Clamp to prevent overflow */
        if (val > 32767.0f) val = 32767.0f;
        if (val < -32768.0f) val = -32768.0f;

        samples[i] = (int16_t)val;
    }
}

/* Doppler shift (frequency offset) */
void apply_doppler_shift(int16_t *samples, size_t num_iq_pairs, float freq_shift_hz,
                         float sample_rate_hz, float *phase_accum) {
    float phase_inc = 2.0f * M_PI * freq_shift_hz / sample_rate_hz;

    for (size_t i = 0; i < num_iq_pairs; i++) {
        /* Get I/Q pair */
        float i_val = samples[i * 2 + 0];
        float q_val = samples[i * 2 + 1];

        /* Complex multiplication with e^(j*phase) */
        float cos_phase = cosf(*phase_accum);
        float sin_phase = sinf(*phase_accum);

        float i_new = i_val * cos_phase - q_val * sin_phase;
        float q_new = i_val * sin_phase + q_val * cos_phase;

        samples[i * 2 + 0] = (int16_t)i_new;
        samples[i * 2 + 1] = (int16_t)q_new;

        /* Increment phase */
        *phase_accum += phase_inc;
        if (*phase_accum > 2.0f * M_PI) *phase_accum -= 2.0f * M_PI;
    }
}

/* Add noise (for SNR simulation) */
void add_noise(int16_t *samples, size_t count, float noise_power) {
    for (size_t i = 0; i < count; i++) {
        /* Simple random noise (use better PRNG for production) */
        float noise = ((float)rand() / RAND_MAX * 2.0f - 1.0f) * noise_power;

        int32_t val = samples[i] + (int16_t)noise;

        /* Clamp */
        if (val > 32767) val = 32767;
        if (val < -32768) val = -32768;

        samples[i] = (int16_t)val;
    }
}
```

---

## Performance Optimization Techniques

### 1. Minimize System Calls

**Problem**: Each system call (sendto, recvfrom) has ~5-10 µs overhead.

**Solution**: Batch operations with `sendmmsg()` and `recvmmsg()`:

```c
#define SEND_BATCH_SIZE 64

struct mmsghdr msgs[SEND_BATCH_SIZE];
struct iovec iovecs[SEND_BATCH_SIZE];
uint8_t buffers[SEND_BATCH_SIZE][2048];

/* Set up message headers */
for (int i = 0; i < SEND_BATCH_SIZE; i++) {
    iovecs[i].iov_base = buffers[i];
    iovecs[i].iov_len = packet_size;

    msgs[i].msg_hdr.msg_iov = &iovecs[i];
    msgs[i].msg_hdr.msg_iovlen = 1;
    msgs[i].msg_hdr.msg_name = &dest_addr;
    msgs[i].msg_hdr.msg_namelen = sizeof(dest_addr);
}

/* Single syscall sends 64 packets */
int sent = sendmmsg(sock, msgs, SEND_BATCH_SIZE, 0);

/* Result: 64x fewer syscalls (83,000/sec → 1,300/sec) */
```

### 2. Avoid Hot Path Allocations

**Problem**: malloc/free in main loop kills performance.

**Solution**: Pre-allocate buffer pool:

```c
/* BAD: Allocation in loop */
while (running) {
    int16_t *buffer = malloc(65536 * 4);  /* SLOW! */
    process(buffer);
    free(buffer);
}

/* GOOD: Pre-allocated pool */
#define POOL_SIZE 64
int16_t *buffer_pool[POOL_SIZE];

/* Initialize once */
for (int i = 0; i < POOL_SIZE; i++) {
    buffer_pool[i] = aligned_alloc(64, 65536 * 4);
}

/* Use pool in loop */
int pool_idx = 0;
while (running) {
    int16_t *buffer = buffer_pool[pool_idx];
    process(buffer);
    pool_idx = (pool_idx + 1) % POOL_SIZE;  /* Rotate */
}
```

### 3. Cache-Friendly Data Structures

**Problem**: False sharing between cores destroys performance.

**Solution**: Align hot variables to cache lines:

```c
/* BAD: Variables share cache line */
typedef struct {
    atomic_int write_idx;  /* Core 0 writes */
    atomic_int read_idx;   /* Core 1 writes */
} bad_ring_buffer_t;
/* Both cores constantly invalidate each other's cache! */

/* GOOD: Cache-line aligned */
typedef struct {
    atomic_int write_idx __attribute__((aligned(64)));  /* Own cache line */
    atomic_int read_idx  __attribute__((aligned(64)));  /* Own cache line */
} good_ring_buffer_t;
/* Cores operate independently */
```

### 4. Compiler Optimizations

Enable aggressive optimization flags:

```makefile
CFLAGS = -O3 -march=armv7-a -mtune=cortex-a9 -mfpu=neon -mfloat-abi=hard
CFLAGS += -ffast-math -funroll-loops -ftree-vectorize
CFLAGS += -flto  # Link-time optimization

LDFLAGS = -flto -Wl,-O1
```

**Optimization Levels**:
- `-O2`: Safe optimizations (default)
- `-O3`: Aggressive (larger binary, faster)
- `-Ofast`: Breaks IEEE float compliance (use with care)
- `-march=armv7-a`: Target Cortex-A9 specifically
- `-mfpu=neon`: Enable NEON SIMD instructions

### 5. Reduce Mutex Contention

**Problem**: Frequent mutex locks serialize threads.

**Solution**: Lock-free atomics or coarse-grained locking:

```c
/* BAD: Mutex per operation */
pthread_mutex_lock(&mutex);
counter++;
pthread_mutex_unlock(&mutex);

/* GOOD: Atomic operation */
atomic_fetch_add(&counter, 1);

/* Or: Batch updates */
int local_counter = 0;
for (int i = 0; i < 1000; i++) {
    local_counter++;  /* No lock */
}
pthread_mutex_lock(&mutex);
global_counter += local_counter;  /* One lock */
pthread_mutex_unlock(&mutex);
```

### 6. Profile and Measure

Use `perf` to identify bottlenecks:

```bash
# On Pluto (may need custom kernel)
perf record -F 99 -a -g -- ./your_app
perf report

# Alternative: Manual timing
uint64_t t1 = get_timestamp_us();
critical_function();
uint64_t t2 = get_timestamp_us();
printf("Function took: %llu us\n", t2 - t1);
```

---

## Complete Example: Target Emulator

Full implementation of RX → Delay Line → Modulation → TX pipeline:

```c
/*
 * Target Emulator for ADALM-Pluto
 *
 * Receives RF signal, delays it, applies modulation effects, and retransmits
 *
 * Build: arm-linux-gnueabihf-gcc -o target_emulator target_emulator.c -liio -lpthread -lm
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <unistd.h>
#include <pthread.h>
#include <sched.h>
#include <math.h>
#include <signal.h>
#include <sys/time.h>
#include <iio.h>

/* Configuration */
#define RX_FREQ_HZ          2400000000ULL   /* 2.4 GHz */
#define TX_FREQ_HZ          2450000000ULL   /* 2.45 GHz (offset) */
#define SAMPLE_RATE_HZ      10000000        /* 10 MSPS */
#define DELAY_MS            50              /* 50ms delay */
#define DOPPLER_HZ          1000            /* 1 kHz Doppler shift */
#define AMPLITUDE_GAIN      0.8             /* 80% amplitude */

#define BUFFER_TIME_MS      5
#define MIN_BUFFER_SAMPLES  4096
#define MAX_BUFFER_SAMPLES  65536
#define CACHE_LINE_SIZE     64

/* Global state */
static volatile bool g_running = true;

/* Delay line structure */
typedef struct {
    int16_t *buffer;
    size_t capacity;
    size_t write_pos;
    size_t delay_samples;
    pthread_mutex_t mutex;
    float doppler_phase;
} delay_line_t;

/* SDR config */
typedef struct {
    uint64_t rx_freq_hz;
    uint64_t tx_freq_hz;
    uint32_t sample_rate_hz;
    uint32_t bandwidth_hz;
    double rx_gain_db;
    double tx_atten_db;
    size_t delay_ms;
    float doppler_hz;
    float amplitude_gain;
} target_config_t;

static target_config_t g_config = {
    .rx_freq_hz = RX_FREQ_HZ,
    .tx_freq_hz = TX_FREQ_HZ,
    .sample_rate_hz = SAMPLE_RATE_HZ,
    .bandwidth_hz = SAMPLE_RATE_HZ * 0.8,
    .rx_gain_db = 40.0,
    .tx_atten_db = -20.0,
    .delay_ms = DELAY_MS,
    .doppler_hz = DOPPLER_HZ,
    .amplitude_gain = AMPLITUDE_GAIN
};

/* Statistics */
typedef struct {
    uint64_t rx_buffers;
    uint64_t tx_buffers;
    uint64_t dropped_buffers;
} statistics_t;

static statistics_t g_stats = {0};

/* Signal handler */
static void signal_handler(int sig) {
    (void)sig;
    printf("\nShutting down...\n");
    g_running = false;
}

/* Timestamp */
static uint64_t get_timestamp_us(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000000ULL + tv.tv_usec;
}

/* Delay line implementation */
static int delay_line_init(delay_line_t *dl, size_t delay_samples) {
    dl->capacity = (delay_samples + 4096) * 2;  /* I/Q pairs + margin */

    dl->buffer = aligned_alloc(CACHE_LINE_SIZE, dl->capacity * sizeof(int16_t));
    if (!dl->buffer) return -1;

    memset(dl->buffer, 0, dl->capacity * sizeof(int16_t));

    dl->write_pos = 0;
    dl->delay_samples = delay_samples * 2;
    dl->doppler_phase = 0.0f;
    pthread_mutex_init(&dl->mutex, NULL);

    printf("Delay line: %zu samples (%.1f ms @ %.1f MSPS)\n",
           delay_samples,
           (double)delay_samples * 1000.0 / g_config.sample_rate_hz,
           g_config.sample_rate_hz / 1e6);

    return 0;
}

static void delay_line_write(delay_line_t *dl, int16_t *samples, size_t count) {
    pthread_mutex_lock(&dl->mutex);

    for (size_t i = 0; i < count; i++) {
        dl->buffer[dl->write_pos] = samples[i];
        dl->write_pos = (dl->write_pos + 1) % dl->capacity;
    }

    pthread_mutex_unlock(&dl->mutex);
}

static void delay_line_read_and_modulate(delay_line_t *dl, int16_t *output,
                                          size_t count, target_config_t *config) {
    pthread_mutex_lock(&dl->mutex);

    /* Calculate read position */
    size_t read_pos = (dl->write_pos + dl->capacity - dl->delay_samples) % dl->capacity;

    /* Read samples */
    for (size_t i = 0; i < count; i++) {
        output[i] = dl->buffer[read_pos];
        read_pos = (read_pos + 1) % dl->capacity;
    }

    pthread_mutex_unlock(&dl->mutex);

    /* Apply modulation effects (outside lock for better concurrency) */
    size_t num_iq_pairs = count / 2;
    float phase_inc = 2.0f * M_PI * config->doppler_hz / config->sample_rate_hz;

    for (size_t i = 0; i < num_iq_pairs; i++) {
        /* Get I/Q pair */
        float i_val = output[i * 2 + 0];
        float q_val = output[i * 2 + 1];

        /* Apply amplitude scaling */
        i_val *= config->amplitude_gain;
        q_val *= config->amplitude_gain;

        /* Apply Doppler shift (complex multiply by e^(j*phase)) */
        float cos_phase = cosf(dl->doppler_phase);
        float sin_phase = sinf(dl->doppler_phase);

        float i_new = i_val * cos_phase - q_val * sin_phase;
        float q_new = i_val * sin_phase + q_val * cos_phase;

        /* Clamp and store */
        i_new = (i_new > 32767.0f) ? 32767.0f : (i_new < -32768.0f) ? -32768.0f : i_new;
        q_new = (q_new > 32767.0f) ? 32767.0f : (q_new < -32768.0f) ? -32768.0f : q_new;

        output[i * 2 + 0] = (int16_t)i_new;
        output[i * 2 + 1] = (int16_t)q_new;

        /* Increment phase */
        dl->doppler_phase += phase_inc;
        if (dl->doppler_phase > 2.0f * M_PI) dl->doppler_phase -= 2.0f * M_PI;
    }
}

static void delay_line_destroy(delay_line_t *dl) {
    free(dl->buffer);
    pthread_mutex_destroy(&dl->mutex);
}

/* Configure RX */
static int configure_rx(struct iio_context *ctx) {
    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    if (!phy || !dev) return -1;

    char buf[64];

    /* Disable channels */
    struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
    struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);
    if (rx0_i) iio_channel_disable(rx0_i);
    if (rx0_q) iio_channel_disable(rx0_q);

    /* Set RX frequency */
    struct iio_channel *rx_lo = iio_device_find_channel(phy, "altvoltage0", true);
    if (rx_lo) {
        snprintf(buf, sizeof(buf), "%llu", g_config.rx_freq_hz);
        iio_channel_attr_write(rx_lo, "frequency", buf);
    }

    /* Set sample rate */
    struct iio_channel *phy_rx = iio_device_find_channel(phy, "voltage0", false);
    if (phy_rx) {
        snprintf(buf, sizeof(buf), "%u", g_config.sample_rate_hz);
        iio_channel_attr_write(phy_rx, "sampling_frequency", buf);

        snprintf(buf, sizeof(buf), "%u", g_config.bandwidth_hz);
        iio_channel_attr_write(phy_rx, "rf_bandwidth", buf);

        snprintf(buf, sizeof(buf), "%.1f", g_config.rx_gain_db);
        iio_channel_attr_write(phy_rx, "hardwaregain", buf);

        iio_channel_attr_write(phy_rx, "gain_control_mode", "manual");
    }

    usleep(10000);

    /* Re-enable channels */
    if (rx0_i) iio_channel_enable(rx0_i);
    if (rx0_q) iio_channel_enable(rx0_q);

    printf("RX configured: %.1f MHz, %.1f MSPS, %.1f dB gain\n",
           g_config.rx_freq_hz / 1e6,
           g_config.sample_rate_hz / 1e6,
           g_config.rx_gain_db);

    return 0;
}

/* Configure TX */
static int configure_tx(struct iio_context *ctx) {
    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    if (!phy || !dev) return -1;

    char buf[64];

    /* Enable TX channels */
    struct iio_channel *tx0_i = iio_device_find_channel(dev, "voltage0", true);
    struct iio_channel *tx0_q = iio_device_find_channel(dev, "voltage1", true);
    if (tx0_i) iio_channel_enable(tx0_i);
    if (tx0_q) iio_channel_enable(tx0_q);

    /* Set TX frequency */
    struct iio_channel *tx_lo = iio_device_find_channel(phy, "altvoltage1", true);
    if (tx_lo) {
        snprintf(buf, sizeof(buf), "%llu", g_config.tx_freq_hz);
        iio_channel_attr_write(tx_lo, "frequency", buf);
    }

    /* Set sample rate and attenuation */
    struct iio_channel *phy_tx = iio_device_find_channel(phy, "voltage0", true);
    if (phy_tx) {
        snprintf(buf, sizeof(buf), "%u", g_config.sample_rate_hz);
        iio_channel_attr_write(phy_tx, "sampling_frequency", buf);

        snprintf(buf, sizeof(buf), "%u", g_config.bandwidth_hz);
        iio_channel_attr_write(phy_tx, "rf_bandwidth", buf);

        snprintf(buf, sizeof(buf), "%.2f", g_config.tx_atten_db);
        iio_channel_attr_write(phy_tx, "hardwaregain", buf);
    }

    printf("TX configured: %.1f MHz, %.1f MSPS, %.1f dB attenuation\n",
           g_config.tx_freq_hz / 1e6,
           g_config.sample_rate_hz / 1e6,
           g_config.tx_atten_db);

    return 0;
}

/* RX Thread - Core 0 */
typedef struct {
    struct iio_context *ctx;
    delay_line_t *delay_line;
} rx_thread_args_t;

static void *rx_thread_func(void *arg) {
    rx_thread_args_t *args = (rx_thread_args_t *)arg;

    /* Pin to Core 0 */
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(0, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);

    printf("[RX Thread] Started on Core 0\n");

    /* Configure RX */
    if (configure_rx(args->ctx) < 0) {
        fprintf(stderr, "[RX Thread] Failed to configure RX\n");
        return NULL;
    }

    struct iio_device *dev = iio_context_find_device(args->ctx, "cf-ad9361-lpc");

    /* Create buffer */
    size_t buffer_samples = (g_config.sample_rate_hz * BUFFER_TIME_MS) / 1000;
    buffer_samples = (buffer_samples < MIN_BUFFER_SAMPLES) ?
                      MIN_BUFFER_SAMPLES : buffer_samples;
    buffer_samples = (buffer_samples > MAX_BUFFER_SAMPLES) ?
                      MAX_BUFFER_SAMPLES : buffer_samples;

    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
    if (!rxbuf) {
        fprintf(stderr, "[RX Thread] Failed to create buffer\n");
        return NULL;
    }

    struct iio_channel *rx_chan = iio_device_find_channel(dev, "voltage0", false);

    printf("[RX Thread] Buffer: %zu samples (%.2f ms)\n",
           buffer_samples,
           (double)buffer_samples * 1000.0 / g_config.sample_rate_hz);

    /* Main loop */
    while (g_running) {
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        if (nbytes < 0) {
            usleep(1000);
            continue;
        }

        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, rx_chan);
        size_t num_samples = nbytes / 4;

        /* Write to delay line */
        delay_line_write(args->delay_line, samples, num_samples * 2);

        g_stats.rx_buffers++;
    }

    iio_buffer_destroy(rxbuf);
    printf("[RX Thread] Stopped\n");
    return NULL;
}

/* TX Thread - Core 1 */
typedef struct {
    struct iio_context *ctx;
    delay_line_t *delay_line;
} tx_thread_args_t;

static void *tx_thread_func(void *arg) {
    tx_thread_args_t *args = (tx_thread_args_t *)arg;

    /* Pin to Core 1 */
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(1, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);

    printf("[TX Thread] Started on Core 1\n");

    /* Configure TX */
    if (configure_tx(args->ctx) < 0) {
        fprintf(stderr, "[TX Thread] Failed to configure TX\n");
        return NULL;
    }

    struct iio_device *dev = iio_context_find_device(args->ctx, "cf-ad9361-lpc");

    /* Create buffer */
    size_t buffer_samples = (g_config.sample_rate_hz * BUFFER_TIME_MS) / 1000;
    buffer_samples = (buffer_samples < MIN_BUFFER_SAMPLES) ?
                      MIN_BUFFER_SAMPLES : buffer_samples;
    buffer_samples = (buffer_samples > MAX_BUFFER_SAMPLES) ?
                      MAX_BUFFER_SAMPLES : buffer_samples;

    struct iio_buffer *txbuf = iio_device_create_buffer(dev, buffer_samples, false);
    if (!txbuf) {
        fprintf(stderr, "[TX Thread] Failed to create TX buffer\n");
        return NULL;
    }

    struct iio_channel *tx_chan = iio_device_find_channel(dev, "voltage0", true);
    int16_t *tx_ptr = (int16_t *)iio_buffer_first(txbuf, tx_chan);

    printf("[TX Thread] Buffer: %zu samples (%.2f ms)\n",
           buffer_samples,
           (double)buffer_samples * 1000.0 / g_config.sample_rate_hz);

    /* Wait for delay line to fill */
    usleep(g_config.delay_ms * 1000);

    /* Main loop */
    while (g_running) {
        /* Read delayed + modulated samples */
        delay_line_read_and_modulate(args->delay_line, tx_ptr,
                                     buffer_samples * 2, &g_config);

        /* Push to TX */
        ssize_t nbytes = iio_buffer_push(txbuf);
        if (nbytes < 0) {
            usleep(1000);
            continue;
        }

        g_stats.tx_buffers++;
    }

    iio_buffer_destroy(txbuf);
    printf("[TX Thread] Stopped\n");
    return NULL;
}

/* Main */
int main(int argc, char **argv) {
    printf("========================================\n");
    printf("Target Emulator for ADALM-Pluto\n");
    printf("========================================\n");
    printf("RX: %.1f MHz, TX: %.1f MHz\n",
           g_config.rx_freq_hz / 1e6, g_config.tx_freq_hz / 1e6);
    printf("Delay: %zu ms, Doppler: %.0f Hz, Gain: %.1f\n",
           g_config.delay_ms, g_config.doppler_hz, g_config.amplitude_gain);
    printf("========================================\n\n");

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Create IIO context */
    struct iio_context *ctx = iio_create_local_context();
    if (!ctx) ctx = iio_create_network_context("192.168.2.1");
    if (!ctx) {
        fprintf(stderr, "ERROR: Failed to create IIO context\n");
        return 1;
    }

    /* Initialize delay line */
    delay_line_t delay_line;
    size_t delay_samples = (g_config.sample_rate_hz * g_config.delay_ms) / 1000;
    if (delay_line_init(&delay_line, delay_samples) < 0) {
        fprintf(stderr, "ERROR: Failed to initialize delay line\n");
        iio_context_destroy(ctx);
        return 1;
    }

    /* Start threads */
    pthread_t rx_tid, tx_tid;

    rx_thread_args_t rx_args = { .ctx = ctx, .delay_line = &delay_line };
    tx_thread_args_t tx_args = { .ctx = ctx, .delay_line = &delay_line };

    pthread_create(&rx_tid, NULL, rx_thread_func, &rx_args);
    pthread_create(&tx_tid, NULL, tx_thread_func, &tx_args);

    /* Statistics loop */
    uint64_t last_rx = 0, last_tx = 0;

    while (g_running) {
        sleep(5);

        uint64_t rx_delta = g_stats.rx_buffers - last_rx;
        uint64_t tx_delta = g_stats.tx_buffers - last_tx;

        printf("[Stats] RX: %llu (+%llu), TX: %llu (+%llu)\n",
               (unsigned long long)g_stats.rx_buffers,
               (unsigned long long)rx_delta,
               (unsigned long long)g_stats.tx_buffers,
               (unsigned long long)tx_delta);

        last_rx = g_stats.rx_buffers;
        last_tx = g_stats.tx_buffers;
    }

    /* Cleanup */
    pthread_join(rx_tid, NULL);
    pthread_join(tx_tid, NULL);

    delay_line_destroy(&delay_line);
    iio_context_destroy(ctx);

    printf("\nShutdown complete\n");
    printf("Total RX buffers: %llu\n", (unsigned long long)g_stats.rx_buffers);
    printf("Total TX buffers: %llu\n", (unsigned long long)g_stats.tx_buffers);

    return 0;
}
```

---

## Build System Configuration

### Makefile for Cross-Compilation

```makefile
# Makefile for ARM Applications on Pluto

# Cross-compiler
CROSS_COMPILE ?= arm-linux-gnueabihf-
CC = $(CROSS_COMPILE)gcc
STRIP = $(CROSS_COMPILE)strip

# Optimization flags
CFLAGS = -Wall -Wextra -O3 -std=gnu99
CFLAGS += -march=armv7-a -mtune=cortex-a9 -mfpu=neon -mfloat-abi=hard
CFLAGS += -ffast-math -funroll-loops

# Libraries
LDFLAGS = -liio -lpthread -lm

# Targets
TARGET = target_emulator
SRC = target_emulator.c

# Pluto connection
PLUTO_IP ?= pluto.local
PLUTO_USER ?= root
PLUTO_PASS ?= analog

.PHONY: all clean deploy

all: $(TARGET)

$(TARGET): $(SRC)
	@echo "Cross-compiling for ARM..."
	$(CC) $(CFLAGS) -o $(TARGET) $(SRC) $(LDFLAGS)
	$(STRIP) $(TARGET)
	@echo "Build complete: $(TARGET)"
	@file $(TARGET)
	@ls -lh $(TARGET)

clean:
	rm -f $(TARGET)

deploy: $(TARGET)
	@echo "Deploying to Pluto..."
	@if command -v sshpass >/dev/null; then \
		sshpass -p $(PLUTO_PASS) scp $(TARGET) $(PLUTO_USER)@$(PLUTO_IP):/root/; \
		sshpass -p $(PLUTO_PASS) ssh $(PLUTO_USER)@$(PLUTO_IP) "chmod +x /root/$(TARGET)"; \
	else \
		scp $(TARGET) $(PLUTO_USER)@$(PLUTO_IP):/root/; \
		ssh $(PLUTO_USER)@$(PLUTO_IP) "chmod +x /root/$(TARGET)"; \
	fi
	@echo "Deployment complete! Run: ssh $(PLUTO_USER)@$(PLUTO_IP)"
```

### Docker Build Environment

For Windows or systems without cross-compiler:

```dockerfile
# Dockerfile for Pluto cross-compilation
FROM debian:bullseye

RUN apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf \
    libiio-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
```

Build script (`build-with-docker.sh`):

```bash
#!/bin/bash
docker build -t pluto-builder .
docker run --rm -v $(pwd):/build pluto-builder make
```

---

## Summary

This guide provides a complete reference for building high-performance, multi-core applications on the ADALM-Pluto using the LibIIO stack. Key takeaways:

1. **LibIIO Fundamentals**: Context creation, device discovery, channel configuration
2. **Multi-Core Architecture**: Producer-consumer with core pinning and priority scheduling
3. **Lock-Free Communication**: Cache-aligned ring buffer with atomic operations
4. **DMA Operations**: Blocking refill for RX, push for TX, zero-copy design
5. **Memory Management**: Pre-allocated pools, fast memcpy, cache-line alignment
6. **Delay Line**: Circular buffer with modulation effects
7. **Performance**: Minimize syscalls, avoid allocations, leverage compiler optimizations
8. **Complete Example**: Target emulator with RX/TX pipeline

**Next Steps**:
- Adapt target emulator for your specific application
- Profile with `perf` to identify bottlenecks
- Experiment with NEON intrinsics for further optimization
- Integrate with existing VITA49 streamer for network control

**References**:
- LibIIO Documentation: https://analogdevicesinc.github.io/libiio/
- AD9361 Datasheet: https://www.analog.com/media/en/technical-documentation/data-sheets/AD9361.pdf
- ARM Cortex-A9 TRM: https://developer.arm.com/documentation/ddi0388/latest/
