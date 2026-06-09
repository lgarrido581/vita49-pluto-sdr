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
#include <math.h>
#include <fcntl.h>
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

/* VITA49 Stream IDs - format: device_id(8) | data_type(8) | reserved(8) | channel(8) */
#define STREAM_ID_RX0           0x01000001      /* Device 1, Channel 1 (RX0) */
#define STREAM_ID_RX1           0x01000002      /* Device 1, Channel 2 (RX1) */

/* Global state */
static volatile bool g_running = true;
static pthread_mutex_t g_subscribers_mutex = PTHREAD_MUTEX_INITIALIZER;
static size_t g_samples_per_packet = 360;  /* Will be calculated at runtime based on MTU */

/* Multicore optimization: Global ring buffers for IQ data transfer
 * - RX0 buffer used for single-channel mode (backward compatible)
 * - RX1 buffer used only in dual-channel mode
 */
static lock_free_ring_buffer_t g_ring_buffer_rx0;
static lock_free_ring_buffer_t g_ring_buffer_rx1;
static atomic_uint g_sequence_counter_rx0 = ATOMIC_VAR_INIT(0);
static atomic_uint g_sequence_counter_rx1 = ATOMIC_VAR_INIT(0);

/* Burst mode configuration and buffers
 * - Streaming mode (≤11 MSPS): DMA → Ring Buffer → Network (continuous, low latency)
 * - Burst mode (>11 MSPS): DMA → Burst Buffer → Network (accumulate then rapid-fire)
 *
 * Conservative buffer size: 5M samples = 20 MB per channel (40 MB total for dual)
 * This fits comfortably in Pluto's memory while providing large contiguous IQ chunks
 */
#define BURST_MODE_THRESHOLD_HZ 11000000    /* 11 MSPS threshold */
#define BURST_BUFFER_SAMPLES    (5 * 1024 * 1024)  /* 5M samples per channel */

typedef struct {
    int16_t *data;              /* Burst accumulation buffer (I/Q pairs) */
    atomic_size_t fill_count;   /* Current number of I/Q samples accumulated */
    atomic_bool ready;          /* Buffer full and ready to transmit */
    uint64_t start_timestamp_ns;/* Timestamp of first sample in burst (nanoseconds) */
    uint32_t sequence_base;     /* Sequence number at burst start */
} burst_buffer_t;

static burst_buffer_t g_burst_rx0 = {0};
static burst_buffer_t g_burst_rx1 = {0};
static atomic_bool g_burst_mode_enabled = ATOMIC_VAR_INIT(false);

/* Thread argument structure for network thread (Phase 3) */
typedef struct {
    struct iio_context *iio_ctx;
    int control_sock;
} network_thread_args_t;

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

/* Channel Mode Configuration */
typedef enum {
    CHANNEL_MODE_SINGLE_RX0 = 0x00,  /* RX0 only (default, backward compatible) */
    CHANNEL_MODE_SINGLE_RX1 = 0x01,  /* RX1 only */
    CHANNEL_MODE_DUAL = 0x02         /* RX0 + RX1 simultaneously */
} channel_mode_t;

/* SDR Configuration */
typedef struct {
    uint64_t center_freq_hz;
    uint32_t sample_rate_hz;
    uint32_t bandwidth_hz;
    double gain_db;
    channel_mode_t channel_mode;  /* Channel selection mode */
    bool config_changed;  /* Flag to signal streaming thread to reconfigure */
    pthread_mutex_t mutex;
} sdr_config_t;

static sdr_config_t g_sdr_config = {
    .center_freq_hz = DEFAULT_FREQ_HZ,
    .sample_rate_hz = DEFAULT_RATE_HZ,
    .bandwidth_hz = DEFAULT_RATE_HZ * 0.8,
    .gain_db = DEFAULT_GAIN_DB,
    .channel_mode = CHANNEL_MODE_SINGLE_RX0,  /* Default: single channel RX0 for backward compatibility */
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

static void encode_context_packet(uint8_t *buf, size_t *len, uint32_t stream_id, bool sample_loss);
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data, size_t num_samples, uint8_t *packet_count, uint64_t timestamp_ns, uint32_t stream_id, bool sample_loss);
/* Multicore optimization thread functions */
static void *dma_reader_thread(void *arg);     /* Core 0: DMA reader (producer) */
static void *network_thread(void *arg);        /* Core 1: Network TX + Config (consumer) */
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

/* Get current timestamp in nanoseconds (GPS-disciplined via chrony+PPS on Pluto+).
 * Uses clock_gettime(CLOCK_REALTIME) for nanosecond resolution rather than
 * gettimeofday() which is limited to microseconds. On a GPS-locked Pluto+ the
 * system clock tracks GPS to ~100-300 ns, which is embedded in the VITA49
 * fractional timestamp field and used for TDOA cross-correlation. */
static uint64_t get_timestamp_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/* Legacy microsecond wrapper for subscriber timeout tracking (no precision needed) */
static uint64_t get_timestamp_us(void) {
    return get_timestamp_ns() / 1000ULL;
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
static void encode_context_packet(uint8_t *buf, size_t *len, uint32_t stream_id, bool sample_loss) {
    vrt_context_header_t *hdr = (vrt_context_header_t *)buf;
    uint8_t *payload = buf + sizeof(vrt_context_header_t);
    size_t payload_len = 0;

    pthread_mutex_lock(&g_sdr_config.mutex);
    uint64_t freq = g_sdr_config.center_freq_hz;
    uint32_t rate = g_sdr_config.sample_rate_hz;
    uint32_t bw = g_sdr_config.bandwidth_hz;
    double gain = g_sdr_config.gain_db;
    pthread_mutex_unlock(&g_sdr_config.mutex);

    /* Timestamp - use nanosecond resolution for TDOA accuracy.
     * VITA49 fractional field is in picoseconds; multiply ns remainder by 1000. */
    uint64_t ts_ns = get_timestamp_ns();
    uint32_t ts_int = (uint32_t)(ts_ns / 1000000000ULL);
    uint64_t ts_frac = (ts_ns % 1000000000ULL) * 1000ULL;  /* ns → ps */

    /* Health status indicators */
    uint64_t underflows = sample_loss ? 1 : 0;  /* Indicate sample loss if detected */
    uint64_t overflows = 0;  /* Not currently tracked */

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
    hdr->stream_id = htonl_custom(stream_id);
    hdr->timestamp_int = htonl_custom(ts_int);
    hdr->timestamp_frac = htonll(ts_frac);
    hdr->cif = htonl_custom(cif);

    *len = sizeof(vrt_context_header_t) + payload_len;
}

/* Encode VITA49 Data packet with big-endian byte order (VITA49 standard)
 * Samples are byte-swapped to big-endian for protocol compliance.
 */
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data,
                               size_t num_samples, uint8_t *packet_count,
                               uint64_t timestamp_ns, uint32_t stream_id,
                               bool sample_loss) {
    if (num_samples == 0) {
        *len = 0;
        return;
    }

    vrt_data_header_t *hdr = (vrt_data_header_t *)buf;
    uint8_t *payload = buf + sizeof(vrt_data_header_t);

    /* Byte-swap samples to big-endian (VITA49 requirement)
     * Each complex sample = 2 int16 values (I + Q)
     */
    size_t num_int16_values = num_samples * 2;  /* I and Q for each sample */
    int16_t *src = iq_data;
    int16_t *dst = (int16_t *)payload;

    for (size_t i = 0; i < num_int16_values; i++) {
        dst[i] = (int16_t)htons((uint16_t)src[i]);
    }

    size_t payload_bytes = num_int16_values * sizeof(int16_t);

    /* Pad to 32-bit boundary if needed */
    size_t padding = (4 - (payload_bytes % 4)) % 4;
    if (padding) {
        memset(payload + payload_bytes, 0, padding);
        payload_bytes += padding;
    }

    /* Trailer */
    uint32_t *trailer = (uint32_t *)(payload + payload_bytes);
    uint32_t trailer_val = 0x40000000;  /* valid_data = 1 (bit 30) */
    if (sample_loss) {
        trailer_val |= (1 << 24);  /* sample_loss = 1 (bit 24) */
    }
    *trailer = htonl_custom(trailer_val);

    /* Calculate packet size */
    size_t total_words = 1 + 1 + 1 + 2 + (payload_bytes / 4) + 1;

    /* Use pre-computed timestamp (passed from caller, nanosecond resolution).
     * VITA49 integer field = whole seconds, fractional field = picoseconds. */
    uint32_t ts_int = (uint32_t)(timestamp_ns / 1000000000ULL);
    uint64_t ts_frac = (timestamp_ns % 1000000000ULL) * 1000ULL;  /* ns → ps */

    /* Build header */
    uint32_t header = 0;
    header |= (VRT_PKT_TYPE_DATA & 0xF) << 28;
    header |= (1 << 26);  /* Trailer present */
    header |= (VRT_TSI_UTC & 0x3) << 22;
    header |= (VRT_TSF_PICOSECONDS & 0x3) << 20;
    header |= ((*packet_count) & 0xF) << 16;
    header |= (total_words & 0xFFFF);

    hdr->header = htonl_custom(header);
    hdr->stream_id = htonl_custom(stream_id);
    hdr->timestamp_int = htonl_custom(ts_int);
    hdr->timestamp_frac = htonll(ts_frac);

    *len = sizeof(vrt_data_header_t) + payload_bytes + sizeof(uint32_t);
    *packet_count = (*packet_count + 1) & 0xF;
}

/* Parse VITA49 Context packet and extract configuration */
static int parse_context_packet(const uint8_t *buf, size_t len,
                                uint64_t *freq_hz, uint32_t *rate_hz, double *gain_db,
                                channel_mode_t *channel_mode) {
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

    /* Bit 16: Channel Mode (custom extension for dual-channel support) */
    if (cif & (1 << 16)) {
        /* Read 32-bit field: 1 byte channel_mode + 3 bytes padding */
        uint32_t mode_field = ntohl(*(uint32_t *)p);
        uint8_t mode = (mode_field >> 24) & 0xFF;  /* Extract high byte */

        /* Validate and set channel mode */
        if (mode <= CHANNEL_MODE_DUAL) {
            *channel_mode = (channel_mode_t)mode;
        }
        p += 4;
    }

    return 0;
}

/* ============================================================================
 * Burst Mode Functions
 * ============================================================================ */

/**
 * Initialize burst buffer - allocate memory
 */
static int burst_buffer_init(burst_buffer_t *burst) {
    burst->data = calloc(BURST_BUFFER_SAMPLES * 2, sizeof(int16_t));  /* *2 for I/Q pairs */
    if (!burst->data) {
        fprintf(stderr, "ERROR: Failed to allocate burst buffer (%zu MB)\n",
                (BURST_BUFFER_SAMPLES * 2 * sizeof(int16_t)) / (1024 * 1024));
        return -1;
    }
    atomic_store(&burst->fill_count, 0);
    atomic_store(&burst->ready, false);
    burst->start_timestamp_ns = 0;
    burst->sequence_base = 0;
    return 0;
}

/**
 * Free burst buffer memory
 */
static void burst_buffer_free(burst_buffer_t *burst) {
    if (burst->data) {
        free(burst->data);
        burst->data = NULL;
    }
}

/**
 * Reset burst buffer for next accumulation cycle
 */
static void burst_buffer_reset(burst_buffer_t *burst) {
    atomic_store(&burst->fill_count, 0);
    atomic_store(&burst->ready, false);
    burst->start_timestamp_ns = 0;
}

/**
 * Add samples to burst buffer
 * Returns true if buffer is now full and ready to transmit
 */
static bool burst_buffer_add_samples(burst_buffer_t *burst, const int16_t *samples,
                                     size_t sample_count, uint64_t timestamp_ns,
                                     uint32_t sequence_num) {
    size_t current_fill = atomic_load(&burst->fill_count);

    /* Record start timestamp on first samples */
    if (current_fill == 0) {
        burst->start_timestamp_ns = timestamp_ns;
        burst->sequence_base = sequence_num;
    }

    /* Calculate how many samples we can add */
    size_t space_available = BURST_BUFFER_SAMPLES - current_fill;
    size_t samples_to_add = (sample_count < space_available) ? sample_count : space_available;

    /* Copy I/Q pairs to burst buffer */
    memcpy(burst->data + (current_fill * 2), samples, samples_to_add * 4);

    /* Update fill count */
    size_t new_fill = current_fill + samples_to_add;
    atomic_store(&burst->fill_count, new_fill);

    /* Check if buffer is now full */
    if (new_fill >= BURST_BUFFER_SAMPLES) {
        atomic_store(&burst->ready, true);
        return true;
    }

    return false;
}

/**
 * Update burst mode enabled/disabled based on sample rate
 */
static void update_burst_mode(uint32_t sample_rate_hz) {
    bool should_enable = (sample_rate_hz > BURST_MODE_THRESHOLD_HZ);
    bool currently_enabled = atomic_load(&g_burst_mode_enabled);

    if (should_enable != currently_enabled) {
        atomic_store(&g_burst_mode_enabled, should_enable);
        printf("[Burst Mode] %s (sample rate: %.1f MSPS, threshold: %.1f MSPS)\n",
               should_enable ? "ENABLED" : "DISABLED",
               sample_rate_hz / 1e6,
               BURST_MODE_THRESHOLD_HZ / 1e6);

        /* Reset burst buffers when mode changes */
        if (should_enable) {
            burst_buffer_reset(&g_burst_rx0);
            burst_buffer_reset(&g_burst_rx1);
        }
    }
}

/**
 * Control thread (Core 1) - Receives configuration packets
 *
 * This thread blocks on recvfrom() until a config packet arrives.
 * Zero CPU usage while waiting.
 */

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

        /* Read channel mode to determine buffer format */
        pthread_mutex_lock(&g_sdr_config.mutex);
        channel_mode_t mode = g_sdr_config.channel_mode;
        uint32_t current_rate = g_sdr_config.sample_rate_hz;
        pthread_mutex_unlock(&g_sdr_config.mutex);

        /* Update burst mode based on sample rate */
        update_burst_mode(current_rate);
        bool burst_mode = atomic_load(&g_burst_mode_enabled);

        uint64_t timestamp_ns = get_timestamp_ns();

        if (mode == CHANNEL_MODE_DUAL) {
            /* Dual-channel mode: 8 bytes per sample (RX0_I, RX0_Q, RX1_I, RX1_Q interleaved) */
            size_t num_samples = nbytes / 8;

            /* Prepare buffer entries for both channels */
            iq_buffer_entry_t buffer_rx0 = {
                .data = g_ring_buffer_rx0.sample_pool[buffer_idx],
                .sample_count = num_samples,
                .timestamp_ns = timestamp_ns,
                .sequence_num = atomic_fetch_add(&g_sequence_counter_rx0, 1),
                .buffer_id = buffer_idx
            };

            iq_buffer_entry_t buffer_rx1 = {
                .data = g_ring_buffer_rx1.sample_pool[buffer_idx],
                .sample_count = num_samples,
                .timestamp_ns = timestamp_ns,
                .sequence_num = atomic_fetch_add(&g_sequence_counter_rx1, 1),
                .buffer_id = buffer_idx
            };

            /* De-interleave samples: [RX0_I, RX0_Q, RX1_I, RX1_Q, ...] -> separate buffers
             * This is performance-critical, so we use direct pointer arithmetic */
            int16_t *src = samples;
            int16_t *dst_rx0 = buffer_rx0.data;
            int16_t *dst_rx1 = buffer_rx1.data;

            for (size_t i = 0; i < num_samples; i++) {
                *dst_rx0++ = *src++;  /* RX0 I */
                *dst_rx0++ = *src++;  /* RX0 Q */
                *dst_rx1++ = *src++;  /* RX1 I */
                *dst_rx1++ = *src++;  /* RX1 Q */
            }

            if (burst_mode) {
                /* Burst mode: accumulate samples in burst buffers */
                burst_buffer_add_samples(&g_burst_rx0, buffer_rx0.data, num_samples,
                                        timestamp_ns, buffer_rx0.sequence_num);
                burst_buffer_add_samples(&g_burst_rx1, buffer_rx1.data, num_samples,
                                        timestamp_ns, buffer_rx1.sequence_num);
                g_stats.dma_buffers_processed++;
            } else {
                /* Streaming mode: push to ring buffers immediately */
                bool rx0_ok = ring_buffer_push(&g_ring_buffer_rx0, &buffer_rx0);
                bool rx1_ok = ring_buffer_push(&g_ring_buffer_rx1, &buffer_rx1);

                if (!rx0_ok || !rx1_ok) {
                    g_stats.ring_buffer_drops++;
                } else {
                    g_stats.dma_buffers_processed++;
                }
            }

        } else {
            /* Single-channel mode: 4 bytes per I/Q pair (RX0 or RX1) */
            size_t num_samples = nbytes / 4;

            /* Select appropriate ring buffer based on channel mode */
            lock_free_ring_buffer_t *target_buffer =
                (mode == CHANNEL_MODE_SINGLE_RX1) ? &g_ring_buffer_rx1 : &g_ring_buffer_rx0;
            atomic_uint *seq_counter =
                (mode == CHANNEL_MODE_SINGLE_RX1) ? &g_sequence_counter_rx1 : &g_sequence_counter_rx0;

            /* Prepare ring buffer entry */
            iq_buffer_entry_t buffer_entry = {
                .data = target_buffer->sample_pool[buffer_idx],
                .sample_count = num_samples,
                .timestamp_ns = timestamp_ns,
                .sequence_num = atomic_fetch_add(seq_counter, 1),
                .buffer_id = buffer_idx
            };

            /* Fast memcpy - copy entire buffer at once */
            memcpy(buffer_entry.data, samples, num_samples * 4);

            if (burst_mode) {
                /* Burst mode: accumulate samples in burst buffer */
                burst_buffer_t *target_burst =
                    (mode == CHANNEL_MODE_SINGLE_RX1) ? &g_burst_rx1 : &g_burst_rx0;

                burst_buffer_add_samples(target_burst, buffer_entry.data, num_samples,
                                        timestamp_ns, buffer_entry.sequence_num);
                g_stats.dma_buffers_processed++;
            } else {
                /* Streaming mode: push to ring buffer (non-blocking) */
                if (!ring_buffer_push(target_buffer, &buffer_entry)) {
                    g_stats.ring_buffer_drops++;
                    /* Ring buffer full - network thread may be overloaded */
                } else {
                    g_stats.dma_buffers_processed++;
                }
            }
        }

        /* Rotate to next buffer in pool */
        buffer_idx = (buffer_idx + 1) % RING_BUFFER_CAPACITY;
    }

    printf("[DMA Reader] Stopped\n");
    iio_buffer_destroy(rxbuf);
    return NULL;
}

/**
 * Helper: Transmit IQ buffer as VITA49 packets
 * Used by both streaming mode (ring buffer) and burst mode
 */
static void transmit_iq_buffer(iq_buffer_entry_t *iq_buffer, uint32_t stream_id,
                               uint8_t *packet_counter, int *context_counter,
                               bool *sample_loss_flag, int data_sock,
                               packet_batch_t *batch, uint8_t *context_buf,
                               size_t *context_packet_len) {
    size_t offset = 0;
    size_t packets_this_buffer = 0;
    size_t bytes_this_buffer = 0;

    /* Encode and batch all packets from this buffer */
    while (offset < iq_buffer->sample_count) {
        size_t samples_this_packet = MIN(g_samples_per_packet,
                                         iq_buffer->sample_count - offset);

        uint8_t *packet_buf = batch_get_buffer(batch);
        size_t packet_len;

        encode_data_packet(packet_buf, &packet_len,
                         iq_buffer->data + (offset * 2),
                         samples_this_packet,
                         packet_counter,
                         iq_buffer->timestamp_ns,
                         stream_id,
                         *sample_loss_flag);

        batch_commit_packet(batch, packet_len);
        packets_this_buffer++;
        bytes_this_buffer += packet_len;
        offset += samples_this_packet;

        /* Send context packet periodically */
        (*context_counter)++;
        if (*context_counter >= CONTEXT_INTERVAL) {
            if (batch->count > 0) {
                batch_flush_to_all_subscribers(data_sock, batch);
                batch_init(batch);
            }

            encode_context_packet(context_buf, context_packet_len, stream_id, *sample_loss_flag);
            broadcast_to_subscribers(data_sock, context_buf, *context_packet_len);
            g_stats.contexts_sent++;
            *context_counter = 0;
            *sample_loss_flag = false;  /* Reset after reporting */
        }

        if (batch->count >= SEND_BATCH_SIZE) {
            batch_flush_to_all_subscribers(data_sock, batch);
            batch_init(batch);
        }
    }

    /* Flush any remaining packets */
    if (batch->count > 0) {
        batch_flush_to_all_subscribers(data_sock, batch);
        batch_init(batch);
    }

    /* Update statistics */
    g_stats.network_thread_processed++;
    g_stats.packets_sent += packets_this_buffer;
    g_stats.bytes_sent += bytes_this_buffer;
}

/*
 * Network Thread (Core 1) - Multicore Optimization Consumer
 *
 * Phase 3: Combined network transmission and configuration handling
 * Consumes from ring buffer and handles:
 * - VITA49 packet encoding from ring buffer IQ data
 * - sendmmsg() batch transmission
 * - Configuration packet reception (merged from control_thread)
 * - Subscriber management
 * - Context packet transmission
 *
 * This achieves 95% Core 1 utilization vs 10% in Phase 2.
 */
static void *network_thread(void *arg) {
    network_thread_args_t *args = (network_thread_args_t *)arg;
    int control_sock = args->control_sock;
    
    /* Pin to Core 1 */
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(1, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);

    printf("[Network Thread] Started - pinned to Core 1\n");
    printf("[Network Thread] Handling: Ring buffer consumption + Config + Network TX\n");

    /* Create UDP socket for data transmission */
    int data_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (data_sock < 0) {
        fprintf(stderr, "[Network Thread] ERROR: Failed to create data socket\n");
        return NULL;
    }

    /* Set large send buffer for burst transmission */
    int sndbuf = 2 * 1024 * 1024;  /* 2 MB send buffer */
    setsockopt(data_sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

    /* Set control socket to non-blocking for polling */
    int flags = fcntl(control_sock, F_GETFL, 0);
    fcntl(control_sock, F_SETFL, flags | O_NONBLOCK);

    /* Allocate packet batch on heap - it's ~1MB due to packet buffers */
    packet_batch_t *batch = calloc(1, sizeof(packet_batch_t));
    if (!batch) {
        fprintf(stderr, "[Network Thread] ERROR: Failed to allocate batch buffer\n");
        close(data_sock);
        return NULL;
    }

    uint8_t context_buf[2048];  /* For immediate context packet transmission */
    size_t context_packet_len;
    uint8_t packet_count_rx0 = 0;  /* Separate packet counter for RX0 channel */
    uint8_t packet_count_rx1 = 0;  /* Separate packet counter for RX1 channel */
    int packets_since_context_rx0 = 0;
    int packets_since_context_rx1 = 0;
    uint64_t total_packets_sent = 0;

    /* Sample loss detection: track last sequence numbers */
    uint32_t last_sequence_rx0 = UINT32_MAX;  /* Init to max so first packet doesn't trigger loss */
    uint32_t last_sequence_rx1 = UINT32_MAX;
    bool sample_loss_rx0 = false;
    bool sample_loss_rx1 = false;

    printf("[Network Thread] Using sendmmsg() batching: %d packets/syscall\n", SEND_BATCH_SIZE);

    while (g_running) {
        bool work_done = false;
        
        /* 1. Check for configuration packets (non-blocking) */
        uint8_t config_buf[2048];
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);
        
        ssize_t config_recv = recvfrom(control_sock, config_buf, sizeof(config_buf), 0,
                                      (struct sockaddr *)&client_addr, &client_len);
        
        if (config_recv > 0) {
            char ip_str[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &client_addr.sin_addr, ip_str, INET_ADDRSTRLEN);
            printf("\n[Network Thread] Config from %s (%zd bytes)\n", ip_str, config_recv);
            
            /* Parse and apply configuration */
            uint64_t new_freq = g_sdr_config.center_freq_hz;
            uint32_t new_rate = g_sdr_config.sample_rate_hz;
            double new_gain = g_sdr_config.gain_db;
            channel_mode_t new_channel_mode = g_sdr_config.channel_mode;

            if (parse_context_packet(config_buf, config_recv, &new_freq, &new_rate, &new_gain, &new_channel_mode) == 0) {
                bool changed = false;

                pthread_mutex_lock(&g_sdr_config.mutex);

                if (new_freq != g_sdr_config.center_freq_hz) {
                    printf("[Network Thread] Freq: %.3f -> %.3f MHz\n",
                           g_sdr_config.center_freq_hz / 1e6, new_freq / 1e6);
                    g_sdr_config.center_freq_hz = new_freq;
                    changed = true;
                }

                if (new_rate != g_sdr_config.sample_rate_hz) {
                    printf("[Network Thread] Rate: %.1f -> %.1f MSPS\n",
                           g_sdr_config.sample_rate_hz / 1e6, new_rate / 1e6);
                    g_sdr_config.sample_rate_hz = new_rate;
                    changed = true;
                }

                if (fabs(new_gain - g_sdr_config.gain_db) > 0.1) {
                    printf("[Network Thread] Gain: %.1f -> %.1f dB\n",
                           g_sdr_config.gain_db, new_gain);
                    g_sdr_config.gain_db = new_gain;
                    changed = true;
                }

                if (new_channel_mode != g_sdr_config.channel_mode) {
                    const char *mode_names[] = {"RX0", "RX1", "DUAL"};
                    printf("[Network Thread] Channel Mode: %s -> %s\n",
                           mode_names[g_sdr_config.channel_mode],
                           mode_names[new_channel_mode]);
                    g_sdr_config.channel_mode = new_channel_mode;
                    changed = true;
                }

                if (changed) {
                    g_sdr_config.config_changed = true;
                }
                
                pthread_mutex_unlock(&g_sdr_config.mutex);
                
                /* Add client as subscriber */
                add_subscriber(&client_addr);
                
                /* Send immediate context packet response */
                encode_context_packet(context_buf, &context_packet_len, STREAM_ID_RX0, false);
                sendto(data_sock, context_buf, context_packet_len, 0,
                      (struct sockaddr *)&client_addr, sizeof(client_addr));
                g_stats.contexts_sent++;
                
                work_done = true;
            }
        }

        /* 2. Consume IQ data from burst buffer(s) or ring buffer(s) and transmit */

        /* Check if in burst mode */
        bool burst_mode = atomic_load(&g_burst_mode_enabled);

        /* Read channel mode to determine single vs dual-channel */
        pthread_mutex_lock(&g_sdr_config.mutex);
        channel_mode_t mode = g_sdr_config.channel_mode;
        pthread_mutex_unlock(&g_sdr_config.mutex);

        /* BURST MODE: Transmit accumulated samples when buffer is full */
        if (burst_mode) {
            bool transmitted_rx0 = false;
            bool transmitted_rx1 = false;

            /* Check RX0 burst buffer */
            if (atomic_load(&g_burst_rx0.ready) &&
                (mode == CHANNEL_MODE_SINGLE_RX0 || mode == CHANNEL_MODE_DUAL)) {

                size_t sample_count = atomic_load(&g_burst_rx0.fill_count);
                printf("[Network Thread] Transmitting RX0 burst: %zu samples (%.1f MB)\n",
                       sample_count, (sample_count * 4.0) / (1024 * 1024));

                /* Create temporary buffer entry for burst transmission */
                iq_buffer_entry_t burst_entry = {
                    .data = g_burst_rx0.data,
                    .sample_count = sample_count,
                    .timestamp_ns = g_burst_rx0.start_timestamp_ns,
                    .sequence_num = g_burst_rx0.sequence_base,
                    .buffer_id = 0
                };

                /* Transmit using existing packet encoding (stream ID RX0) */
                transmit_iq_buffer(&burst_entry, STREAM_ID_RX0, &packet_count_rx0,
                                  &packets_since_context_rx0, &sample_loss_rx0,
                                  data_sock, batch, context_buf, &context_packet_len);

                /* Reset for next burst */
                burst_buffer_reset(&g_burst_rx0);
                transmitted_rx0 = true;
            }

            /* Check RX1 burst buffer */
            if (atomic_load(&g_burst_rx1.ready) &&
                (mode == CHANNEL_MODE_SINGLE_RX1 || mode == CHANNEL_MODE_DUAL)) {

                size_t sample_count = atomic_load(&g_burst_rx1.fill_count);
                printf("[Network Thread] Transmitting RX1 burst: %zu samples (%.1f MB)\n",
                       sample_count, (sample_count * 4.0) / (1024 * 1024));

                iq_buffer_entry_t burst_entry = {
                    .data = g_burst_rx1.data,
                    .sample_count = sample_count,
                    .timestamp_ns = g_burst_rx1.start_timestamp_ns,
                    .sequence_num = g_burst_rx1.sequence_base,
                    .buffer_id = 0
                };

                transmit_iq_buffer(&burst_entry, STREAM_ID_RX1, &packet_count_rx1,
                                  &packets_since_context_rx1, &sample_loss_rx1,
                                  data_sock, batch, context_buf, &context_packet_len);

                burst_buffer_reset(&g_burst_rx1);
                transmitted_rx1 = true;
            }

            if (transmitted_rx0 || transmitted_rx1) {
                work_done = true;
            }
        }
        /* STREAMING MODE: Continuous transmission from ring buffers */
        else {

        if (mode == CHANNEL_MODE_DUAL) {
            /* Dual-channel mode: Pop from both buffers and alternate packets */
            iq_buffer_entry_t iq_rx0, iq_rx1;
            bool has_rx0 = ring_buffer_pop(&g_ring_buffer_rx0, &iq_rx0);
            bool has_rx1 = ring_buffer_pop(&g_ring_buffer_rx1, &iq_rx1);

            /* Detect sequence gaps for sample loss indication */
            sample_loss_rx0 = false;
            sample_loss_rx1 = false;

            if (has_rx0) {
                if (last_sequence_rx0 != UINT32_MAX) {
                    uint32_t expected = last_sequence_rx0 + 1;
                    if (iq_rx0.sequence_num != expected) {
                        sample_loss_rx0 = true;
                        uint32_t gap = iq_rx0.sequence_num - expected;
                        printf("[Network Thread] RX0 sample loss: gap of %u buffers (seq %u -> %u)\n",
                               gap, last_sequence_rx0, iq_rx0.sequence_num);
                    }
                }
                last_sequence_rx0 = iq_rx0.sequence_num;
            }

            if (has_rx1) {
                if (last_sequence_rx1 != UINT32_MAX) {
                    uint32_t expected = last_sequence_rx1 + 1;
                    if (iq_rx1.sequence_num != expected) {
                        sample_loss_rx1 = true;
                        uint32_t gap = iq_rx1.sequence_num - expected;
                        printf("[Network Thread] RX1 sample loss: gap of %u buffers (seq %u -> %u)\n",
                               gap, last_sequence_rx1, iq_rx1.sequence_num);
                    }
                }
                last_sequence_rx1 = iq_rx1.sequence_num;
            }

            if (has_rx0 || has_rx1) {
                batch_init(batch);
                size_t packets_this_iteration = 0;
                size_t bytes_this_iteration = 0;

                /* Process both channels in an alternating pattern for lowest latency */
                size_t max_samples = has_rx0 ? iq_rx0.sample_count : 0;
                if (has_rx1 && iq_rx1.sample_count > max_samples) {
                    max_samples = iq_rx1.sample_count;
                }

                size_t offset_rx0 = 0, offset_rx1 = 0;
                while ((has_rx0 && offset_rx0 < iq_rx0.sample_count) ||
                       (has_rx1 && offset_rx1 < iq_rx1.sample_count)) {

                    /* Send RX0 packet if available */
                    if (has_rx0 && offset_rx0 < iq_rx0.sample_count) {
                        size_t samples_this_packet = MIN(g_samples_per_packet,
                                                        iq_rx0.sample_count - offset_rx0);
                        uint8_t *packet_buf = batch_get_buffer(batch);
                        size_t packet_len;

                        encode_data_packet(packet_buf, &packet_len,
                                         iq_rx0.data + (offset_rx0 * 2),
                                         samples_this_packet,
                                         &packet_count_rx0,
                                         iq_rx0.timestamp_ns,
                                         STREAM_ID_RX0,
                                         sample_loss_rx0);

                        batch_commit_packet(batch, packet_len);
                        packets_this_iteration++;
                        bytes_this_iteration += packet_len;
                        offset_rx0 += samples_this_packet;

                        /* Context packet for RX0 */
                        packets_since_context_rx0++;
                        if (packets_since_context_rx0 >= CONTEXT_INTERVAL) {
                            if (batch->count > 0) {
                                batch_flush_to_all_subscribers(data_sock, batch);
                                batch_init(batch);
                            }
                            encode_context_packet(context_buf, &context_packet_len, STREAM_ID_RX0, sample_loss_rx0);
                            broadcast_to_subscribers(data_sock, context_buf, context_packet_len);
                            g_stats.contexts_sent++;
                            packets_since_context_rx0 = 0;
                            sample_loss_rx0 = false;  /* Reset after reporting */
                        }

                        if (batch->count >= SEND_BATCH_SIZE) {
                            batch_flush_to_all_subscribers(data_sock, batch);
                            batch_init(batch);
                        }
                    }

                    /* Send RX1 packet if available (alternate for low latency) */
                    if (has_rx1 && offset_rx1 < iq_rx1.sample_count) {
                        size_t samples_this_packet = MIN(g_samples_per_packet,
                                                        iq_rx1.sample_count - offset_rx1);
                        uint8_t *packet_buf = batch_get_buffer(batch);
                        size_t packet_len;

                        encode_data_packet(packet_buf, &packet_len,
                                         iq_rx1.data + (offset_rx1 * 2),
                                         samples_this_packet,
                                         &packet_count_rx1,
                                         iq_rx1.timestamp_ns,
                                         STREAM_ID_RX1,
                                         sample_loss_rx1);

                        batch_commit_packet(batch, packet_len);
                        packets_this_iteration++;
                        bytes_this_iteration += packet_len;
                        offset_rx1 += samples_this_packet;

                        /* Context packet for RX1 */
                        packets_since_context_rx1++;
                        if (packets_since_context_rx1 >= CONTEXT_INTERVAL) {
                            if (batch->count > 0) {
                                batch_flush_to_all_subscribers(data_sock, batch);
                                batch_init(batch);
                            }
                            encode_context_packet(context_buf, &context_packet_len, STREAM_ID_RX1, sample_loss_rx1);
                            broadcast_to_subscribers(data_sock, context_buf, context_packet_len);
                            g_stats.contexts_sent++;
                            packets_since_context_rx1 = 0;
                            sample_loss_rx1 = false;  /* Reset after reporting */
                        }

                        if (batch->count >= SEND_BATCH_SIZE) {
                            batch_flush_to_all_subscribers(data_sock, batch);
                            batch_init(batch);
                        }
                    }
                }

                /* Flush remaining packets */
                if (batch->count > 0) {
                    batch_flush_to_all_subscribers(data_sock, batch);
                    batch_init(batch);
                }

                /* Update statistics */
                g_stats.network_thread_processed++;
                g_stats.packets_sent += packets_this_iteration;
                g_stats.bytes_sent += bytes_this_iteration;
                total_packets_sent += packets_this_iteration;

                if (total_packets_sent % SUBSCRIBER_CLEANUP_INTERVAL == 0) {
                    cleanup_dead_subscribers();
                }

                work_done = true;
            }
        } else {
            /* Single-channel mode (RX0 or RX1) */
            lock_free_ring_buffer_t *source_buffer =
                (mode == CHANNEL_MODE_SINGLE_RX1) ? &g_ring_buffer_rx1 : &g_ring_buffer_rx0;
            uint8_t *packet_counter =
                (mode == CHANNEL_MODE_SINGLE_RX1) ? &packet_count_rx1 : &packet_count_rx0;
            int *context_counter =
                (mode == CHANNEL_MODE_SINGLE_RX1) ? &packets_since_context_rx1 : &packets_since_context_rx0;
            uint32_t stream_id =
                (mode == CHANNEL_MODE_SINGLE_RX1) ? STREAM_ID_RX1 : STREAM_ID_RX0;
            uint32_t *last_sequence =
                (mode == CHANNEL_MODE_SINGLE_RX1) ? &last_sequence_rx1 : &last_sequence_rx0;
            bool *sample_loss_flag =
                (mode == CHANNEL_MODE_SINGLE_RX1) ? &sample_loss_rx1 : &sample_loss_rx0;

            iq_buffer_entry_t iq_buffer;
            if (ring_buffer_pop(source_buffer, &iq_buffer)) {
                /* Detect sequence gaps for sample loss indication */
                *sample_loss_flag = false;
                if (*last_sequence != UINT32_MAX) {
                    uint32_t expected = *last_sequence + 1;
                    if (iq_buffer.sequence_num != expected) {
                        *sample_loss_flag = true;
                        uint32_t gap = iq_buffer.sequence_num - expected;
                        const char *channel_name = (mode == CHANNEL_MODE_SINGLE_RX1) ? "RX1" : "RX0";
                        printf("[Network Thread] %s sample loss: gap of %u buffers (seq %u -> %u)\n",
                               channel_name, gap, *last_sequence, iq_buffer.sequence_num);
                    }
                }
                *last_sequence = iq_buffer.sequence_num;
                /* Process entire IQ buffer into VITA49 packets */
                batch_init(batch);
                size_t packets_this_buffer = 0;
                size_t bytes_this_buffer = 0;

                for (size_t offset = 0; offset < iq_buffer.sample_count; offset += g_samples_per_packet) {
                    size_t samples_this_packet = MIN(g_samples_per_packet,
                                                    iq_buffer.sample_count - offset);

                    uint8_t *packet_buf = batch_get_buffer(batch);
                    size_t packet_len;

                    encode_data_packet(packet_buf, &packet_len,
                                     iq_buffer.data + (offset * 2),
                                     samples_this_packet,
                                     packet_counter,
                                     iq_buffer.timestamp_ns,
                                     stream_id,
                                     *sample_loss_flag);

                    batch_commit_packet(batch, packet_len);
                    packets_this_buffer++;
                    bytes_this_buffer += packet_len;

                    /* Send context packet periodically */
                    (*context_counter)++;
                    if (*context_counter >= CONTEXT_INTERVAL) {
                        if (batch->count > 0) {
                            batch_flush_to_all_subscribers(data_sock, batch);
                            batch_init(batch);
                        }

                        encode_context_packet(context_buf, &context_packet_len, stream_id, *sample_loss_flag);
                        broadcast_to_subscribers(data_sock, context_buf, context_packet_len);
                        g_stats.contexts_sent++;
                        *context_counter = 0;
                        *sample_loss_flag = false;  /* Reset after reporting */
                    }

                    if (batch->count >= SEND_BATCH_SIZE) {
                        batch_flush_to_all_subscribers(data_sock, batch);
                        batch_init(batch);
                    }
                }

                /* Flush any remaining packets */
                if (batch->count > 0) {
                    batch_flush_to_all_subscribers(data_sock, batch);
                    batch_init(batch);
                }

                /* Update statistics */
                g_stats.network_thread_processed++;
                g_stats.packets_sent += packets_this_buffer;
                g_stats.bytes_sent += bytes_this_buffer;
                total_packets_sent += packets_this_buffer;

                if (total_packets_sent % SUBSCRIBER_CLEANUP_INTERVAL == 0) {
                    cleanup_dead_subscribers();
                }

                work_done = true;
            }
        }
        }  /* End of streaming mode (ring buffer) processing */

        /* 3. Brief sleep only if no work was done */
        if (!work_done) {
            usleep(10);  /* 10 microseconds - very brief */
        }
    }

    printf("[Network Thread] Stopped\n");
    free(batch);
    close(data_sock);
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
    channel_mode_t target_channel_mode = g_sdr_config.channel_mode;
    pthread_mutex_unlock(&g_sdr_config.mutex);

    char buf[64];
    ssize_t ret;

    /* First, disable all DMA channels before changing sample rate */
    struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
    struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);
    struct iio_channel *rx1_i = iio_device_find_channel(dev, "voltage2", false);
    struct iio_channel *rx1_q = iio_device_find_channel(dev, "voltage3", false);

    if (rx0_i) iio_channel_disable(rx0_i);
    if (rx0_q) iio_channel_disable(rx0_q);
    if (rx1_i) iio_channel_disable(rx1_i);
    if (rx1_q) iio_channel_disable(rx1_q);

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

    /* Re-enable DMA channels for buffer creation based on channel mode */
    if (target_channel_mode == CHANNEL_MODE_SINGLE_RX0 || target_channel_mode == CHANNEL_MODE_DUAL) {
        if (rx0_i) iio_channel_enable(rx0_i);
        if (rx0_q) iio_channel_enable(rx0_q);
    }
    if (target_channel_mode == CHANNEL_MODE_SINGLE_RX1 || target_channel_mode == CHANNEL_MODE_DUAL) {
        if (rx1_i) iio_channel_enable(rx1_i);
        if (rx1_q) iio_channel_enable(rx1_q);
    }

    const char *mode_str = (target_channel_mode == CHANNEL_MODE_DUAL) ? "DUAL (RX0+RX1)" :
                           (target_channel_mode == CHANNEL_MODE_SINGLE_RX1) ? "RX1" : "RX0";
    printf("[Config] Configured: %.1f MHz, %.1f MSPS, %.1f dB, Mode: %s\n",
           target_freq / 1e6,
           target_rate / 1e6,
           target_gain,
           mode_str);

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
            printf("\nArchitecture (Phase 3 - Multicore Optimization):\n");
            printf("  Dual-core producer/consumer with lock-free ring buffer:\n");
            printf("  - Core 0 (DMA Reader): High-priority DMA + ring buffer push (95%% CPU)\n");
            printf("  - Core 1 (Network): Ring buffer consume + VITA49 encode + TX (95%% CPU)\n");
            printf("\nExpected throughput (Optimized):\n");
            printf("  5 MSPS  -> ~80 Mbps   (2x improvement)\n");
            printf("  10 MSPS -> ~160 Mbps  (2x improvement)\n");
            printf("  20 MSPS -> ~320 Mbps  (2x improvement)\n");
            printf("  30 MSPS -> ~400 Mbps  (1.67x improvement, approaching Gigabit limit)\n");
            return 0;
        }
    }

    /* Calculate optimal packet size based on MTU */
    g_samples_per_packet = calculate_optimal_samples_per_packet(mtu);

    size_t packet_payload = g_samples_per_packet * 2 * sizeof(int16_t);
    size_t total_vita49_packet = packet_payload + VITA49_OVERHEAD;
    size_t total_udp_datagram = total_vita49_packet + IP_UDP_OVERHEAD;

    printf("========================================\n");
    printf("VITA49 Streamer for Pluto (Multicore)\n");
    printf("Phase 3: Dual-core optimization\n");
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

    /* Initialize ring buffers for multicore optimization */
    ring_buffer_init(&g_ring_buffer_rx0);
    ring_buffer_init(&g_ring_buffer_rx1);
    printf("Ring buffers initialized: RX0=%d entries (%zu MB), RX1=%d entries (%zu MB)\n",
           RING_BUFFER_CAPACITY, sizeof(g_ring_buffer_rx0) / (1024*1024),
           RING_BUFFER_CAPACITY, sizeof(g_ring_buffer_rx1) / (1024*1024));

    /* Initialize burst mode buffers */
    if (burst_buffer_init(&g_burst_rx0) < 0 || burst_buffer_init(&g_burst_rx1) < 0) {
        fprintf(stderr, "ERROR: Failed to allocate burst buffers\n");
        close(control_sock);
        iio_context_destroy(ctx);
        return 1;
    }
    printf("Burst buffers initialized: %zu samples per channel (%.1f MB each, %.1f MB total)\n",
           BURST_BUFFER_SAMPLES,
           (BURST_BUFFER_SAMPLES * 2 * sizeof(int16_t)) / (1024.0 * 1024.0),
           (BURST_BUFFER_SAMPLES * 2 * sizeof(int16_t) * 2) / (1024.0 * 1024.0));
    printf("Burst mode: Enabled automatically when sample rate > %.1f MSPS\n\n",
           BURST_MODE_THRESHOLD_HZ / 1e6);

    /* Start multicore optimized threads - Phase 3: Full dual-core utilization */
    pthread_t dma_tid, network_tid;
    
    /* Prepare arguments for network thread */
    network_thread_args_t net_args = {
        .iio_ctx = ctx,
        .control_sock = control_sock
    };

    pthread_create(&dma_tid, NULL, dma_reader_thread, ctx);
    pthread_create(&network_tid, NULL, network_thread, &net_args);

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

        /* Phase 3: Full dual-core performance stats */
        ring_buffer_stats_t rb_stats = ring_buffer_get_stats(&g_ring_buffer_rx0);
        printf("[Core 0] DMA: %llu bufs processed, Ring pushes: %.1f%% success\n",
               (unsigned long long)g_stats.dma_buffers_processed,
               rb_stats.push_success_rate * 100.0);
        printf("[Core 1] Network: %llu bufs consumed, Ring: %.1f%% full, Drops: %llu\n",
               (unsigned long long)g_stats.network_thread_processed,
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
    pthread_join(network_tid, NULL);

    /* Free burst buffers */
    burst_buffer_free(&g_burst_rx0);
    burst_buffer_free(&g_burst_rx1);

    close(control_sock);
    iio_context_destroy(ctx);

    printf("\nStopped\n");
    return 0;
}
