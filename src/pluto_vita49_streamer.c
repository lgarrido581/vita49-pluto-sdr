/*
 * VITA49 Standalone Streamer for ADALM-Pluto (C Implementation)
 *
 * A lightweight VITA49 IQ streamer that runs directly on the Pluto ARM processor.
 * Uses libiio for SDR control and standard sockets for UDP streaming.
 *
 * Features:
 * - Receives configuration via VITA49 Context packets (UDP port 4990)
 * - Streams IQ samples via VITA49 Data packets (UDP port 4991)
 * - Zero dependencies beyond libiio (already on Pluto)
 * - Minimal memory footprint (~2 MB)
 * - Supports multiple simultaneous receivers
 *
 * Compilation:
 *   arm-linux-gnueabihf-gcc -o vita49_streamer pluto_vita49_streamer.c -liio -lpthread
 *
 * Usage:
 *   ./vita49_streamer --dest 192.168.2.100
 *
 * Author: VITA49-Pluto Project
 * License: MIT
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <unistd.h>
#include <pthread.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <signal.h>
#include <iio.h>

/* Configuration */
#define DEFAULT_FREQ_HZ         2400000000ULL   /* 2.4 GHz */
#define DEFAULT_RATE_HZ         30000000        /* 30 MSPS */
#define DEFAULT_GAIN_DB         20.0
#define DEFAULT_BUFFER_SIZE     16384           /* Samples per buffer */
#define CONTROL_PORT            4990            /* Config reception port */
#define DATA_PORT               4991            /* Data streaming port */
#define CONTEXT_INTERVAL        100             /* Send context every N packets */
#define MAX_SUBSCRIBERS         16              /* Max simultaneous receivers */

/* Subscriber Management Configuration */
#define SUBSCRIBER_TIMEOUT_US       30000000    /* 30 seconds */
#define MAX_CONSECUTIVE_FAILURES    10          /* Remove after 10 failures */
#define SUBSCRIBER_CLEANUP_INTERVAL 100         /* Check every 100 packets */

/* Buffer Timing Configuration (Rate Control) */
#define BUFFER_TIME_US              5000        /* Target 5ms of samples per buffer */
#define MIN_BUFFER_SAMPLES          4096        /* Minimum for efficiency */
#define MAX_BUFFER_SAMPLES          65536       /* Maximum for memory/latency */
#define BUFFER_TIME_TOLERANCE       0.1         /* 10% tolerance on timing */

/* Ring Buffer Configuration (Producer-Consumer FIFO) */
#define RING_BUFFER_SLOTS           16          /* Number of buffer slots */
#define RING_BUFFER_SLOT_SAMPLES    65536       /* Max samples per slot */

/* MTU and Packet Size Configuration */
#define MTU_STANDARD            1500            /* Standard Ethernet */
#define MTU_JUMBO               9000            /* Jumbo frames */
#define IP_HEADER_SIZE          20              /* IPv4 header */
#define UDP_HEADER_SIZE         8               /* UDP header */
#define VITA49_HEADER_SIZE      20              /* VRT header + stream ID + timestamp */
#define VITA49_TRAILER_SIZE     4               /* VRT trailer */

#define VITA49_OVERHEAD         (VITA49_HEADER_SIZE + VITA49_TRAILER_SIZE)
#define IP_UDP_OVERHEAD         (IP_HEADER_SIZE + UDP_HEADER_SIZE)
#define MAX_PACKET_BUFFER       16384           /* Support jumbo frames */

/* VITA49 Packet Types */
#define VRT_PKT_TYPE_DATA       0x1             /* IF Data with Stream ID */
#define VRT_PKT_TYPE_CONTEXT    0x4             /* Context packet */
#define VRT_TSI_UTC             0x1             /* UTC timestamp */
#define VRT_TSF_PICOSECONDS     0x2             /* Picosecond fractional time */

/* Global state */
static volatile bool g_running = true;
static pthread_mutex_t g_subscribers_mutex = PTHREAD_MUTEX_INITIALIZER;
static size_t g_samples_per_packet = 360;  /* Will be calculated at runtime based on MTU */

/* Subscriber list */
typedef struct {
    struct sockaddr_in addr;
    bool active;

    /* Health tracking */
    int consecutive_failures;
    uint64_t packets_sent;
    uint64_t bytes_sent;
    uint64_t last_seen_us;       /* Last successful send timestamp */
    uint64_t first_seen_us;      /* When subscriber was added */

    /* Statistics */
    uint64_t total_failures;
} subscriber_t;

static subscriber_t g_subscribers[MAX_SUBSCRIBERS];
static int g_subscriber_count = 0;

/* SDR Configuration */
typedef struct {
    uint64_t center_freq_hz;
    uint32_t sample_rate_hz;
    uint32_t bandwidth_hz;
    double gain_db;
    bool config_changed;  /* Flag to signal streaming thread to reconfigure */
    pthread_mutex_t mutex;
} sdr_config_t;

static sdr_config_t g_sdr_config = {
    .center_freq_hz = DEFAULT_FREQ_HZ,
    .sample_rate_hz = DEFAULT_RATE_HZ,
    .bandwidth_hz = DEFAULT_RATE_HZ * 0.8,
    .gain_db = DEFAULT_GAIN_DB,
    .config_changed = false,
    .mutex = PTHREAD_MUTEX_INITIALIZER
};

/* Statistics */
typedef struct {
    /* Packet stats */
    uint64_t packets_sent;
    uint64_t bytes_sent;
    uint32_t contexts_sent;
    uint32_t reconfigs;

    /* Health monitoring */
    uint64_t underflows;
    uint64_t overflows;
    uint64_t refill_failures;
    uint64_t send_failures;
    uint64_t timestamp_jumps;
    uint64_t last_timestamp_us;

    /* Performance metrics (refill thread timing) */
    uint64_t total_loop_time_us;
    uint64_t loop_iterations;

    /* FIFO buffer metrics */
    uint64_t buffer_refills;        /* Total buffer refills from IIO */
    uint64_t buffer_overruns;       /* Times ring buffer was full (samples lost) */

    pthread_mutex_t mutex;
} stream_statistics_t;

static stream_statistics_t g_stats = {0};

/*
 * Lock-free single-producer single-consumer ring buffer
 * Producer: refill thread (grabs samples from IIO)
 * Consumer: send thread (sends UDP packets)
 */
typedef struct {
    int16_t *data;              /* Sample data (I/Q interleaved) */
    size_t num_samples;         /* Number of I/Q pairs */
    uint64_t timestamp_ns;      /* Timestamp when buffer was acquired */
    uint32_t sequence;          /* Sequence number for tracking */
} ring_slot_t;

typedef struct {
    ring_slot_t slots[RING_BUFFER_SLOTS];
    size_t slot_capacity;       /* Max samples per slot */

    /* Atomic indices for lock-free operation */
    _Atomic size_t write_idx;   /* Next slot to write (producer) */
    _Atomic size_t read_idx;    /* Next slot to read (consumer) */

    /* Statistics */
    _Atomic uint64_t overflows;     /* Producer couldn't write (buffer full) */
    _Atomic uint64_t underflows;    /* Consumer couldn't read (buffer empty) */
    _Atomic uint64_t total_samples; /* Total samples passed through */
} ring_buffer_t;

static ring_buffer_t g_ring = {0};

/* VITA49 Packet Structures */
#pragma pack(push, 1)
typedef struct {
    uint32_t header;
    uint32_t stream_id;
    uint32_t timestamp_int;
    uint64_t timestamp_frac;
    /* Payload follows */
} vrt_data_header_t;

typedef struct {
    uint32_t header;
    uint32_t stream_id;
    uint32_t timestamp_int;
    uint64_t timestamp_frac;
    uint32_t cif;
    /* Context fields follow */
} vrt_context_header_t;
#pragma pack(pop)

/* Function prototypes */
static void signal_handler(int sig);
static void add_subscriber(struct sockaddr_in *addr);
static int send_to_subscriber(int sock, uint8_t *buf, size_t len, subscriber_t *sub);
static void cleanup_dead_subscribers(void);
static void broadcast_to_subscribers(int sock, uint8_t *buf, size_t len);
static uint64_t get_timestamp_us(void);
static uint64_t get_timestamp_ns(void);
static size_t calculate_optimal_samples_per_packet(size_t mtu);
static size_t calculate_buffer_size(uint32_t sample_rate_hz);

/* Ring buffer functions */
static int ring_buffer_init(size_t slot_capacity);
static void ring_buffer_destroy(void);
static size_t ring_buffer_write_available(void);
static size_t ring_buffer_read_available(void);
static int ring_buffer_write(const int16_t *samples, size_t num_samples, uint64_t timestamp_ns, uint32_t sequence);
static int ring_buffer_read(ring_slot_t **slot_out);
static void ring_buffer_read_done(void);
static void ring_buffer_get_stats(uint64_t *overflows, uint64_t *underflows, uint64_t *total_samples, size_t *fill_level);

static void encode_context_packet(uint8_t *buf, size_t *len);
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data, size_t num_samples, uint8_t *packet_count);
static void *control_thread(void *arg);
static void *refill_thread(void *arg);  /* Producer: grabs samples from IIO */
static void *send_thread(void *arg);    /* Consumer: sends UDP packets */
static int configure_sdr(struct iio_context *ctx, struct iio_device *dev);

/* Utility functions */
static inline uint32_t htonl_custom(uint32_t x) {
    return htonl(x);
}

static inline uint64_t htonll(uint64_t x) {
    return ((uint64_t)htonl(x & 0xFFFFFFFF) << 32) | htonl(x >> 32);
}

/* Get current timestamp in microseconds */
static uint64_t get_timestamp_us(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000000ULL + tv.tv_usec;
}

/* Get current timestamp in nanoseconds (for precise rate control) */
static uint64_t get_timestamp_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/**
 * Calculate optimal buffer size for given sample rate
 *
 * The buffer should hold BUFFER_TIME_US worth of samples.
 * At 30 MSPS: 5ms × 30M = 150,000 samples (too large)
 * So we cap at MAX_BUFFER_SAMPLES and adjust timing accordingly.
 *
 * @param sample_rate_hz  Current sample rate in Hz
 * @return Optimal buffer size in samples (I/Q pairs)
 */
static size_t calculate_buffer_size(uint32_t sample_rate_hz) {
    /* Calculate samples for target buffer time */
    size_t target_samples = (size_t)((uint64_t)sample_rate_hz * BUFFER_TIME_US / 1000000);

    /* Clamp to valid range */
    if (target_samples < MIN_BUFFER_SAMPLES) {
        target_samples = MIN_BUFFER_SAMPLES;
    }
    if (target_samples > MAX_BUFFER_SAMPLES) {
        target_samples = MAX_BUFFER_SAMPLES;
    }

    /* Round to power of 2 for DMA efficiency */
    size_t power_of_2 = MIN_BUFFER_SAMPLES;
    while (power_of_2 < target_samples && power_of_2 < MAX_BUFFER_SAMPLES) {
        power_of_2 *= 2;
    }

    return power_of_2;
}

/**
 * Initialize ring buffer
 * @param slot_capacity  Maximum samples per slot (should match IIO buffer size)
 * @return 0 on success, -1 on failure
 */
static int ring_buffer_init(size_t slot_capacity) {
    g_ring.slot_capacity = slot_capacity;
    atomic_store(&g_ring.write_idx, 0);
    atomic_store(&g_ring.read_idx, 0);
    atomic_store(&g_ring.overflows, 0);
    atomic_store(&g_ring.underflows, 0);
    atomic_store(&g_ring.total_samples, 0);

    /* Allocate data buffers for each slot */
    for (int i = 0; i < RING_BUFFER_SLOTS; i++) {
        /* 4 bytes per sample: 2 bytes I + 2 bytes Q */
        g_ring.slots[i].data = (int16_t *)malloc(slot_capacity * 4);
        if (!g_ring.slots[i].data) {
            /* Cleanup on failure */
            for (int j = 0; j < i; j++) {
                free(g_ring.slots[j].data);
            }
            return -1;
        }
        g_ring.slots[i].num_samples = 0;
        g_ring.slots[i].timestamp_ns = 0;
        g_ring.slots[i].sequence = 0;
    }

    printf("[RingBuffer] Initialized: %d slots x %zu samples = %zu MB\n",
           RING_BUFFER_SLOTS, slot_capacity,
           (RING_BUFFER_SLOTS * slot_capacity * 4) / (1024 * 1024));

    return 0;
}

/**
 * Destroy ring buffer and free memory
 */
static void ring_buffer_destroy(void) {
    for (int i = 0; i < RING_BUFFER_SLOTS; i++) {
        if (g_ring.slots[i].data) {
            free(g_ring.slots[i].data);
            g_ring.slots[i].data = NULL;
        }
    }
}

/**
 * Get number of slots available for writing
 */
static size_t ring_buffer_write_available(void) {
    size_t w = atomic_load(&g_ring.write_idx);
    size_t r = atomic_load(&g_ring.read_idx);

    /* Leave one slot empty to distinguish full from empty */
    size_t used = (w >= r) ? (w - r) : (RING_BUFFER_SLOTS - r + w);
    return RING_BUFFER_SLOTS - 1 - used;
}

/**
 * Get number of slots available for reading
 */
static size_t ring_buffer_read_available(void) {
    size_t w = atomic_load(&g_ring.write_idx);
    size_t r = atomic_load(&g_ring.read_idx);
    return (w >= r) ? (w - r) : (RING_BUFFER_SLOTS - r + w);
}

/**
 * Write samples to ring buffer (called by refill thread)
 *
 * @param samples       Pointer to I/Q sample data
 * @param num_samples   Number of I/Q pairs
 * @param timestamp_ns  Acquisition timestamp
 * @param sequence      Sequence number
 * @return 0 on success, -1 if buffer full (overflow)
 */
static int ring_buffer_write(const int16_t *samples, size_t num_samples,
                             uint64_t timestamp_ns, uint32_t sequence) {
    if (ring_buffer_write_available() == 0) {
        atomic_fetch_add(&g_ring.overflows, 1);
        return -1;  /* Buffer full - overflow */
    }

    size_t idx = atomic_load(&g_ring.write_idx);
    ring_slot_t *slot = &g_ring.slots[idx];

    /* Copy data */
    size_t copy_samples = (num_samples > g_ring.slot_capacity) ?
                          g_ring.slot_capacity : num_samples;
    memcpy(slot->data, samples, copy_samples * 4);
    slot->num_samples = copy_samples;
    slot->timestamp_ns = timestamp_ns;
    slot->sequence = sequence;

    /* Memory barrier before updating index */
    atomic_thread_fence(memory_order_release);

    /* Advance write index */
    atomic_store(&g_ring.write_idx, (idx + 1) % RING_BUFFER_SLOTS);

    atomic_fetch_add(&g_ring.total_samples, copy_samples);

    return 0;
}

/**
 * Read samples from ring buffer (called by send thread)
 *
 * @param slot_out  Pointer to receive slot data (do not free!)
 * @return 0 on success, -1 if buffer empty (underflow)
 */
static int ring_buffer_read(ring_slot_t **slot_out) {
    if (ring_buffer_read_available() == 0) {
        atomic_fetch_add(&g_ring.underflows, 1);
        return -1;  /* Buffer empty - underflow */
    }

    size_t idx = atomic_load(&g_ring.read_idx);

    /* Memory barrier before reading data */
    atomic_thread_fence(memory_order_acquire);

    *slot_out = &g_ring.slots[idx];

    return 0;
}

/**
 * Mark current read slot as consumed (advance read pointer)
 * Call after processing data from ring_buffer_read()
 */
static void ring_buffer_read_done(void) {
    size_t idx = atomic_load(&g_ring.read_idx);
    atomic_store(&g_ring.read_idx, (idx + 1) % RING_BUFFER_SLOTS);
}

/**
 * Get ring buffer statistics
 */
static void ring_buffer_get_stats(uint64_t *overflows, uint64_t *underflows,
                                  uint64_t *total_samples, size_t *fill_level) {
    *overflows = atomic_load(&g_ring.overflows);
    *underflows = atomic_load(&g_ring.underflows);
    *total_samples = atomic_load(&g_ring.total_samples);
    *fill_level = ring_buffer_read_available();
}

/* Calculate optimal samples per packet to fit within MTU */
static size_t calculate_optimal_samples_per_packet(size_t mtu) {
    /* Available payload after all headers */
    size_t available_bytes = mtu - IP_UDP_OVERHEAD - VITA49_OVERHEAD;

    /* Each sample is 2 * int16_t (I + Q) */
    size_t sample_bytes = 2 * sizeof(int16_t);

    /* Calculate max samples that fit */
    size_t max_samples = available_bytes / sample_bytes;

    /* Align to ensure 32-bit boundary (VITA49 requirement) */
    /* Round down to nearest even number */
    size_t aligned_samples = (max_samples / 2) * 2;

    return aligned_samples;
}

/* Signal handler for graceful shutdown */
static void signal_handler(int sig) {
    (void)sig;
    printf("\nShutting down...\n");
    g_running = false;
}

/* Add subscriber to list */
static void add_subscriber(struct sockaddr_in *addr) {
    pthread_mutex_lock(&g_subscribers_mutex);

    /* Check if already exists */
    for (int i = 0; i < g_subscriber_count; i++) {
        if (g_subscribers[i].addr.sin_addr.s_addr == addr->sin_addr.s_addr &&
            g_subscribers[i].addr.sin_port == addr->sin_port) {

            /* Reactivate if was inactive */
            if (!g_subscribers[i].active) {
                g_subscribers[i].active = true;
                g_subscribers[i].consecutive_failures = 0;
                char ip_str[INET_ADDRSTRLEN];
                inet_ntop(AF_INET, &addr->sin_addr, ip_str, INET_ADDRSTRLEN);
                printf("[Control] Reactivated subscriber: %s:%d\n",
                       ip_str, ntohs(addr->sin_port));
            }

            pthread_mutex_unlock(&g_subscribers_mutex);
            return;
        }
    }

    /* Add new subscriber */
    if (g_subscriber_count < MAX_SUBSCRIBERS) {
        subscriber_t *sub = &g_subscribers[g_subscriber_count];
        sub->addr = *addr;
        sub->active = true;
        sub->consecutive_failures = 0;
        sub->packets_sent = 0;
        sub->bytes_sent = 0;
        sub->last_seen_us = get_timestamp_us();
        sub->first_seen_us = sub->last_seen_us;
        sub->total_failures = 0;

        g_subscriber_count++;

        char ip_str[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &addr->sin_addr, ip_str, INET_ADDRSTRLEN);
        printf("[Control] Added subscriber: %s:%d (total: %d)\n",
               ip_str, ntohs(addr->sin_port), g_subscriber_count);
    } else {
        fprintf(stderr, "[Control] ERROR: Maximum subscribers reached (%d)\n",
                MAX_SUBSCRIBERS);
    }

    pthread_mutex_unlock(&g_subscribers_mutex);
}

/* Send packet to individual subscriber with error handling */
static int send_to_subscriber(int sock, uint8_t *buf, size_t len, subscriber_t *sub) {
    ssize_t sent = sendto(sock, buf, len, 0,
                         (struct sockaddr *)&sub->addr,
                         sizeof(sub->addr));

    if (sent < 0) {
        sub->consecutive_failures++;
        sub->total_failures++;

        pthread_mutex_lock(&g_stats.mutex);
        g_stats.send_failures++;
        pthread_mutex_unlock(&g_stats.mutex);

        /* Log periodic failures (every 10) */
        if (sub->consecutive_failures % 10 == 0) {
            char ip_str[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &sub->addr.sin_addr, ip_str, INET_ADDRSTRLEN);
            fprintf(stderr, "[Streaming] WARNING: Send to %s:%d failed %d times (total: %llu)\n",
                   ip_str, ntohs(sub->addr.sin_port), sub->consecutive_failures,
                   (unsigned long long)sub->total_failures);
        }

        /* Mark inactive after threshold */
        if (sub->consecutive_failures >= MAX_CONSECUTIVE_FAILURES) {
            char ip_str[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &sub->addr.sin_addr, ip_str, INET_ADDRSTRLEN);
            fprintf(stderr, "[Streaming] Marking subscriber %s:%d as inactive after %d failures\n",
                   ip_str, ntohs(sub->addr.sin_port), sub->consecutive_failures);
            sub->active = false;
        }

        return -1;
    }

    /* Success - reset failure counter and update stats */
    sub->consecutive_failures = 0;
    sub->last_seen_us = get_timestamp_us();
    sub->packets_sent++;
    sub->bytes_sent += len;

    return 0;
}

/* Remove dead subscribers from list */
static void cleanup_dead_subscribers(void) {
    uint64_t current_time = get_timestamp_us();
    int removed = 0;

    pthread_mutex_lock(&g_subscribers_mutex);

    /* Compact array, removing inactive subscribers */
    int write_idx = 0;
    for (int read_idx = 0; read_idx < g_subscriber_count; read_idx++) {
        subscriber_t *sub = &g_subscribers[read_idx];

        /* Check if subscriber should be removed */
        bool should_remove = false;

        if (!sub->active) {
            should_remove = true;
        } else if (sub->last_seen_us > 0 &&
                  (current_time - sub->last_seen_us) > SUBSCRIBER_TIMEOUT_US) {
            /* Timeout - no successful sends in 30 seconds */
            char ip_str[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &sub->addr.sin_addr, ip_str, INET_ADDRSTRLEN);
            fprintf(stderr, "[Streaming] Removing subscriber %s:%d (timeout)\n",
                   ip_str, ntohs(sub->addr.sin_port));
            should_remove = true;
        }

        if (!should_remove) {
            /* Keep this subscriber */
            if (write_idx != read_idx) {
                g_subscribers[write_idx] = g_subscribers[read_idx];
            }
            write_idx++;
        } else {
            removed++;
        }
    }

    g_subscriber_count = write_idx;

    pthread_mutex_unlock(&g_subscribers_mutex);

    if (removed > 0) {
        printf("[Streaming] Removed %d dead subscriber(s), %d active remain\n",
               removed, g_subscriber_count);
    }
}

/* Broadcast packet to all active subscribers */
static void broadcast_to_subscribers(int sock, uint8_t *buf, size_t len) {
    pthread_mutex_lock(&g_subscribers_mutex);

    for (int i = 0; i < g_subscriber_count; i++) {
        if (g_subscribers[i].active) {
            send_to_subscriber(sock, buf, len, &g_subscribers[i]);
        }
    }

    pthread_mutex_unlock(&g_subscribers_mutex);
}

/* Encode VITA49 Context packet */
static void encode_context_packet(uint8_t *buf, size_t *len) {
    vrt_context_header_t *hdr = (vrt_context_header_t *)buf;
    uint8_t *payload = buf + sizeof(vrt_context_header_t);
    size_t payload_len = 0;

    pthread_mutex_lock(&g_sdr_config.mutex);
    uint64_t freq = g_sdr_config.center_freq_hz;
    uint32_t rate = g_sdr_config.sample_rate_hz;
    uint32_t bw = g_sdr_config.bandwidth_hz;
    double gain = g_sdr_config.gain_db;
    pthread_mutex_unlock(&g_sdr_config.mutex);

    /* Timestamp */
    uint64_t ts_us = get_timestamp_us();
    uint32_t ts_int = ts_us / 1000000;
    uint64_t ts_frac = (ts_us % 1000000) * 1000000ULL;  /* Convert to picoseconds */

    /* Get current health status */
    pthread_mutex_lock(&g_stats.mutex);
    uint64_t underflows = g_stats.underflows;
    uint64_t overflows = g_stats.overflows;
    pthread_mutex_unlock(&g_stats.mutex);

    /* Context Indicator Field (CIF) */
    uint32_t cif = 0;
    cif |= (1 << 29);  /* bandwidth */
    cif |= (1 << 27);  /* rf_reference_frequency */
    cif |= (1 << 23);  /* gain */
    cif |= (1 << 21);  /* sample_rate */
    cif |= (1 << 19);  /* state_event_indicators */

    /* Encode context fields in DESCENDING CIF bit order (VITA49 requirement)
     * Bit 29: Bandwidth
     * Bit 27: RF Reference Frequency
     * Bit 23: Gain (comes BEFORE bit 21!)
     * Bit 21: Sample Rate
     * Bit 19: State/Event Indicators
     *
     * NOTE: Use memcpy to avoid alignment issues with uint64_t at non-8-byte offsets
     */
    int64_t bw_fixed = ((int64_t)bw * (1 << 20));
    int64_t freq_fixed = ((int64_t)freq * (1 << 20));
    int64_t rate_fixed = ((int64_t)rate * (1 << 20));
    int16_t gain_fixed = (int16_t)(gain * 128);

    /* Bit 29: Bandwidth (64-bit, 20-bit radix) */
    uint64_t bw_be = htonll(bw_fixed);
    memcpy(payload + payload_len, &bw_be, 8);
    payload_len += 8;

    /* Bit 27: RF Reference Frequency (64-bit, 20-bit radix) */
    uint64_t freq_be = htonll(freq_fixed);
    memcpy(payload + payload_len, &freq_be, 8);
    payload_len += 8;

    /* Bit 23: Gain - Stage 1 and Stage 2 (two 16-bit values, 7-bit radix) */
    uint16_t gain_be = htons(gain_fixed);
    memcpy(payload + payload_len, &gain_be, 2);
    payload_len += 2;
    uint16_t zero = 0;
    memcpy(payload + payload_len, &zero, 2);  /* Stage 2 (unused) */
    payload_len += 2;

    /* Bit 21: Sample Rate (64-bit, 20-bit radix) */
    uint64_t rate_be = htonll(rate_fixed);
    memcpy(payload + payload_len, &rate_be, 8);
    payload_len += 8;

    /* Bit 19: State/Event Indicators (32-bit field)
     * Bit 31: Calibrated Time (1 = time is calibrated)
     * Bit 19: Overrange (1 = overflow detected)
     * Bit 18: Sample Loss (1 = underflow/sample loss detected)
     */
    uint32_t state_event = 0;
    state_event |= (1U << 31);  /* Calibrated Time */
    if (overflows > 0) {
        state_event |= (1 << 19);  /* Overrange indicator */
    }
    if (underflows > 0) {
        state_event |= (1 << 18);  /* Sample Loss indicator */
    }
    uint32_t state_event_be = htonl_custom(state_event);
    memcpy(payload + payload_len, &state_event_be, 4);
    payload_len += 4;

    /* DEBUG: Log what we're encoding */
    static int debug_count = 0;
    if (debug_count++ < 5) {  /* Only log first 5 packets */
        printf("[DEBUG] Encoding context: freq=%.1f MHz, rate=%.1f MSPS, gain=%.1f dB\n",
               freq / 1e6, rate / 1e6, gain);
        printf("[DEBUG] Fixed-point: freq=%lld, rate=%lld, gain=%d\n",
               (long long)freq_fixed, (long long)rate_fixed, gain_fixed);
        printf("[DEBUG] Payload length: %zu bytes\n", payload_len);
    }

    /* Calculate packet size in 32-bit words */
    size_t total_words = 1 + 1 + 1 + 2 + 1 + (payload_len / 4);

    /* Build header */
    uint32_t header = 0;
    header |= (VRT_PKT_TYPE_CONTEXT & 0xF) << 28;
    header |= (VRT_TSI_UTC & 0x3) << 22;
    header |= (VRT_TSF_PICOSECONDS & 0x3) << 20;
    header |= (total_words & 0xFFFF);

    hdr->header = htonl_custom(header);
    hdr->stream_id = htonl_custom(0x01000000);
    hdr->timestamp_int = htonl_custom(ts_int);
    hdr->timestamp_frac = htonll(ts_frac);
    hdr->cif = htonl_custom(cif);

    *len = sizeof(vrt_context_header_t) + payload_len;
}

/* Encode VITA49 Data packet */
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data,
                               size_t num_samples, uint8_t *packet_count) {
    /* Validate buffer won't overflow */
    size_t required_size = sizeof(vrt_data_header_t) +
                          (num_samples * 2 * sizeof(int16_t)) +
                          sizeof(uint32_t);  /* trailer */

    if (required_size > MAX_PACKET_BUFFER) {
        fprintf(stderr, "ERROR: Packet would exceed buffer size (%zu > %d)\n",
                required_size, MAX_PACKET_BUFFER);
        *len = 0;
        return;
    }

    vrt_data_header_t *hdr = (vrt_data_header_t *)buf;
    int16_t *payload = (int16_t *)(buf + sizeof(vrt_data_header_t));

    /* Copy and convert to big-endian */
    for (size_t i = 0; i < num_samples * 2; i++) {
        payload[i] = htons(iq_data[i]);
    }

    size_t payload_bytes = num_samples * 2 * sizeof(int16_t);

    /* Pad to 32-bit boundary */
    size_t padding = (4 - (payload_bytes % 4)) % 4;
    if (padding) {
        memset((uint8_t *)payload + payload_bytes, 0, padding);
        payload_bytes += padding;
    }

    /* Trailer */
    uint32_t *trailer = (uint32_t *)(buf + sizeof(vrt_data_header_t) + payload_bytes);
    *trailer = htonl_custom(0x40000000);  /* valid_data = 1 */

    /* Calculate packet size */
    size_t total_words = 1 + 1 + 1 + 2 + (payload_bytes / 4) + 1;

    /* Timestamp */
    uint64_t ts_us = get_timestamp_us();
    uint32_t ts_int = ts_us / 1000000;
    uint64_t ts_frac = (ts_us % 1000000) * 1000000ULL;

    /* Build header */
    uint32_t header = 0;
    header |= (VRT_PKT_TYPE_DATA & 0xF) << 28;
    header |= (1 << 26);  /* Trailer present */
    header |= (VRT_TSI_UTC & 0x3) << 22;
    header |= (VRT_TSF_PICOSECONDS & 0x3) << 20;
    header |= ((*packet_count) & 0xF) << 16;
    header |= (total_words & 0xFFFF);

    hdr->header = htonl_custom(header);
    hdr->stream_id = htonl_custom(0x01000000);
    hdr->timestamp_int = htonl_custom(ts_int);
    hdr->timestamp_frac = htonll(ts_frac);

    *len = sizeof(vrt_data_header_t) + payload_bytes + sizeof(uint32_t);
    *packet_count = (*packet_count + 1) & 0xF;
}

/* Parse VITA49 Context packet and extract configuration */
static int parse_context_packet(const uint8_t *buf, size_t len,
                                uint64_t *freq_hz, uint32_t *rate_hz, double *gain_db) {
    if (len < 28) return -1;  /* Minimum context packet size */

    /* Skip VRT header (4 bytes) and stream ID (4 bytes) */
    const uint8_t *p = buf + 8;

    /* Skip timestamps (12 bytes) */
    p += 12;

    /* Read Context Indicator Field (CIF) */
    uint32_t cif = ntohl(*(uint32_t *)p);
    p += 4;

    /* Parse context fields in descending CIF bit order (VITA49 spec) */

    /* Bit 29: Bandwidth (not currently used, but must skip if present) */
    if (cif & (1 << 29)) {
        p += 8;  /* Skip 64-bit bandwidth field */
    }

    /* Bit 27: RF Reference Frequency */
    if (cif & (1 << 27)) {
        /* Read 64-bit signed fixed-point value (20-bit radix) */
        uint32_t high = ntohl(*(uint32_t *)p);
        uint32_t low = ntohl(*(uint32_t *)(p + 4));
        int64_t freq_fixed = ((int64_t)high << 32) | (int64_t)low;
        *freq_hz = (uint64_t)(freq_fixed / (1 << 20));  /* Divide by 2^20 */
        p += 8;
    }

    /* Bit 23: Gain (comes before bit 21!) */
    if (cif & (1 << 23)) {
        /* Read 16-bit signed fixed-point value (7-bit radix) */
        int16_t gain_fixed = (int16_t)ntohs(*(uint16_t *)p);
        *gain_db = gain_fixed / 128.0;  /* Divide by 2^7 */
        p += 4;  /* Skip both stage1 and stage2 (4 bytes total) */
    }

    /* Bit 21: Sample Rate */
    if (cif & (1 << 21)) {
        /* Read 64-bit signed fixed-point value (20-bit radix) */
        uint32_t high = ntohl(*(uint32_t *)p);
        uint32_t low = ntohl(*(uint32_t *)(p + 4));
        int64_t rate_fixed = ((int64_t)high << 32) | (int64_t)low;
        *rate_hz = (uint32_t)(rate_fixed / (1 << 20));  /* Divide by 2^20 */
        p += 8;
    }

    return 0;
}

/* Control thread - receives configuration */
static void *control_thread(void *arg) {
    int *sock_fd = (int *)arg;
    uint8_t buf[4096];
    struct sockaddr_in client_addr;
    socklen_t client_len = sizeof(client_addr);

    /* Set socket timeout so we can check g_running periodically */
    struct timeval timeout;
    timeout.tv_sec = 1;  /* 1 second timeout */
    timeout.tv_usec = 0;
    setsockopt(*sock_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    printf("[Control] Listening on port %d\n", CONTROL_PORT);
    printf("[Control] Default config: %.3f MHz, %.1f MSPS, %.1f dB\n",
           g_sdr_config.center_freq_hz / 1e6,
           g_sdr_config.sample_rate_hz / 1e6,
           g_sdr_config.gain_db);

    while (g_running) {
        ssize_t recv_len = recvfrom(*sock_fd, buf, sizeof(buf), 0,
                                   (struct sockaddr *)&client_addr, &client_len);

        if (recv_len < 0) continue;  /* Timeout or error, check g_running */

        char ip_str[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &client_addr.sin_addr, ip_str, INET_ADDRSTRLEN);
        printf("\n[Control] ========================================\n");
        printf("[Control] Received config from %s (%zd bytes)\n", ip_str, recv_len);

        /* Parse context packet */
        uint64_t new_freq = g_sdr_config.center_freq_hz;
        uint32_t new_rate = g_sdr_config.sample_rate_hz;
        double new_gain = g_sdr_config.gain_db;

        if (parse_context_packet(buf, recv_len, &new_freq, &new_rate, &new_gain) == 0) {
            bool changed = false;

            /* Check what changed and update */
            pthread_mutex_lock(&g_sdr_config.mutex);

            if (new_freq != g_sdr_config.center_freq_hz) {
                printf("[Control] Frequency: %.3f MHz -> %.3f MHz\n",
                       g_sdr_config.center_freq_hz / 1e6, new_freq / 1e6);
                g_sdr_config.center_freq_hz = new_freq;
                changed = true;
            }

            if (new_rate != g_sdr_config.sample_rate_hz) {
                printf("[Control] Sample Rate: %.1f MSPS -> %.1f MSPS\n",
                       g_sdr_config.sample_rate_hz / 1e6, new_rate / 1e6);
                g_sdr_config.sample_rate_hz = new_rate;
                g_sdr_config.bandwidth_hz = new_rate * 0.8;
                changed = true;
            }

            if (new_gain != g_sdr_config.gain_db) {
                printf("[Control] Gain: %.1f dB -> %.1f dB\n",
                       g_sdr_config.gain_db, new_gain);
                g_sdr_config.gain_db = new_gain;
                changed = true;
            }

            /* Set flag to notify streaming thread to apply changes */
            if (changed) {
                g_sdr_config.config_changed = true;
            }

            pthread_mutex_unlock(&g_sdr_config.mutex);

            if (!changed) {
                printf("[Control] No changes (same as current config)\n");
            } else {
                printf("[Control] Configuration updated - streaming thread will apply changes\n");
            }
        } else {
            printf("[Control] Warning: Failed to parse context packet\n");
        }

        /* Add as subscriber */
        client_addr.sin_port = htons(DATA_PORT);
        add_subscriber(&client_addr);
        printf("[Control] Added %s as subscriber (total: %d)\n", ip_str, g_subscriber_count);
        printf("[Control] ========================================\n\n");

        pthread_mutex_lock(&g_stats.mutex);
        g_stats.reconfigs++;
        pthread_mutex_unlock(&g_stats.mutex);
    }

    printf("[Control] Thread stopped\n");
    return NULL;
}

/**
 * Refill thread (Producer) - Grabs samples from IIO as fast as possible
 *
 * This thread NEVER sleeps (except when blocked on iio_buffer_refill).
 * It writes samples to the ring buffer immediately after receiving them.
 * The DMA buffer must be read before the AD9361 overwrites it.
 */
static void *refill_thread(void *arg) {
    struct iio_context *ctx = (struct iio_context *)arg;
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");

    if (!dev) {
        fprintf(stderr, "[Refill] ERROR: Device not found\n");
        return NULL;
    }

    /* Configure SDR */
    if (configure_sdr(ctx, dev) < 0) {
        return NULL;
    }

    /* Calculate and create appropriately sized buffer based on sample rate */
    pthread_mutex_lock(&g_sdr_config.mutex);
    uint32_t current_rate = g_sdr_config.sample_rate_hz;
    pthread_mutex_unlock(&g_sdr_config.mutex);

    size_t buffer_samples = calculate_buffer_size(current_rate);
    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
    if (!rxbuf) {
        fprintf(stderr, "[Refill] ERROR: Failed to create buffer\n");
        return NULL;
    }

    printf("[Refill] Created IIO buffer: %zu samples (%.2f ms at %.1f MSPS)\n",
           buffer_samples,
           (double)buffer_samples * 1000.0 / current_rate,
           current_rate / 1e6);

    uint32_t sequence = 0;
    uint64_t last_config_check_us = get_timestamp_us();

    printf("[Refill] Started - producer thread running\n");

    while (g_running) {
        /* Check for configuration changes every 100ms */
        uint64_t now_us = get_timestamp_us();
        if (now_us - last_config_check_us >= 100000) {
            last_config_check_us = now_us;

            pthread_mutex_lock(&g_sdr_config.mutex);
            bool needs_reconfig = g_sdr_config.config_changed;
            pthread_mutex_unlock(&g_sdr_config.mutex);

            if (needs_reconfig) {
                printf("[Refill] ========================================\n");
                printf("[Refill] Configuration change detected - applying to hardware\n");

                /* Destroy current buffer */
                iio_buffer_destroy(rxbuf);
                rxbuf = NULL;

                /* Get new sample rate for buffer calculation */
                pthread_mutex_lock(&g_sdr_config.mutex);
                uint32_t new_rate = g_sdr_config.sample_rate_hz;
                pthread_mutex_unlock(&g_sdr_config.mutex);

                /* Apply new configuration to SDR hardware */
                if (configure_sdr(ctx, dev) < 0) {
                    fprintf(stderr, "[Refill] ERROR: Failed to apply new configuration\n");

                    /* Try to recreate buffer with old settings */
                    rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
                    if (!rxbuf) {
                        fprintf(stderr, "[Refill] FATAL: Cannot recreate buffer - stopping\n");
                        break;
                    }

                    pthread_mutex_lock(&g_sdr_config.mutex);
                    g_sdr_config.config_changed = false;
                    pthread_mutex_unlock(&g_sdr_config.mutex);
                    continue;
                }

                /* Recalculate buffer size for new sample rate */
                buffer_samples = calculate_buffer_size(new_rate);
                rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
                if (!rxbuf) {
                    fprintf(stderr, "[Refill] FATAL: Failed to recreate buffer - stopping\n");
                    break;
                }

                current_rate = new_rate;

                /* Clear the flag */
                pthread_mutex_lock(&g_sdr_config.mutex);
                g_sdr_config.config_changed = false;
                pthread_mutex_unlock(&g_sdr_config.mutex);

                printf("[Refill] Buffer resized: %zu samples (%.2f ms at %.1f MSPS)\n",
                       buffer_samples,
                       (double)buffer_samples * 1000.0 / new_rate,
                       new_rate / 1e6);
                printf("[Refill] Configuration applied successfully\n");
                printf("[Refill] ========================================\n");

                pthread_mutex_lock(&g_stats.mutex);
                g_stats.reconfigs++;
                pthread_mutex_unlock(&g_stats.mutex);
            }
        }

        /* Refill buffer - BLOCKS until DMA delivers data */
        uint64_t refill_start = get_timestamp_ns();
        ssize_t nbytes = iio_buffer_refill(rxbuf);

        if (nbytes < 0) {
            pthread_mutex_lock(&g_stats.mutex);
            g_stats.refill_failures++;
            pthread_mutex_unlock(&g_stats.mutex);
            usleep(1000);  /* Brief delay before retry */
            continue;
        }

        uint64_t timestamp_ns = get_timestamp_ns();

        /* Get pointer to data */
        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, iio_device_get_channel(dev, 0));
        if (!samples) continue;

        size_t num_samples = nbytes / (2 * sizeof(int16_t));  /* IQ pairs */

        /* Write to ring buffer - MUST NOT BLOCK */
        int ret = ring_buffer_write(samples, num_samples, timestamp_ns, sequence++);

        if (ret < 0) {
            /* Ring buffer full - send thread can't keep up */
            pthread_mutex_lock(&g_stats.mutex);
            g_stats.buffer_overruns++;
            pthread_mutex_unlock(&g_stats.mutex);

            /* Log only occasionally to avoid flooding */
            static uint64_t last_overflow_log = 0;
            if (timestamp_ns - last_overflow_log > 1000000000ULL) {  /* 1 second */
                fprintf(stderr, "[Refill] WARNING: Ring buffer overflow - send thread too slow\n");
                last_overflow_log = timestamp_ns;
            }
        }

        pthread_mutex_lock(&g_stats.mutex);
        g_stats.buffer_refills++;
        pthread_mutex_unlock(&g_stats.mutex);

        /* Timing statistics */
        uint64_t refill_time = get_timestamp_ns() - refill_start;
        pthread_mutex_lock(&g_stats.mutex);
        g_stats.total_loop_time_us += refill_time / 1000;
        g_stats.loop_iterations++;
        pthread_mutex_unlock(&g_stats.mutex);
    }

    printf("[Refill] Stopped\n");
    iio_buffer_destroy(rxbuf);
    return NULL;
}

/**
 * Send thread (Consumer) - Sends UDP packets as fast as network allows
 *
 * This thread reads from the ring buffer and sends packets immediately.
 * No artificial pacing - the network is the only rate limiter.
 * Small usleep(100) when buffer empty to avoid busy-waiting.
 */
static void *send_thread(void *arg) {
    (void)arg;  /* Unused - we use global ring buffer */

    /* Create UDP socket for data */
    int data_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (data_sock < 0) {
        fprintf(stderr, "[Send] ERROR: Failed to create socket\n");
        return NULL;
    }

    /* Increase socket send buffer for burst handling */
    int sndbuf = 4 * 1024 * 1024;  /* 4 MB */
    setsockopt(data_sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

    printf("[Send] Started - consumer thread running\n");

    static uint8_t packet_buf[MAX_PACKET_BUFFER];
    size_t packet_len;
    uint8_t packet_count = 0;
    int packets_since_context = 0;
    uint64_t packets_sent = 0;
    uint64_t last_timestamp_ns = 0;

    while (g_running) {
        /* Try to read from ring buffer */
        ring_slot_t *slot = NULL;
        int ret = ring_buffer_read(&slot);

        if (ret < 0) {
            /* Buffer empty - small sleep to avoid busy-wait */
            usleep(100);  /* 100 microseconds */
            continue;
        }

        /* We have data - send it as fast as possible */
        int16_t *samples = slot->data;
        size_t num_samples = slot->num_samples;
        uint64_t buffer_timestamp = slot->timestamp_ns;

        /* Timestamp discontinuity detection */
        if (last_timestamp_ns != 0) {
            pthread_mutex_lock(&g_sdr_config.mutex);
            uint32_t sample_rate = g_sdr_config.sample_rate_hz;
            pthread_mutex_unlock(&g_sdr_config.mutex);

            uint64_t expected_delta_ns = (num_samples * 1000000000ULL) / sample_rate;
            uint64_t actual_delta_ns = buffer_timestamp - last_timestamp_ns;
            int64_t delta_error = (int64_t)(actual_delta_ns - expected_delta_ns);

            if (llabs(delta_error) > 10000000) {  /* More than 10ms discrepancy */
                pthread_mutex_lock(&g_stats.mutex);
                g_stats.timestamp_jumps++;
                if (delta_error > 0) {
                    g_stats.underflows++;
                } else {
                    g_stats.overflows++;
                }
                pthread_mutex_unlock(&g_stats.mutex);

                static uint64_t last_jump_log = 0;
                if (buffer_timestamp - last_jump_log > 1000000000ULL) {
                    fprintf(stderr, "[Send] WARNING: Timestamp jump: %lld ms\n",
                            (long long)(delta_error / 1000000));
                    last_jump_log = buffer_timestamp;
                }
            }
        }
        last_timestamp_ns = buffer_timestamp;

        /* Send all packets from this buffer */
        for (size_t offset = 0; offset < num_samples; offset += g_samples_per_packet) {
            if (!g_running) break;

            size_t chunk_size = (offset + g_samples_per_packet > num_samples) ?
                               (num_samples - offset) : g_samples_per_packet;

            /* Send periodic context packets */
            if (packets_since_context >= CONTEXT_INTERVAL) {
                encode_context_packet(packet_buf, &packet_len);
                broadcast_to_subscribers(data_sock, packet_buf, packet_len);
                pthread_mutex_lock(&g_stats.mutex);
                g_stats.contexts_sent++;
                pthread_mutex_unlock(&g_stats.mutex);
                packets_since_context = 0;
            }

            encode_data_packet(packet_buf, &packet_len, samples + offset * 2,
                             chunk_size, &packet_count);

            broadcast_to_subscribers(data_sock, packet_buf, packet_len);

            pthread_mutex_lock(&g_stats.mutex);
            g_stats.packets_sent++;
            g_stats.bytes_sent += packet_len;
            pthread_mutex_unlock(&g_stats.mutex);

            packets_since_context++;
            packets_sent++;
        }

        /* Mark slot as consumed - MUST be done after processing */
        ring_buffer_read_done();

        /* Periodic cleanup of dead subscribers */
        if (packets_sent % SUBSCRIBER_CLEANUP_INTERVAL == 0) {
            cleanup_dead_subscribers();
        }
    }

    printf("[Send] Stopped\n");
    close(data_sock);
    return NULL;
}

/* Configure SDR */
static int configure_sdr(struct iio_context *ctx, struct iio_device *dev) {
    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    if (!phy) {
        fprintf(stderr, "ERROR: ad9361-phy not found\n");
        return -1;
    }

    pthread_mutex_lock(&g_sdr_config.mutex);

    /* Set RX LO frequency */
    struct iio_channel *ch = iio_device_find_channel(phy, "altvoltage0", true);
    if (ch) {
        char buf[32];
        snprintf(buf, sizeof(buf), "%llu", (unsigned long long)g_sdr_config.center_freq_hz);
        iio_channel_attr_write(ch, "frequency", buf);
    }

    /* Set sample rate */
    ch = iio_device_find_channel(phy, "voltage0", false);
    if (ch) {
        char buf[32];
        snprintf(buf, sizeof(buf), "%u", g_sdr_config.sample_rate_hz);
        iio_channel_attr_write(ch, "sampling_frequency", buf);

        snprintf(buf, sizeof(buf), "%u", g_sdr_config.bandwidth_hz);
        iio_channel_attr_write(ch, "rf_bandwidth", buf);

        snprintf(buf, sizeof(buf), "%.1f", g_sdr_config.gain_db);
        iio_channel_attr_write(ch, "hardwaregain", buf);

        iio_channel_attr_write(ch, "gain_control_mode", "manual");
    }

    /* Enable channels */
    struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
    struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);

    if (rx0_i) iio_channel_enable(rx0_i);
    if (rx0_q) iio_channel_enable(rx0_q);

    printf("[Config] Configured: %.1f MHz, %.1f MSPS, %.1f dB\n",
           g_sdr_config.center_freq_hz / 1e6,
           g_sdr_config.sample_rate_hz / 1e6,
           g_sdr_config.gain_db);

    pthread_mutex_unlock(&g_sdr_config.mutex);

    return 0;
}

/* Main */
int main(int argc, char **argv) {
    /* Parse command-line arguments */
    size_t mtu = MTU_STANDARD;  /* Default to standard Ethernet */
    bool use_jumbo = false;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--jumbo") == 0) {
            use_jumbo = true;
            mtu = MTU_JUMBO;
        } else if (strcmp(argv[i], "--mtu") == 0 && i + 1 < argc) {
            mtu = (size_t)atoi(argv[++i]);
        } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  --jumbo               Use jumbo frames (MTU 9000)\n");
            printf("  --mtu <size>          Set custom MTU size in bytes\n");
            printf("  --help, -h            Show this help message\n");
            printf("\nExamples:\n");
            printf("  %s                    # Standard MTU (1500 bytes)\n", argv[0]);
            printf("  %s --jumbo            # Jumbo frames (9000 bytes)\n", argv[0]);
            printf("  %s --mtu 1492         # PPPoE MTU\n", argv[0]);
            printf("\nArchitecture:\n");
            printf("  This streamer uses a producer-consumer FIFO model:\n");
            printf("  - Refill thread: Grabs samples from IIO DMA as fast as possible\n");
            printf("  - Ring buffer:   %d slots x %d samples (lock-free SPSC)\n",
                   RING_BUFFER_SLOTS, RING_BUFFER_SLOT_SAMPLES);
            printf("  - Send thread:   Sends UDP packets as fast as network allows\n");
            return 0;
        }
    }

    /* Calculate optimal packet size based on MTU */
    g_samples_per_packet = calculate_optimal_samples_per_packet(mtu);

    /* Calculate actual packet sizes for verification */
    size_t packet_payload = g_samples_per_packet * 2 * sizeof(int16_t);
    size_t total_vita49_packet = packet_payload + VITA49_OVERHEAD;
    size_t total_udp_datagram = total_vita49_packet + IP_UDP_OVERHEAD;

    printf("========================================\n");
    printf("VITA49 Standalone Streamer for Pluto\n");
    printf("  FIFO Buffer Edition (Producer-Consumer)\n");
    printf("========================================\n");
    printf("MTU: %zu bytes%s\n", mtu, use_jumbo ? " (Jumbo frames)" : "");
    printf("Samples per packet: %zu\n", g_samples_per_packet);
    printf("VITA49 packet size: %zu bytes\n", total_vita49_packet);
    printf("UDP datagram size: %zu bytes\n", total_udp_datagram);
    printf("Ring buffer: %d slots x %d samples\n",
           RING_BUFFER_SLOTS, RING_BUFFER_SLOT_SAMPLES);

    if (total_udp_datagram > mtu) {
        fprintf(stderr, "WARNING: Packet size exceeds MTU! Will fragment.\n");
    } else {
        double efficiency = 100.0 * total_udp_datagram / mtu;
        printf("Packet fits in MTU (efficiency: %.1f%%)\n", efficiency);
    }
    printf("\n");

    /* Register signal handler */
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Initialize statistics mutex */
    pthread_mutex_init(&g_stats.mutex, NULL);

    /* Initialize ring buffer */
    if (ring_buffer_init(RING_BUFFER_SLOT_SAMPLES) < 0) {
        fprintf(stderr, "ERROR: Failed to initialize ring buffer\n");
        return 1;
    }

    /* Create IIO context */
    struct iio_context *ctx = iio_create_local_context();
    if (!ctx) {
        ctx = iio_create_network_context("192.168.2.1");
    }

    if (!ctx) {
        fprintf(stderr, "ERROR: Failed to create IIO context\n");
        ring_buffer_destroy();
        return 1;
    }

    printf("IIO context created\n");

    /* Create control socket */
    int control_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (control_sock < 0) {
        fprintf(stderr, "ERROR: Failed to create control socket\n");
        iio_context_destroy(ctx);
        ring_buffer_destroy();
        return 1;
    }

    struct sockaddr_in control_addr = {0};
    control_addr.sin_family = AF_INET;
    control_addr.sin_addr.s_addr = INADDR_ANY;
    control_addr.sin_port = htons(CONTROL_PORT);

    if (bind(control_sock, (struct sockaddr *)&control_addr, sizeof(control_addr)) < 0) {
        fprintf(stderr, "ERROR: Failed to bind control socket\n");
        close(control_sock);
        iio_context_destroy(ctx);
        ring_buffer_destroy();
        return 1;
    }

    printf("Control port: %d\n", CONTROL_PORT);
    printf("Data port: %d\n\n", DATA_PORT);

    /* Start 3 threads: control, refill (producer), send (consumer) */
    pthread_t control_tid, refill_tid, send_tid;

    pthread_create(&control_tid, NULL, control_thread, &control_sock);
    pthread_create(&refill_tid, NULL, refill_thread, ctx);
    pthread_create(&send_tid, NULL, send_thread, NULL);

    /* Monitor loop */
    while (g_running) {
        sleep(5);

        /* Get statistics */
        pthread_mutex_lock(&g_stats.mutex);
        uint64_t packets = g_stats.packets_sent;
        uint64_t bytes = g_stats.bytes_sent;
        uint32_t contexts = g_stats.contexts_sent;
        uint64_t underflows = g_stats.underflows;
        uint64_t overflows = g_stats.overflows;
        uint64_t refill_fails = g_stats.refill_failures;
        uint64_t ts_jumps = g_stats.timestamp_jumps;
        uint64_t buffer_refills = g_stats.buffer_refills;
        uint64_t buffer_overruns = g_stats.buffer_overruns;
        double avg_loop = g_stats.loop_iterations > 0 ?
            (double)g_stats.total_loop_time_us / g_stats.loop_iterations : 0;
        pthread_mutex_unlock(&g_stats.mutex);

        /* Ring buffer statistics */
        uint64_t ring_overflows, ring_underflows, ring_total_samples;
        size_t ring_fill;
        ring_buffer_get_stats(&ring_overflows, &ring_underflows,
                              &ring_total_samples, &ring_fill);

        printf("[Stats] Packets: %llu, Bytes: %llu MB, Contexts: %u, Subs: %d\n",
               (unsigned long long)packets,
               (unsigned long long)(bytes / 1048576),
               contexts, g_subscriber_count);

        printf("[Health] Underflows: %llu, Overflows: %llu, Refill Fails: %llu, TS Jumps: %llu\n",
               (unsigned long long)underflows,
               (unsigned long long)overflows,
               (unsigned long long)refill_fails,
               (unsigned long long)ts_jumps);

        printf("[FIFO] Fill: %zu/%d, Refills: %llu, Overruns: %llu, Ring OVF/UNF: %llu/%llu\n",
               ring_fill, RING_BUFFER_SLOTS,
               (unsigned long long)buffer_refills,
               (unsigned long long)buffer_overruns,
               (unsigned long long)ring_overflows,
               (unsigned long long)ring_underflows);

        printf("[Timing] Avg refill: %.1f us\n", avg_loop);

        /* Detailed subscriber statistics */
        printf("\n[Subscribers] Active: %d/%d\n", g_subscriber_count, MAX_SUBSCRIBERS);
        pthread_mutex_lock(&g_subscribers_mutex);
        for (int i = 0; i < g_subscriber_count; i++) {
            if (g_subscribers[i].active) {
                char ip_str[INET_ADDRSTRLEN];
                inet_ntop(AF_INET, &g_subscribers[i].addr.sin_addr,
                         ip_str, INET_ADDRSTRLEN);

                uint64_t current_time = get_timestamp_us();
                uint64_t uptime = (current_time - g_subscribers[i].first_seen_us) / 1000000;

                printf("  [%d] %s:%d - Pkts: %llu, Fails: %d/%llu, Uptime: %llus\n",
                       i, ip_str, ntohs(g_subscribers[i].addr.sin_port),
                       (unsigned long long)g_subscribers[i].packets_sent,
                       g_subscribers[i].consecutive_failures,
                       (unsigned long long)g_subscribers[i].total_failures,
                       (unsigned long long)uptime);
            }
        }
        pthread_mutex_unlock(&g_subscribers_mutex);
        printf("\n");
    }

    /* Cleanup */
    pthread_join(control_tid, NULL);
    pthread_join(refill_tid, NULL);
    pthread_join(send_tid, NULL);

    close(control_sock);
    iio_context_destroy(ctx);
    ring_buffer_destroy();
    pthread_mutex_destroy(&g_stats.mutex);

    printf("\n✓ Stopped\n");
    return 0;
}
