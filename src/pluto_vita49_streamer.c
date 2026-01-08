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
 * - sendmmsg() batching for 64x syscall reduction (~1300 syscalls/sec vs 83000)
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

#define _GNU_SOURCE  /* Required for CPU_ZERO, CPU_SET, pthread_setaffinity_np, sendmmsg */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <unistd.h>
#include <pthread.h>
#include <sched.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <signal.h>
#include <iio.h>
#include "lock_free_ring_buffer.h"

/* Configuration */
#define DEFAULT_FREQ_HZ         2400000000ULL   /* 2.4 GHz */
#define DEFAULT_RATE_HZ         30000000        /* 30 MSPS */
#define DEFAULT_GAIN_DB         20.0
#define CONTROL_PORT            4990            /* Config reception port */
#define DATA_PORT               4991            /* Data streaming port */
#define CONTEXT_INTERVAL        100             /* Send context every N packets */
#define MAX_SUBSCRIBERS         16              /* Max simultaneous receivers */

/* Subscriber Management Configuration */
#define SUBSCRIBER_TIMEOUT_US       30000000    /* 30 seconds */
#define MAX_CONSECUTIVE_FAILURES    10          /* Remove after 10 failures */
#define SUBSCRIBER_CLEANUP_INTERVAL 100         /* Check every 100 packets */

/* Buffer sizing - DMA naturally paces at ~2-3ms per refill */
#define MIN_BUFFER_SAMPLES          4096        /* Minimum for efficiency */
#define MAX_BUFFER_SAMPLES          65536       /* Maximum for memory/latency */
#define BUFFER_TIME_MS              3           /* Target ~3ms worth of samples */

/* Helper macros */
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define CLAMP(x, lo, hi) ((x) < (lo) ? (lo) : ((x) > (hi) ? (hi) : (x)))

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

/* sendmmsg() batching - reduces syscalls from 83,000/sec to ~1,300/sec */
#define SEND_BATCH_SIZE         64              /* Packets per sendmmsg() call */

/* VITA49 Packet Types */
#define VRT_PKT_TYPE_DATA       0x1             /* IF Data with Stream ID */
#define VRT_PKT_TYPE_CONTEXT    0x4             /* Context packet */
#define VRT_TSI_UTC             0x1             /* UTC timestamp */
#define VRT_TSF_PICOSECONDS     0x2             /* Picosecond fractional time */

/* Global state */
static volatile bool g_running = true;
static pthread_mutex_t g_subscribers_mutex = PTHREAD_MUTEX_INITIALIZER;
static size_t g_samples_per_packet = 360;  /* Will be calculated at runtime based on MTU */

/* Multicore optimization: Global ring buffer for IQ data transfer */
static lock_free_ring_buffer_t g_ring_buffer;
static atomic_uint g_sequence_counter = ATOMIC_VAR_INIT(0);

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

/* Statistics - simple counters, updated by threads */
typedef struct {
    uint64_t packets_sent;
    uint64_t bytes_sent;
    uint32_t contexts_sent;
    uint32_t reconfigs;
    uint64_t refill_failures;
    uint64_t send_failures;
    
    /* Multicore optimization stats */
    uint64_t dma_buffers_processed;
    uint64_t ring_buffer_drops;
    uint64_t network_thread_processed;
} stream_statistics_t;

static stream_statistics_t g_stats = {0};

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

/* Packet batch for sendmmsg() - reduces syscall overhead by 64x */
typedef struct {
    uint8_t data[SEND_BATCH_SIZE][MAX_PACKET_BUFFER];   /* Packet buffers */
    struct iovec iov[SEND_BATCH_SIZE];                  /* IO vectors */
    struct mmsghdr msgs[SEND_BATCH_SIZE];               /* Message headers */
    size_t count;                                        /* Packets in batch */
} packet_batch_t;

/* Function prototypes */
static void signal_handler(int sig);
static void add_subscriber(struct sockaddr_in *addr);
static int send_to_subscriber(int sock, uint8_t *buf, size_t len, subscriber_t *sub);
static void cleanup_dead_subscribers(void);
static void broadcast_to_subscribers(int sock, uint8_t *buf, size_t len);
static uint64_t get_timestamp_us(void);
static size_t calculate_optimal_samples_per_packet(size_t mtu);

static void encode_context_packet(uint8_t *buf, size_t *len);
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data, size_t num_samples, uint8_t *packet_count, uint64_t timestamp_us);
/* Multicore optimization thread functions */
static void *dma_reader_thread(void *arg);     /* Core 0: DMA reader (producer) */
static void *network_thread(void *arg);        /* Core 1: Network TX + Config (consumer) */
static void *control_thread(void *arg);        /* Core 1: receive config packets (legacy) */
static int configure_sdr(struct iio_context *ctx, struct iio_device *dev);

/* Batch sending functions - sendmmsg() for 64x syscall reduction */
static void batch_init(packet_batch_t *batch);
static uint8_t *batch_get_buffer(packet_batch_t *batch);
static void batch_commit_packet(packet_batch_t *batch, size_t len);
static int batch_flush_to_subscriber(int sock, packet_batch_t *batch, subscriber_t *sub);
static int batch_flush_to_all_subscribers(int sock, packet_batch_t *batch);

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

/* Send packet to individual subscriber with error handling - HOT PATH, no syscalls */
static int send_to_subscriber(int sock, uint8_t *buf, size_t len, subscriber_t *sub) {
    ssize_t sent = sendto(sock, buf, len, 0,
                         (struct sockaddr *)&sub->addr,
                         sizeof(sub->addr));

    if (sent < 0) {
        sub->consecutive_failures++;
        sub->total_failures++;

        /* Mark inactive after threshold */
        if (sub->consecutive_failures >= MAX_CONSECUTIVE_FAILURES) {
            sub->active = false;
        }

        return -1;
    }

    /* Success - reset failure counter, update packet stats only */
    sub->consecutive_failures = 0;
    /* NOTE: last_seen_us updated periodically in cleanup, NOT per-packet */
    sub->packets_sent++;
    sub->bytes_sent += len;

    return 0;
}

/* Remove dead subscribers from list and update timestamps for active ones */
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
        } else {
            /* Active subscriber - update timestamp and check for timeout */
            if (sub->packets_sent > 0) {
                /* Has been sending successfully - update timestamp */
                sub->last_seen_us = current_time;
            } else if (sub->last_seen_us > 0 &&
                      (current_time - sub->last_seen_us) > SUBSCRIBER_TIMEOUT_US) {
                /* No packets sent and timed out */
                should_remove = true;
            }
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

/* Broadcast packet to all active subscribers - NO LOCK in hot path
 * Safe because: control thread only appends, data thread only reads.
 * Worst case: miss a new subscriber for one buffer cycle (harmless).
 */
static void broadcast_to_subscribers(int sock, uint8_t *buf, size_t len) {
    /* Volatile read of subscriber count - no lock needed */
    int count = g_subscriber_count;

    for (int i = 0; i < count; i++) {
        if (g_subscribers[i].active) {
            send_to_subscriber(sock, buf, len, &g_subscribers[i]);
        }
    }
}

/* ========================================================================
 * sendmmsg() Batch Functions - Reduce syscalls by 64x
 *
 * Instead of: 83,000 sendto() calls/sec (one per packet)
 * We now do:  ~1,300 sendmmsg() calls/sec (64 packets per call)
 *
 * This eliminates the syscall overhead that was limiting throughput.
 * ======================================================================== */

/* Initialize batch for new round of packets */
static void batch_init(packet_batch_t *batch) {
    batch->count = 0;
    memset(batch->msgs, 0, sizeof(batch->msgs));
}

/* Get pointer to next available packet buffer in batch */
static uint8_t *batch_get_buffer(packet_batch_t *batch) {
    if (batch->count >= SEND_BATCH_SIZE) {
        return NULL;  /* Batch full - caller should flush first */
    }
    return batch->data[batch->count];
}

/* Commit a packet to the batch after encoding */
static void batch_commit_packet(packet_batch_t *batch, size_t len) {
    if (batch->count >= SEND_BATCH_SIZE) {
        return;  /* Should not happen - caller should check */
    }

    size_t idx = batch->count;

    /* Set up iovec pointing to this packet's data */
    batch->iov[idx].iov_base = batch->data[idx];
    batch->iov[idx].iov_len = len;

    /* Set up mmsghdr - destination will be set during flush */
    batch->msgs[idx].msg_hdr.msg_iov = &batch->iov[idx];
    batch->msgs[idx].msg_hdr.msg_iovlen = 1;

    batch->count++;
}

/* Flush batch to a single subscriber using sendmmsg() */
static int batch_flush_to_subscriber(int sock, packet_batch_t *batch, subscriber_t *sub) {
    if (batch->count == 0) {
        return 0;
    }

    /* Set destination for all messages in batch */
    for (size_t i = 0; i < batch->count; i++) {
        batch->msgs[i].msg_hdr.msg_name = &sub->addr;
        batch->msgs[i].msg_hdr.msg_namelen = sizeof(sub->addr);
    }

    /* Single syscall sends all packets */
    int sent = sendmmsg(sock, batch->msgs, batch->count, 0);

    if (sent < 0) {
        sub->consecutive_failures++;
        sub->total_failures++;
        if (sub->consecutive_failures >= MAX_CONSECUTIVE_FAILURES) {
            sub->active = false;
        }
        return -1;
    }

    /* Success - update subscriber stats */
    sub->consecutive_failures = 0;
    sub->packets_sent += sent;

    /* Calculate bytes sent from individual message lengths */
    for (int i = 0; i < sent; i++) {
        sub->bytes_sent += batch->msgs[i].msg_len;
    }

    return sent;
}

/* Flush batch to all active subscribers */
static int batch_flush_to_all_subscribers(int sock, packet_batch_t *batch) {
    if (batch->count == 0) {
        return 0;
    }

    int count = g_subscriber_count;  /* Volatile read */
    int total_sent = 0;

    for (int i = 0; i < count; i++) {
        if (g_subscribers[i].active) {
            int sent = batch_flush_to_subscriber(sock, batch, &g_subscribers[i]);
            if (sent > 0) {
                total_sent += sent;
            }
        }
    }

    return total_sent;
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

    /* Health status indicators (not tracked in simplified stats) */
    uint64_t underflows = 0;
    uint64_t overflows = 0;

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

/* Encode VITA49 Data packet - OPTIMIZED: no byte swapping
 * Samples stay in native little-endian format.
 * Receiver must decode as little-endian: np.frombuffer(payload, dtype='<i2')
 */
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data,
                               size_t num_samples, uint8_t *packet_count,
                               uint64_t timestamp_us) {
    if (num_samples == 0) {
        *len = 0;
        return;
    }

    vrt_data_header_t *hdr = (vrt_data_header_t *)buf;
    uint8_t *payload = buf + sizeof(vrt_data_header_t);

    /* Direct memcpy - samples stay in native (little) endian
     * This eliminates ~60 million htons() calls/sec at 30 MSPS
     */
    size_t payload_bytes = num_samples * 4;  /* 2 bytes I + 2 bytes Q per sample */
    memcpy(payload, iq_data, payload_bytes);

    /* Pad to 32-bit boundary if needed */
    size_t padding = (4 - (payload_bytes % 4)) % 4;
    if (padding) {
        memset(payload + payload_bytes, 0, padding);
        payload_bytes += padding;
    }

    /* Trailer */
    uint32_t *trailer = (uint32_t *)(payload + payload_bytes);
    *trailer = htonl_custom(0x40000000);  /* valid_data = 1 */

    /* Calculate packet size */
    size_t total_words = 1 + 1 + 1 + 2 + (payload_bytes / 4) + 1;

    /* Use pre-computed timestamp (passed from caller) */
    uint32_t ts_int = timestamp_us / 1000000;
    uint64_t ts_frac = (timestamp_us % 1000000) * 1000000ULL;

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

/**
 * Control thread (Core 1) - Receives configuration packets
 *
 * This thread blocks on recvfrom() until a config packet arrives.
 * Zero CPU usage while waiting.
 */
static void *control_thread(void *arg) {
    int *sock_fd = (int *)arg;
    uint8_t buf[2048];
    struct sockaddr_in client_addr;
    socklen_t client_len = sizeof(client_addr);

    /* Pin to Core 1 */
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(1, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);

    /* Set socket timeout so we can check g_running periodically */
    struct timeval timeout;
    timeout.tv_sec = 1;
    timeout.tv_usec = 0;
    setsockopt(*sock_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    printf("[Control] Listening on port %d - pinned to Core 1\n", CONTROL_PORT);
    printf("[Control] Default: %.3f MHz, %.1f MSPS, %.1f dB\n",
           g_sdr_config.center_freq_hz / 1e6,
           g_sdr_config.sample_rate_hz / 1e6,
           g_sdr_config.gain_db);

    while (g_running) {
        /* BLOCK here - zero CPU until packet arrives or timeout */
        ssize_t recv_len = recvfrom(*sock_fd, buf, sizeof(buf), 0,
                                   (struct sockaddr *)&client_addr, &client_len);

        if (recv_len < 0) continue;

        char ip_str[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &client_addr.sin_addr, ip_str, INET_ADDRSTRLEN);
        printf("\n[Control] Config from %s (%zd bytes)\n", ip_str, recv_len);

        /* Parse context packet */
        uint64_t new_freq = g_sdr_config.center_freq_hz;
        uint32_t new_rate = g_sdr_config.sample_rate_hz;
        double new_gain = g_sdr_config.gain_db;

        if (parse_context_packet(buf, recv_len, &new_freq, &new_rate, &new_gain) == 0) {
            bool changed = false;

            pthread_mutex_lock(&g_sdr_config.mutex);

            if (new_freq != g_sdr_config.center_freq_hz) {
                printf("[Control] Freq: %.3f -> %.3f MHz\n",
                       g_sdr_config.center_freq_hz / 1e6, new_freq / 1e6);
                g_sdr_config.center_freq_hz = new_freq;
                changed = true;
            }

            if (new_rate != g_sdr_config.sample_rate_hz) {
                printf("[Control] Rate: %.1f -> %.1f MSPS\n",
                       g_sdr_config.sample_rate_hz / 1e6, new_rate / 1e6);
                g_sdr_config.sample_rate_hz = new_rate;
                g_sdr_config.bandwidth_hz = new_rate * 0.8;
                changed = true;
            }

            if (new_gain != g_sdr_config.gain_db) {
                printf("[Control] Gain: %.1f -> %.1f dB\n",
                       g_sdr_config.gain_db, new_gain);
                g_sdr_config.gain_db = new_gain;
                changed = true;
            }

            if (changed) {
                g_sdr_config.config_changed = true;
                printf("[Control] Config queued for data thread\n");
            }

            pthread_mutex_unlock(&g_sdr_config.mutex);
        }

        /* Add as subscriber */
        client_addr.sin_port = htons(DATA_PORT);
        add_subscriber(&client_addr);
    }

    printf("[Control] Stopped\n");
    return NULL;
}

/**
 * Data thread (Core 0) - Refills DMA buffer and sends packets
 *
 * Key insight: iio_buffer_refill() naturally blocks for ~2ms at 30 MSPS,
 * and the send loop takes ~1.8ms. This IS the pacing - no artificial
 * delays needed, no ring buffer needed.
 *
 * Architecture:
 *   while (running):
 *     refill()   <- blocks ~2ms until DMA fills buffer
 *     send_all() <- sends all packets immediately (~1.8ms)
 *     loop       <- completes before next buffer ready
 */
static void *data_thread(void *arg) {
    struct iio_context *ctx = (struct iio_context *)arg;
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");

    if (!dev) {
        fprintf(stderr, "[Data] ERROR: Device not found\n");
        return NULL;
    }

    /* Pin to Core 0 for cache locality */
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(0, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);

    /* Configure SDR */
    if (configure_sdr(ctx, dev) < 0) {
        return NULL;
    }

    /* Calculate buffer size: ~3ms worth of samples, clamped to valid range */
    pthread_mutex_lock(&g_sdr_config.mutex);
    uint32_t rate = g_sdr_config.sample_rate_hz;
    pthread_mutex_unlock(&g_sdr_config.mutex);

    size_t buffer_samples = CLAMP((rate * BUFFER_TIME_MS) / 1000,
                                  MIN_BUFFER_SAMPLES, MAX_BUFFER_SAMPLES);

    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
    if (!rxbuf) {
        fprintf(stderr, "[Data] ERROR: Failed to create buffer\n");
        return NULL;
    }

    struct iio_channel *rx_chan = iio_device_find_channel(dev, "voltage0", false);

    printf("[Data] Created IIO buffer: %zu samples (%.2f ms at %.1f MSPS)\n",
           buffer_samples,
           (double)buffer_samples * 1000.0 / rate,
           rate / 1e6);

    /* Create UDP socket with large send buffer */
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        fprintf(stderr, "[Data] ERROR: Failed to create socket\n");
        iio_buffer_destroy(rxbuf);
        return NULL;
    }

    int sndbuf = 2 * 1024 * 1024;  /* 2 MB send buffer */
    setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

    printf("[Data] Started - pinned to Core 0\n");
    printf("[Data] Using sendmmsg() batching: %d packets/syscall\n", SEND_BATCH_SIZE);

    /* Allocate batch on heap - it's ~1MB due to packet buffers */
    packet_batch_t *batch = calloc(1, sizeof(packet_batch_t));
    if (!batch) {
        fprintf(stderr, "[Data] ERROR: Failed to allocate batch buffer\n");
        close(sock);
        iio_buffer_destroy(rxbuf);
        return NULL;
    }

    uint8_t context_buf[2048];  /* Context packets sent immediately (rare) */
    size_t packet_len;
    uint8_t packet_count = 0;
    int packets_since_context = 0;
    uint64_t packets_sent = 0;

    while (g_running) {
        /* Check for reconfig (non-blocking check) */
        pthread_mutex_lock(&g_sdr_config.mutex);
        bool reconfig = g_sdr_config.config_changed;
        pthread_mutex_unlock(&g_sdr_config.mutex);

        if (reconfig) {
            /* Flush any pending packets before reconfiguring */
            if (batch->count > 0) {
                batch_flush_to_all_subscribers(sock, batch);
                batch_init(batch);
            }

            printf("[Data] ========================================\n");
            printf("[Data] Configuration change detected\n");

            iio_buffer_destroy(rxbuf);

            if (configure_sdr(ctx, dev) < 0) {
                fprintf(stderr, "[Data] ERROR: Failed to apply configuration\n");
                /* Try to recover with old buffer size */
                rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
                if (!rxbuf) {
                    fprintf(stderr, "[Data] FATAL: Cannot recreate buffer\n");
                    break;
                }
            } else {
                pthread_mutex_lock(&g_sdr_config.mutex);
                rate = g_sdr_config.sample_rate_hz;
                g_sdr_config.config_changed = false;
                pthread_mutex_unlock(&g_sdr_config.mutex);

                buffer_samples = CLAMP((rate * BUFFER_TIME_MS) / 1000,
                                       MIN_BUFFER_SAMPLES, MAX_BUFFER_SAMPLES);
                rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
                if (!rxbuf) {
                    fprintf(stderr, "[Data] FATAL: Cannot create buffer\n");
                    break;
                }

                printf("[Data] Buffer: %zu samples (%.2f ms at %.1f MSPS)\n",
                       buffer_samples,
                       (double)buffer_samples * 1000.0 / rate,
                       rate / 1e6);

                /* Notify subscribers of config change */
                encode_context_packet(context_buf, &packet_len);
                broadcast_to_subscribers(sock, context_buf, packet_len);
                g_stats.contexts_sent++;
                g_stats.reconfigs++;
            }

            printf("[Data] ========================================\n");
            packets_since_context = 0;
        }

        /* BLOCK here until DMA fills buffer - THIS IS THE NATURAL PACING */
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        if (nbytes < 0) {
            g_stats.refill_failures++;
            usleep(1000);  /* Only sleep on error */
            continue;
        }

        /* Get timestamp ONCE per buffer, not per packet */
        uint64_t buffer_timestamp = get_timestamp_us();

        /* Send ALL packets immediately - no sleeping */
        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, rx_chan);
        if (!samples) continue;

        size_t num_samples = nbytes / 4;  /* 4 bytes per I/Q pair */

        /* Initialize batch for this DMA buffer */
        batch_init(batch);

        /* Batch statistics for this buffer */
        size_t packets_this_buffer = 0;
        size_t bytes_this_buffer = 0;

        for (size_t offset = 0; offset < num_samples; offset += g_samples_per_packet) {
            if (!g_running) break;

            size_t chunk = MIN(g_samples_per_packet, num_samples - offset);

            /* Periodic context packets - send immediately (rare, 1 per 100 data pkts) */
            if (packets_since_context >= CONTEXT_INTERVAL) {
                /* Flush data batch first */
                if (batch->count > 0) {
                    batch_flush_to_all_subscribers(sock, batch);
                    batch_init(batch);
                }
                encode_context_packet(context_buf, &packet_len);
                broadcast_to_subscribers(sock, context_buf, packet_len);
                g_stats.contexts_sent++;
                packets_since_context = 0;
            }

            /* Get buffer from batch */
            uint8_t *pkt_buf = batch_get_buffer(batch);
            if (!pkt_buf) {
                /* Batch full - flush and get new buffer */
                batch_flush_to_all_subscribers(sock, batch);
                batch_init(batch);
                pkt_buf = batch_get_buffer(batch);
            }

            /* Encode packet into batch buffer */
            encode_data_packet(pkt_buf, &packet_len,
                             samples + offset * 2, chunk, &packet_count,
                             buffer_timestamp);
            batch_commit_packet(batch, packet_len);

            packets_this_buffer++;
            bytes_this_buffer += packet_len;
            packets_since_context++;
        }

        /* Flush remaining packets in batch */
        if (batch->count > 0) {
            batch_flush_to_all_subscribers(sock, batch);
        }

        /* Update global stats once per buffer (not per packet) */
        g_stats.packets_sent += packets_this_buffer;
        g_stats.bytes_sent += bytes_this_buffer;
        packets_sent += packets_this_buffer;

        /* Periodic cleanup */
        if (packets_sent % SUBSCRIBER_CLEANUP_INTERVAL == 0) {
            cleanup_dead_subscribers();
        }
    }

    free(batch);

    printf("[Data] Stopped\n");
    close(sock);
    iio_buffer_destroy(rxbuf);
    return NULL;
}

/*
 * DMA Reader Thread (Core 0) - Multicore Optimization Producer
 *
 * Dedicated to reading IQ samples from DMA and pushing to ring buffer.
 * Runs on Core 0 with high priority for minimal latency.
 * 
 * Key optimizations:
 * - Fast memcpy of entire DMA buffer (no sample-by-sample loops)
 * - Lock-free ring buffer push (no mutex contention) 
 * - Pre-allocated buffer pool (no malloc/free)
 * - Natural DMA pacing (iio_buffer_refill blocks ~2ms)
 */
static void *dma_reader_thread(void *arg) {
    struct iio_context *ctx = (struct iio_context *)arg;
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");

    if (!dev) {
        fprintf(stderr, "[DMA Reader] ERROR: Device not found\n");
        return NULL;
    }

    /* Pin to Core 0 with high priority */
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(0, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);

    /* Set high priority for DMA thread */
    struct sched_param param;
    param.sched_priority = 90;  /* High real-time priority */
    if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &param) != 0) {
        printf("[DMA Reader] WARNING: Failed to set high priority\n");
    }

    /* Configure SDR */
    if (configure_sdr(ctx, dev) < 0) {
        return NULL;
    }

    /* Calculate buffer size: ~3ms worth of samples */
    pthread_mutex_lock(&g_sdr_config.mutex);
    uint32_t rate = g_sdr_config.sample_rate_hz;
    pthread_mutex_unlock(&g_sdr_config.mutex);

    size_t buffer_samples = CLAMP((rate * BUFFER_TIME_MS) / 1000,
                                  MIN_BUFFER_SAMPLES, MAX_BUFFER_SAMPLES);

    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
    if (!rxbuf) {
        fprintf(stderr, "[DMA Reader] ERROR: Failed to create buffer\n");
        return NULL;
    }

    struct iio_channel *rx_chan = iio_device_find_channel(dev, "voltage0", false);

    printf("[DMA Reader] Started - pinned to Core 0, priority 90\n");
    printf("[DMA Reader] Buffer: %zu samples (%.2f ms at %.1f MSPS)\n",
           buffer_samples,
           (double)buffer_samples * 1000.0 / rate,
           rate / 1e6);

    size_t buffer_idx = 0;
    
    while (g_running) {
        /* Check for reconfig (non-blocking check) */
        pthread_mutex_lock(&g_sdr_config.mutex);
        bool reconfig = g_sdr_config.config_changed;
        pthread_mutex_unlock(&g_sdr_config.mutex);

        if (reconfig) {
            printf("[DMA Reader] Configuration change detected, reconfiguring...\n");
            
            iio_buffer_destroy(rxbuf);

            if (configure_sdr(ctx, dev) < 0) {
                fprintf(stderr, "[DMA Reader] ERROR: Failed to apply configuration\n");
                break;
            }

            pthread_mutex_lock(&g_sdr_config.mutex);
            rate = g_sdr_config.sample_rate_hz;
            g_sdr_config.config_changed = false;
            pthread_mutex_unlock(&g_sdr_config.mutex);

            buffer_samples = CLAMP((rate * BUFFER_TIME_MS) / 1000,
                                   MIN_BUFFER_SAMPLES, MAX_BUFFER_SAMPLES);
            rxbuf = iio_device_create_buffer(dev, buffer_samples, false);
            if (!rxbuf) {
                fprintf(stderr, "[DMA Reader] FATAL: Cannot recreate buffer\n");
                break;
            }

            printf("[DMA Reader] Reconfigured: %zu samples at %.1f MSPS\n",
                   buffer_samples, rate / 1e6);
            g_stats.reconfigs++;
        }

        /* BLOCK here until DMA fills buffer - natural pacing */
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        if (nbytes < 0) {
            g_stats.refill_failures++;
            usleep(1000);  /* Only sleep on error */
            continue;
        }

        /* Get pointer to DMA data */
        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, rx_chan);
        if (!samples) continue;

        size_t num_samples = nbytes / 4;  /* 4 bytes per I/Q pair */

        /* Prepare ring buffer entry */
        iq_buffer_entry_t buffer_entry = {
            .data = g_ring_buffer.sample_pool[buffer_idx],
            .sample_count = num_samples,
            .timestamp_us = get_timestamp_us(),
            .sequence_num = atomic_fetch_add(&g_sequence_counter, 1),
            .buffer_id = buffer_idx
        };

        /* Fast memcpy - copy entire buffer at once (much faster than sample loops) */
        memcpy(buffer_entry.data, samples, num_samples * 4);

        /* Push to ring buffer (non-blocking) */
        if (!ring_buffer_push(&g_ring_buffer, &buffer_entry)) {
            g_stats.ring_buffer_drops++;
            /* Ring buffer full - network thread may be overloaded */
        } else {
            g_stats.dma_buffers_processed++;
        }

        /* Rotate to next buffer in pool */
        buffer_idx = (buffer_idx + 1) % RING_BUFFER_CAPACITY;
    }

    printf("[DMA Reader] Stopped\n");
    iio_buffer_destroy(rxbuf);
    return NULL;
}

/*
 * Network Thread (Core 1) - Multicore Optimization Consumer
 * 
 * TODO: Phase 3 implementation
 * This will consume from ring buffer and handle:
 * - VITA49 packet encoding
 * - sendmmsg() batch transmission  
 * - Configuration packet reception (merged from control_thread)
 * - Subscriber management
 */
static void *network_thread(void *arg) {
    /* TODO: Implement Phase 3 - Combined network TX + config handling on Core 1 */
    (void)arg;  /* Suppress unused parameter warning */
    
    printf("[Network Thread] TODO: Phase 3 implementation\n");
    printf("[Network Thread] Will consume ring buffer and handle network TX\n");
    
    /* For now, just sleep to avoid busy loop */
    while (g_running) {
        sleep(1);
    }
    
    return NULL;
}

/* Configure SDR with verification */
static int configure_sdr(struct iio_context *ctx, struct iio_device *dev) {
    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    if (!phy) {
        fprintf(stderr, "ERROR: ad9361-phy not found\n");
        return -1;
    }

    pthread_mutex_lock(&g_sdr_config.mutex);
    uint64_t target_freq = g_sdr_config.center_freq_hz;
    uint32_t target_rate = g_sdr_config.sample_rate_hz;
    uint32_t target_bw = g_sdr_config.bandwidth_hz;
    double target_gain = g_sdr_config.gain_db;
    pthread_mutex_unlock(&g_sdr_config.mutex);

    char buf[64];
    ssize_t ret;

    /* First, disable DMA channels before changing sample rate */
    struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
    struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);

    if (rx0_i) iio_channel_disable(rx0_i);
    if (rx0_q) iio_channel_disable(rx0_q);

    /* Set RX LO frequency */
    struct iio_channel *lo_ch = iio_device_find_channel(phy, "altvoltage0", true);
    if (lo_ch) {
        snprintf(buf, sizeof(buf), "%llu", (unsigned long long)target_freq);
        ret = iio_channel_attr_write(lo_ch, "frequency", buf);
        if (ret < 0) {
            fprintf(stderr, "[Config] WARNING: Failed to set frequency: %zd\n", ret);
        }
    }

    /* Set sample rate on ad9361-phy RX channel */
    struct iio_channel *phy_rx = iio_device_find_channel(phy, "voltage0", false);
    if (phy_rx) {
        /* Set sample rate */
        snprintf(buf, sizeof(buf), "%u", target_rate);
        ret = iio_channel_attr_write(phy_rx, "sampling_frequency", buf);
        if (ret < 0) {
            fprintf(stderr, "[Config] WARNING: Failed to set sample rate: %zd\n", ret);
        }

        /* Verify sample rate was applied */
        char verify_buf[64] = {0};
        ret = iio_channel_attr_read(phy_rx, "sampling_frequency", verify_buf, sizeof(verify_buf));
        if (ret > 0) {
            uint32_t actual_rate = (uint32_t)atoll(verify_buf);
            if (actual_rate != target_rate) {
                fprintf(stderr, "[Config] WARNING: Rate mismatch! Requested %u, got %u\n",
                       target_rate, actual_rate);
            } else {
                printf("[Config] Sample rate verified: %u Hz\n", actual_rate);
            }
        }

        /* Set bandwidth */
        snprintf(buf, sizeof(buf), "%u", target_bw);
        ret = iio_channel_attr_write(phy_rx, "rf_bandwidth", buf);
        if (ret < 0) {
            fprintf(stderr, "[Config] WARNING: Failed to set bandwidth: %zd\n", ret);
        }

        /* Set gain */
        snprintf(buf, sizeof(buf), "%.1f", target_gain);
        ret = iio_channel_attr_write(phy_rx, "hardwaregain", buf);
        if (ret < 0) {
            fprintf(stderr, "[Config] WARNING: Failed to set gain: %zd\n", ret);
        }

        iio_channel_attr_write(phy_rx, "gain_control_mode", "manual");
    }

    /* Small delay to let AD9361 PLLs settle after rate change */
    usleep(10000);  /* 10ms */

    /* Re-enable DMA channels for buffer creation */
    if (rx0_i) iio_channel_enable(rx0_i);
    if (rx0_q) iio_channel_enable(rx0_q);

    printf("[Config] Configured: %.1f MHz, %.1f MSPS, %.1f dB\n",
           target_freq / 1e6,
           target_rate / 1e6,
           target_gain);

    return 0;
}

/* Main */
int main(int argc, char **argv) {
    /* Parse command-line arguments */
    size_t mtu = MTU_STANDARD;
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
            printf("  --jumbo         Use jumbo frames (MTU 9000)\n");
            printf("  --mtu <size>    Set custom MTU size in bytes\n");
            printf("  --help, -h      Show this help message\n");
            printf("\nArchitecture:\n");
            printf("  2-thread model using DMA blocking as natural pacing:\n");
            printf("  - Data thread (Core 0): refill() blocks ~2ms, then sends all packets\n");
            printf("  - Control thread (Core 1): receives config, zero CPU when idle\n");
            printf("\nExpected throughput (Gigabit Ethernet):\n");
            printf("  5 MSPS  -> ~40 Mbps\n");
            printf("  10 MSPS -> ~80 Mbps\n");
            printf("  20 MSPS -> ~160 Mbps\n");
            printf("  30 MSPS -> ~240 Mbps\n");
            return 0;
        }
    }

    /* Calculate optimal packet size based on MTU */
    g_samples_per_packet = calculate_optimal_samples_per_packet(mtu);

    size_t packet_payload = g_samples_per_packet * 2 * sizeof(int16_t);
    size_t total_vita49_packet = packet_payload + VITA49_OVERHEAD;
    size_t total_udp_datagram = total_vita49_packet + IP_UDP_OVERHEAD;

    printf("========================================\n");
    printf("VITA49 Streamer for Pluto (2-Thread)\n");
    printf("========================================\n");
    printf("MTU: %zu bytes%s\n", mtu, use_jumbo ? " (Jumbo)" : "");
    printf("Samples/packet: %zu\n", g_samples_per_packet);
    printf("Packet size: %zu bytes (UDP: %zu)\n", total_vita49_packet, total_udp_datagram);

    if (total_udp_datagram > mtu) {
        fprintf(stderr, "WARNING: Packet exceeds MTU!\n");
    }
    printf("\n");

    /* Register signal handler */
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Create IIO context */
    struct iio_context *ctx = iio_create_local_context();
    if (!ctx) {
        ctx = iio_create_network_context("192.168.2.1");
    }
    if (!ctx) {
        fprintf(stderr, "ERROR: Failed to create IIO context\n");
        return 1;
    }
    printf("IIO context created\n");

    /* Create control socket */
    int control_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (control_sock < 0) {
        fprintf(stderr, "ERROR: Failed to create control socket\n");
        iio_context_destroy(ctx);
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
        return 1;
    }

    printf("Control: %d, Data: %d\n\n", CONTROL_PORT, DATA_PORT);

    /* Initialize ring buffer for multicore optimization */
    ring_buffer_init(&g_ring_buffer);
    printf("Ring buffer initialized: %d entries, %zu MB\n", 
           RING_BUFFER_CAPACITY, sizeof(g_ring_buffer) / (1024*1024));

    /* Start multicore optimized threads */
    pthread_t dma_tid, control_tid;

    /* TODO: For full Phase 3, replace control_thread with network_thread */
    pthread_create(&dma_tid, NULL, dma_reader_thread, ctx);
    pthread_create(&control_tid, NULL, control_thread, &control_sock);

    /* Monitor loop - simple stats every 5 seconds */
    uint64_t last_packets = 0;
    uint64_t last_bytes = 0;

    while (g_running) {
        sleep(5);

        uint64_t packets = g_stats.packets_sent;
        uint64_t bytes = g_stats.bytes_sent;

        uint64_t pkt_delta = packets - last_packets;
        uint64_t byte_delta = bytes - last_bytes;
        double mbps = (byte_delta * 8.0) / (5.0 * 1000000.0);

        printf("[Stats] Pkts: %llu (+%llu), Throughput: %.1f Mbps, Subs: %d\n",
               (unsigned long long)packets,
               (unsigned long long)pkt_delta,
               mbps, g_subscriber_count);

        /* Multicore optimization stats */
        ring_buffer_stats_t rb_stats = ring_buffer_get_stats(&g_ring_buffer);
        printf("[Multicore] DMA: %llu bufs, Ring: %.1f%% full, Drops: %llu\n",
               (unsigned long long)g_stats.dma_buffers_processed,
               rb_stats.current_utilization * 100.0,
               (unsigned long long)g_stats.ring_buffer_drops);

        if (g_stats.refill_failures > 0 || g_stats.send_failures > 0) {
            printf("[Errors] Refill: %llu, Send: %llu\n",
                   (unsigned long long)g_stats.refill_failures,
                   (unsigned long long)g_stats.send_failures);
        }

        last_packets = packets;
        last_bytes = bytes;
    }

    /* Cleanup */
    pthread_join(dma_tid, NULL);
    pthread_join(control_tid, NULL);

    close(control_sock);
    iio_context_destroy(ctx);

    printf("\nStopped\n");
    return 0;
}
