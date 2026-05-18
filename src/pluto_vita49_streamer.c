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
#include <unistd.h>
#include <pthread.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <signal.h>
#include <fcntl.h>
#include <errno.h>
#include <iio.h>

/* Configuration */
#define DEFAULT_FREQ_HZ         2400000000ULL   /* 2.4 GHz */
#define DEFAULT_RATE_HZ         30000000        /* 30 MSPS */
#define DEFAULT_GAIN_DB         20.0
#define DEFAULT_TX_GAIN_DB      -10.0           /* AD9361 TX is attenuation: -89.75..0 dB */
#define DEFAULT_BUFFER_SIZE     16384           /* Samples per RX buffer */
#define DEFAULT_TX_BUFFER_SIZE  4096            /* Samples per TX push (per channel) */
#define CONTROL_PORT            4990            /* Config reception port */
#define DATA_PORT               4991            /* RX data streaming port */
#define TX_DATA_PORT            4992            /* TX data reception port (inbound IF Data) */
#define CONTEXT_INTERVAL        100             /* Send context every N packets */
#define MAX_SUBSCRIBERS         16              /* Max simultaneous receivers */

/* Subscriber Management Configuration */
#define SUBSCRIBER_TIMEOUT_US       30000000    /* 30 seconds */
#define MAX_CONSECUTIVE_FAILURES    10          /* Remove after 10 failures */
#define SUBSCRIBER_CLEANUP_INTERVAL 100         /* Check every 100 packets */

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

/* Stream IDs. High byte 0x01 = RX, 0x02 = TX. Low byte = channel index.
 * Preserves backward-compat with existing consumers that filter on
 * RX0_STREAM_ID. */
#define RX_STREAM_ID_HIGH_BYTE  0x01
#define TX_STREAM_ID_HIGH_BYTE  0x02
#define RX0_STREAM_ID           0x01000000
#define RX1_STREAM_ID           0x01000001
#define TX0_STREAM_ID           0x02000000
#define TX1_STREAM_ID           0x02000001

/* config_epoch carried via VRT Class ID. Matches the Python helper
 * make_epoch_class_id() in src/vita49/packets.py: when a packet is
 * tagged, its Class ID has OUI=EPOCH_CLASS_OUI, packet_class_code=
 * EPOCH_PACKET_CLASS_CODE, and information_class_code=epoch.
 * Epoch 0 = "untagged" -> no Class ID emitted -> wire format unchanged. */
#define EPOCH_CLASS_OUI         0x00005A
#define EPOCH_PACKET_CLASS_CODE 0xE000

/* Global state */
static volatile bool g_running = true;
static pthread_mutex_t g_subscribers_mutex = PTHREAD_MUTEX_INITIALIZER;
static size_t g_samples_per_packet = 360;  /* Will be calculated at runtime based on MTU */

/* Monotonic config generation. Bumped by the control thread when a
 * reconfig context arrives with a new epoch (or when the local config
 * changes in a way that should invalidate in-flight samples). Streaming
 * threads read this and stamp it on every outbound packet via Class ID. */
static volatile uint16_t g_current_epoch = 0;

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
    /* Bit 0 = RX0 enabled, bit 1 = RX1 enabled. Default 0b01 keeps
     * the binary's behavior bit-identical for single-RX deployments. */
    uint8_t enabled_rx_mask;
    bool config_changed;  /* Flag to signal streaming thread to reconfigure */
    pthread_mutex_t mutex;
} sdr_config_t;

static sdr_config_t g_sdr_config = {
    .center_freq_hz = DEFAULT_FREQ_HZ,
    .sample_rate_hz = DEFAULT_RATE_HZ,
    .bandwidth_hz = DEFAULT_RATE_HZ * 0.8,
    .gain_db = DEFAULT_GAIN_DB,
    .enabled_rx_mask = 0x01,
    .config_changed = false,
    .mutex = PTHREAD_MUTEX_INITIALIZER
};

/* TX Configuration. Default mask 0x00 = TX disabled (binary stays
 * RX-only unless --tx-channels is passed). LO and gain are independent
 * of RX; sample rate is shared (AD9361 hardware constraint). */
typedef struct {
    uint64_t center_freq_hz;
    double gain_db;
    uint8_t enabled_tx_mask;  /* bit 0 = TX0, bit 1 = TX1 */
    pthread_mutex_t mutex;
} tx_config_t;

static tx_config_t g_tx_config = {
    .center_freq_hz = DEFAULT_FREQ_HZ,
    .gain_db = DEFAULT_TX_GAIN_DB,
    .enabled_tx_mask = 0x00,
    .mutex = PTHREAD_MUTEX_INITIALIZER
};

/* TX runtime stats */
typedef struct {
    uint64_t packets_received;       /* Inbound IF Data packets on TX_DATA_PORT */
    uint64_t packets_transmitted;    /* Successfully pushed to iio_buffer */
    uint64_t packets_dropped_wrong_stream;  /* Stream ID high byte != 0x02 */
    uint64_t packets_dropped_disabled_ch;   /* Stream ID channel not enabled */
    uint64_t packets_dropped_decode;        /* Failed packet parse */
    uint64_t push_failures;          /* iio_buffer_push returned < 0 */
    uint64_t bytes_pushed;
    pthread_mutex_t mutex;
} tx_stats_t;

static tx_stats_t g_tx_stats = {0};

/* TX replay mode: when --tx-replay PATH is set, the TX thread reads IQ
 * from disk instead of accepting UDP packets. Mutually exclusive with
 * the UDP TX path. File format: raw interleaved int16_t I,Q in native
 * (little-endian on ARM) byte order, no header. */
static const char *g_tx_replay_path = NULL;
static bool g_tx_replay_loop = false;

/* Statistics */
typedef struct {
    // Existing fields
    uint64_t packets_sent;
    uint64_t bytes_sent;
    uint32_t contexts_sent;
    uint32_t reconfigs;

    // NEW: Health monitoring
    uint64_t underflows;
    uint64_t overflows;
    uint64_t refill_failures;
    uint64_t send_failures;
    uint64_t timestamp_jumps;
    uint64_t last_timestamp_us;

    // NEW: Performance metrics
    uint64_t min_loop_time_us;
    uint64_t max_loop_time_us;
    uint64_t total_loop_time_us;
    uint64_t loop_iterations;

    pthread_mutex_t mutex;
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

/* Function prototypes */
static void signal_handler(int sig);
static void add_subscriber(struct sockaddr_in *addr);
static int send_to_subscriber(int sock, uint8_t *buf, size_t len, subscriber_t *sub);
static void cleanup_dead_subscribers(void);
static void broadcast_to_subscribers(int sock, uint8_t *buf, size_t len);
static uint64_t get_timestamp_us(void);
static size_t calculate_optimal_samples_per_packet(size_t mtu);
static void encode_context_packet(uint8_t *buf, size_t *len, uint32_t stream_id);
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data, size_t num_samples, uint8_t *packet_count, uint32_t stream_id);
static void *control_thread(void *arg);
static void *streaming_thread(void *arg);
static void *tx_thread(void *arg);
static void *tx_replay_thread(void *arg);
static int configure_sdr(struct iio_context *ctx, struct iio_device *dev);
static int configure_tx(struct iio_context *ctx, struct iio_device **tx_dev_out);

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

/* Encode VITA49 Context packet for the given stream_id (channel).
 * If g_current_epoch != 0, inserts a Class ID between stream_id and
 * timestamps to tag this context with the current epoch. */
static void encode_context_packet(uint8_t *buf, size_t *len, uint32_t stream_id) {
    uint16_t epoch = g_current_epoch;
    bool emit_class_id = (epoch != 0);

    pthread_mutex_lock(&g_sdr_config.mutex);
    uint64_t freq = g_sdr_config.center_freq_hz;
    uint32_t rate = g_sdr_config.sample_rate_hz;
    uint32_t bw = g_sdr_config.bandwidth_hz;
    double gain = g_sdr_config.gain_db;
    pthread_mutex_unlock(&g_sdr_config.mutex);

    pthread_mutex_lock(&g_stats.mutex);
    uint64_t underflows = g_stats.underflows;
    uint64_t overflows = g_stats.overflows;
    pthread_mutex_unlock(&g_stats.mutex);

    /* CIF: bandwidth, freq, gain, sample_rate, state_event */
    uint32_t cif = (1 << 29) | (1 << 27) | (1 << 23) | (1 << 21) | (1 << 19);

    /* Pre-encode the fixed-point context fields (descending CIF bit order:
     * bandwidth, freq, gain, sample_rate, state_event). */
    int64_t bw_fixed   = ((int64_t)bw   * (1 << 20));
    int64_t freq_fixed = ((int64_t)freq * (1 << 20));
    int64_t rate_fixed = ((int64_t)rate * (1 << 20));
    int16_t gain_fixed = (int16_t)(gain * 128);

    /* Write fields sequentially. VITA-49 field order:
     *   header, stream_id, [class_id], ts_int, ts_frac, cif, payload */
    size_t off = 0;
    off += 4;  /* header slot, written last */

    uint32_t sid_be = htonl_custom(stream_id);
    memcpy(buf + off, &sid_be, 4);
    off += 4;

    if (emit_class_id) {
        uint32_t w1 = (EPOCH_CLASS_OUI & 0xFFFFFF) << 8;
        uint32_t w2 = ((uint32_t)epoch << 16) | (EPOCH_PACKET_CLASS_CODE & 0xFFFF);
        uint32_t w1_be = htonl_custom(w1);
        uint32_t w2_be = htonl_custom(w2);
        memcpy(buf + off,     &w1_be, 4);
        memcpy(buf + off + 4, &w2_be, 4);
        off += 8;
    }

    uint64_t ts_us = get_timestamp_us();
    uint32_t ts_int = (uint32_t)(ts_us / 1000000);
    uint64_t ts_frac = (ts_us % 1000000) * 1000000ULL;
    uint32_t ts_int_be = htonl_custom(ts_int);
    uint64_t ts_frac_be = htonll(ts_frac);
    memcpy(buf + off, &ts_int_be, 4);  off += 4;
    memcpy(buf + off, &ts_frac_be, 8); off += 8;

    uint32_t cif_be = htonl_custom(cif);
    memcpy(buf + off, &cif_be, 4);
    off += 4;

    /* CIF-ordered payload */
    uint64_t bw_be = htonll(bw_fixed);
    memcpy(buf + off, &bw_be, 8);
    off += 8;

    uint64_t freq_be = htonll(freq_fixed);
    memcpy(buf + off, &freq_be, 8);
    off += 8;

    uint16_t gain_be = htons(gain_fixed);
    memcpy(buf + off, &gain_be, 2);
    off += 2;
    uint16_t zero = 0;
    memcpy(buf + off, &zero, 2);  /* Stage 2 (unused) */
    off += 2;

    uint64_t rate_be = htonll(rate_fixed);
    memcpy(buf + off, &rate_be, 8);
    off += 8;

    uint32_t state_event = (1U << 31);  /* Calibrated Time */
    if (overflows > 0)  state_event |= (1 << 19);
    if (underflows > 0) state_event |= (1 << 18);
    uint32_t state_event_be = htonl_custom(state_event);
    memcpy(buf + off, &state_event_be, 4);
    off += 4;

    /* DEBUG: only log first 5 packets to avoid spam */
    static int debug_count = 0;
    if (debug_count++ < 5) {
        printf("[DEBUG] Encoding context: freq=%.1f MHz, rate=%.1f MSPS, gain=%.1f dB, epoch=%u\n",
               freq / 1e6, rate / 1e6, gain, (unsigned)epoch);
    }

    size_t total_words = off / 4;

    uint32_t header = 0;
    header |= (VRT_PKT_TYPE_CONTEXT & 0xF) << 28;
    if (emit_class_id) header |= (1 << 27);
    header |= (VRT_TSI_UTC & 0x3) << 22;
    header |= (VRT_TSF_PICOSECONDS & 0x3) << 20;
    header |= (total_words & 0xFFFF);
    uint32_t header_be = htonl_custom(header);
    memcpy(buf, &header_be, 4);

    *len = off;
}

/* Encode VITA49 Data packet for the given stream_id (channel). If
 * g_current_epoch != 0, inserts a Class ID (8 bytes) BETWEEN stream_id
 * and timestamps (per VITA-49 spec ordering) to tag this packet with
 * the config epoch. */
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data,
                               size_t num_samples, uint8_t *packet_count, uint32_t stream_id) {
    uint16_t epoch = g_current_epoch;
    bool emit_class_id = (epoch != 0);
    size_t class_id_bytes = emit_class_id ? 8 : 0;
    size_t fixed_prefix_bytes = 4 /*header*/ + 4 /*stream_id*/ + class_id_bytes
                              + 4 /*ts_int*/ + 8 /*ts_frac*/;

    /* Validate buffer won't overflow */
    size_t required_size = fixed_prefix_bytes
                         + (num_samples * 2 * sizeof(int16_t))
                         + sizeof(uint32_t);  /* trailer */

    if (required_size > MAX_PACKET_BUFFER) {
        fprintf(stderr, "ERROR: Packet would exceed buffer size (%zu > %d)\n",
                required_size, MAX_PACKET_BUFFER);
        *len = 0;
        return;
    }

    /* Write fields sequentially; we can't use a struct cast because the
     * optional Class ID changes offsets. VITA-49 field order:
     *   header, stream_id, [class_id], ts_int, ts_frac, payload, trailer */
    size_t off = 0;

    /* Reserve header slot — written last once we know total_words */
    off += 4;

    /* stream_id */
    uint32_t sid_be = htonl_custom(stream_id);
    memcpy(buf + off, &sid_be, 4);
    off += 4;

    /* Optional Class ID (VRT layout: word1 = OUI << 8, word2 =
     * info_class_code << 16 | packet_class_code) */
    if (emit_class_id) {
        uint32_t w1 = (EPOCH_CLASS_OUI & 0xFFFFFF) << 8;
        uint32_t w2 = ((uint32_t)epoch << 16) | (EPOCH_PACKET_CLASS_CODE & 0xFFFF);
        uint32_t w1_be = htonl_custom(w1);
        uint32_t w2_be = htonl_custom(w2);
        memcpy(buf + off,     &w1_be, 4);
        memcpy(buf + off + 4, &w2_be, 4);
        off += 8;
    }

    /* Timestamps */
    uint64_t ts_us = get_timestamp_us();
    uint32_t ts_int = (uint32_t)(ts_us / 1000000);
    uint64_t ts_frac = (ts_us % 1000000) * 1000000ULL;
    uint32_t ts_int_be = htonl_custom(ts_int);
    uint64_t ts_frac_be = htonll(ts_frac);
    memcpy(buf + off, &ts_int_be, 4);  off += 4;
    memcpy(buf + off, &ts_frac_be, 8); off += 8;

    /* Payload (interleaved I,Q big-endian int16) */
    int16_t *payload = (int16_t *)(buf + off);
    for (size_t i = 0; i < num_samples * 2; i++) {
        payload[i] = htons(iq_data[i]);
    }
    size_t payload_bytes = num_samples * 2 * sizeof(int16_t);

    /* Pad payload to 32-bit boundary */
    size_t padding = (4 - (payload_bytes % 4)) % 4;
    if (padding) {
        memset((uint8_t *)payload + payload_bytes, 0, padding);
        payload_bytes += padding;
    }
    off += payload_bytes;

    /* Trailer */
    uint32_t trailer_be = htonl_custom(0x40000000);  /* valid_data = 1 */
    memcpy(buf + off, &trailer_be, 4);
    off += 4;

    /* Total size in 32-bit words */
    size_t total_words = off / 4;

    /* Build and write the header word last */
    uint32_t header = 0;
    header |= (VRT_PKT_TYPE_DATA & 0xF) << 28;
    if (emit_class_id) header |= (1 << 27);  /* class_id_present */
    header |= (1 << 26);  /* trailer_present */
    header |= (VRT_TSI_UTC & 0x3) << 22;
    header |= (VRT_TSF_PICOSECONDS & 0x3) << 20;
    header |= ((*packet_count) & 0xF) << 16;
    header |= (total_words & 0xFFFF);
    uint32_t header_be = htonl_custom(header);
    memcpy(buf, &header_be, 4);

    *len = off;
    *packet_count = (*packet_count + 1) & 0xF;
}

/* Parse VITA49 Context packet and extract configuration.
 * Also extracts the config_epoch from the Class ID if present
 * (Class ID with packet_class_code == EPOCH_PACKET_CLASS_CODE).
 * If no epoch is carried, *epoch_out is left untouched. */
static int parse_context_packet(const uint8_t *buf, size_t len,
                                uint64_t *freq_hz, uint32_t *rate_hz, double *gain_db,
                                uint16_t *epoch_out) {
    if (len < 28) return -1;  /* Minimum context packet size */

    /* Read header to learn about optional fields */
    uint32_t hdr_word = ntohl(*(const uint32_t *)buf);
    bool class_id_present = (hdr_word >> 27) & 0x1;
    const uint8_t *p = buf + 4;

    /* stream_id */
    p += 4;

    /* Optional Class ID */
    if (class_id_present) {
        if ((size_t)(p - buf) + 8 > len) return -1;
        uint32_t w2 = ntohl(*(const uint32_t *)(p + 4));
        uint16_t info_class = (uint16_t)((w2 >> 16) & 0xFFFF);
        uint16_t pkt_class  = (uint16_t)(w2 & 0xFFFF);
        if (pkt_class == EPOCH_PACKET_CLASS_CODE && epoch_out != NULL) {
            *epoch_out = info_class;
        }
        p += 8;
    }

    /* Skip timestamps (12 bytes) */
    if ((size_t)(p - buf) + 12 > len) return -1;
    p += 12;

    /* Read Context Indicator Field (CIF) */
    if ((size_t)(p - buf) + 4 > len) return -1;
    uint32_t cif = ntohl(*(const uint32_t *)p);
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
        uint16_t client_epoch = 0;  /* 0 == client didn't tag this context */

        if (parse_context_packet(buf, recv_len, &new_freq, &new_rate, &new_gain, &client_epoch) == 0) {
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

            /* Epoch handling: a client-provided epoch becomes the new
             * g_current_epoch. If no epoch was tagged but something
             * changed, auto-bump so consumers can still distinguish
             * pre- and post-reconfig samples. */
            if (client_epoch != 0 && client_epoch != g_current_epoch) {
                printf("[Control] Epoch: %u -> %u (client-tagged)\n",
                       (unsigned)g_current_epoch, (unsigned)client_epoch);
                g_current_epoch = client_epoch;
                changed = true;  /* force flush even if no other field changed */
            } else if (changed && client_epoch == 0) {
                uint16_t bumped = g_current_epoch + 1;
                if (bumped == 0) bumped = 1;  /* skip 0 = "untagged" sentinel */
                printf("[Control] Epoch: %u -> %u (auto-bumped on reconfig)\n",
                       (unsigned)g_current_epoch, (unsigned)bumped);
                g_current_epoch = bumped;
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

/* Streaming thread - sends IQ data */
static void *streaming_thread(void *arg) {
    struct iio_context *ctx = (struct iio_context *)arg;
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");

    if (!dev) {
        fprintf(stderr, "[Streaming] ERROR: Device not found\n");
        return NULL;
    }

    /* Configure SDR */
    if (configure_sdr(ctx, dev) < 0) {
        return NULL;
    }

    /* Create buffer */
    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, DEFAULT_BUFFER_SIZE, false);
    if (!rxbuf) {
        fprintf(stderr, "[Streaming] ERROR: Failed to create buffer\n");
        return NULL;
    }

    /* Create UDP socket for data */
    int data_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (data_sock < 0) {
        fprintf(stderr, "[Streaming] ERROR: Failed to create socket\n");
        iio_buffer_destroy(rxbuf);
        return NULL;
    }

    printf("[Streaming] Started\n");

    uint8_t packet_count[2] = {0, 0};  /* Per-channel 4-bit VRT packet counter */
    int packets_since_context = 0;
    uint64_t packets_sent = 0;
    static uint8_t packet_buf[MAX_PACKET_BUFFER];  /* Static to avoid stack overflow with large buffer */
    size_t packet_len;
    uint64_t last_config_check_us = get_timestamp_us();

    while (g_running) {
        /* Periodic cleanup of dead subscribers */
        if (packets_sent % SUBSCRIBER_CLEANUP_INTERVAL == 0 && packets_sent > 0) {
            cleanup_dead_subscribers();
        }

        /* Check for configuration changes every 100ms */
        uint64_t now_us = get_timestamp_us();
        if (now_us - last_config_check_us >= 100000) {  /* 100ms = 100,000 microseconds */
            last_config_check_us = now_us;

            pthread_mutex_lock(&g_sdr_config.mutex);
            bool needs_reconfig = g_sdr_config.config_changed;
            pthread_mutex_unlock(&g_sdr_config.mutex);

            if (needs_reconfig) {
                printf("[Streaming] ========================================\n");
                printf("[Streaming] Configuration change detected - applying to hardware\n");

                /* Destroy current buffer */
                iio_buffer_destroy(rxbuf);
                rxbuf = NULL;

                /* Apply new configuration to SDR hardware */
                if (configure_sdr(ctx, dev) < 0) {
                    fprintf(stderr, "[Streaming] ERROR: Failed to apply new configuration\n");
                    fprintf(stderr, "[Streaming] ERROR: Keeping old configuration\n");

                    /* Try to recreate buffer with old settings */
                    rxbuf = iio_device_create_buffer(dev, DEFAULT_BUFFER_SIZE, false);
                    if (!rxbuf) {
                        fprintf(stderr, "[Streaming] FATAL: Cannot recreate buffer - stopping\n");
                        break;
                    }

                    pthread_mutex_lock(&g_sdr_config.mutex);
                    g_sdr_config.config_changed = false;
                    pthread_mutex_unlock(&g_sdr_config.mutex);
                    continue;
                }

                /* Recreate buffer with new configuration */
                rxbuf = iio_device_create_buffer(dev, DEFAULT_BUFFER_SIZE, false);
                if (!rxbuf) {
                    fprintf(stderr, "[Streaming] FATAL: Failed to recreate buffer - stopping\n");
                    break;
                }

                /* Clear the flag */
                pthread_mutex_lock(&g_sdr_config.mutex);
                g_sdr_config.config_changed = false;
                pthread_mutex_unlock(&g_sdr_config.mutex);

                /* Send Context packet to notify all subscribers of the change.
                 * One context per enabled RX channel so consumers can associate
                 * each data stream with its own config. */
                if (g_sdr_config.enabled_rx_mask & 0x01) {
                    encode_context_packet(packet_buf, &packet_len, RX0_STREAM_ID);
                    broadcast_to_subscribers(data_sock, packet_buf, packet_len);
                    pthread_mutex_lock(&g_stats.mutex);
                    g_stats.contexts_sent++;
                    pthread_mutex_unlock(&g_stats.mutex);
                }
                if (g_sdr_config.enabled_rx_mask & 0x02) {
                    encode_context_packet(packet_buf, &packet_len, RX1_STREAM_ID);
                    broadcast_to_subscribers(data_sock, packet_buf, packet_len);
                    pthread_mutex_lock(&g_stats.mutex);
                    g_stats.contexts_sent++;
                    pthread_mutex_unlock(&g_stats.mutex);
                }

                printf("[Streaming] Configuration applied successfully\n");
                printf("[Streaming] Notified %d subscribers of config change\n", g_subscriber_count);
                printf("[Streaming] ========================================\n");

                packets_since_context = 0;  /* Reset counter */
            }
        }

        /* Refill buffer with improved error handling */
        uint64_t loop_start = get_timestamp_us();
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        if (nbytes < 0) {
            pthread_mutex_lock(&g_stats.mutex);
            g_stats.refill_failures++;
            uint64_t failures = g_stats.refill_failures;
            pthread_mutex_unlock(&g_stats.mutex);

            fprintf(stderr, "[Streaming] ERROR: Buffer refill failed (total failures: %llu)\n",
                    (unsigned long long)failures);

            /* Attempt recovery instead of breaking */
            usleep(1000);  /* 1ms delay */
            continue;
        }

        /* Snapshot the active channel mask once per buffer so the math
         * below stays consistent even if a reconfig races in. */
        uint8_t mask;
        pthread_mutex_lock(&g_sdr_config.mutex);
        mask = g_sdr_config.enabled_rx_mask;
        pthread_mutex_unlock(&g_sdr_config.mutex);
        int active_channels = ((mask & 0x01) ? 1 : 0) + ((mask & 0x02) ? 1 : 0);
        if (active_channels == 0) {
            usleep(1000);
            continue;
        }

        /* Get pointer to data. libiio packs all enabled channels into a
         * single interleaved buffer with stride = active_channels * 4
         * bytes per frame. */
        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, iio_device_get_channel(dev, 0));
        if (!samples) continue;

        size_t num_samples = nbytes / (active_channels * 2 * sizeof(int16_t));  /* IQ pairs per channel */

        /* Timestamp discontinuity detection */
        uint64_t current_ts = get_timestamp_us();

        pthread_mutex_lock(&g_stats.mutex);
        if (g_stats.last_timestamp_us != 0) {
            /* Calculate expected time delta based on sample count */
            uint32_t sample_rate;
            pthread_mutex_lock(&g_sdr_config.mutex);
            sample_rate = g_sdr_config.sample_rate_hz;
            pthread_mutex_unlock(&g_sdr_config.mutex);

            uint64_t expected_delta_us = (num_samples * 1000000ULL) / sample_rate;
            uint64_t actual_delta_us = current_ts - g_stats.last_timestamp_us;
            int64_t delta_error = (int64_t)(actual_delta_us - expected_delta_us);

            if (llabs(delta_error) > 10000) {  /* More than 10ms discrepancy */
                g_stats.timestamp_jumps++;
                fprintf(stderr, "[Streaming] WARNING: Timestamp jump detected: %lld us\n",
                        (long long)delta_error);

                if (delta_error > 0) {
                    g_stats.underflows++;  /* Samples arrived late */
                    fprintf(stderr, "[Streaming] WARNING: Possible UNDERFLOW detected\n");
                } else {
                    g_stats.overflows++;   /* Samples arrived early (shouldn't happen) */
                    fprintf(stderr, "[Streaming] WARNING: Possible OVERFLOW detected\n");
                }
            }
        }
        g_stats.last_timestamp_us = current_ts;
        pthread_mutex_unlock(&g_stats.mutex);

        /* Send context packet periodically (one per enabled channel) */
        if (packets_since_context >= CONTEXT_INTERVAL) {
            if (g_sdr_config.enabled_rx_mask & 0x01) {
                encode_context_packet(packet_buf, &packet_len, RX0_STREAM_ID);
                broadcast_to_subscribers(data_sock, packet_buf, packet_len);
                pthread_mutex_lock(&g_stats.mutex);
                g_stats.contexts_sent++;
                pthread_mutex_unlock(&g_stats.mutex);
            }
            if (g_sdr_config.enabled_rx_mask & 0x02) {
                encode_context_packet(packet_buf, &packet_len, RX1_STREAM_ID);
                broadcast_to_subscribers(data_sock, packet_buf, packet_len);
                pthread_mutex_lock(&g_stats.mutex);
                g_stats.contexts_sent++;
                pthread_mutex_unlock(&g_stats.mutex);
            }
            packets_since_context = 0;
        }

        /* Packetize and send. For single-channel, the buffer layout
         * [I,Q,I,Q,...] feeds straight into encode_data_packet. For
         * dual-channel, [I0,Q0,I1,Q1,...] is deinterleaved per chunk
         * into two staging buffers and emitted as two packets (same
         * destination UDP port, distinct stream_ids). */
        static int16_t ch0_chunk[MAX_PACKET_BUFFER / 2];
        static int16_t ch1_chunk[MAX_PACKET_BUFFER / 2];

        for (size_t offset = 0; offset < num_samples; offset += g_samples_per_packet) {
            size_t chunk_size = (offset + g_samples_per_packet > num_samples) ?
                               (num_samples - offset) : g_samples_per_packet;

            if (active_channels == 1) {
                uint32_t sid = (mask & 0x01) ? RX0_STREAM_ID : RX1_STREAM_ID;
                int slot = (mask & 0x01) ? 0 : 1;
                encode_data_packet(packet_buf, &packet_len, samples + offset * 2,
                                   chunk_size, &packet_count[slot], sid);
                broadcast_to_subscribers(data_sock, packet_buf, packet_len);

                pthread_mutex_lock(&g_stats.mutex);
                g_stats.packets_sent++;
                g_stats.bytes_sent += packet_len;
                pthread_mutex_unlock(&g_stats.mutex);
                packets_since_context++;
                packets_sent++;
            } else {
                /* Deinterleave one chunk: 4 int16s per frame -> 2 channels x 2 int16s */
                for (size_t i = 0; i < chunk_size; i++) {
                    size_t f = (offset + i) * 4;
                    ch0_chunk[i * 2]     = samples[f + 0];
                    ch0_chunk[i * 2 + 1] = samples[f + 1];
                    ch1_chunk[i * 2]     = samples[f + 2];
                    ch1_chunk[i * 2 + 1] = samples[f + 3];
                }

                encode_data_packet(packet_buf, &packet_len, ch0_chunk,
                                   chunk_size, &packet_count[0], RX0_STREAM_ID);
                broadcast_to_subscribers(data_sock, packet_buf, packet_len);
                pthread_mutex_lock(&g_stats.mutex);
                g_stats.packets_sent++;
                g_stats.bytes_sent += packet_len;
                pthread_mutex_unlock(&g_stats.mutex);

                encode_data_packet(packet_buf, &packet_len, ch1_chunk,
                                   chunk_size, &packet_count[1], RX1_STREAM_ID);
                broadcast_to_subscribers(data_sock, packet_buf, packet_len);
                pthread_mutex_lock(&g_stats.mutex);
                g_stats.packets_sent++;
                g_stats.bytes_sent += packet_len;
                pthread_mutex_unlock(&g_stats.mutex);

                packets_since_context += 2;
                packets_sent += 2;
            }
        }

        /* Loop timing measurements */
        uint64_t loop_time = get_timestamp_us() - loop_start;
        pthread_mutex_lock(&g_stats.mutex);
        if (loop_time < g_stats.min_loop_time_us || g_stats.min_loop_time_us == 0) {
            g_stats.min_loop_time_us = loop_time;
        }
        if (loop_time > g_stats.max_loop_time_us) {
            g_stats.max_loop_time_us = loop_time;
        }
        g_stats.total_loop_time_us += loop_time;
        g_stats.loop_iterations++;
        pthread_mutex_unlock(&g_stats.mutex);
    }

    printf("[Streaming] Stopped\n");

    close(data_sock);
    iio_buffer_destroy(rxbuf);
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

    /* RX1 gain (phy voltage1, is_output=false). Same gain as RX0 today;
     * a future revision can split per-channel gain into its own field. */
    if (g_sdr_config.enabled_rx_mask & 0x02) {
        struct iio_channel *phy_rx1 = iio_device_find_channel(phy, "voltage1", false);
        if (phy_rx1) {
            char buf[32];
            snprintf(buf, sizeof(buf), "%.1f", g_sdr_config.gain_db);
            iio_channel_attr_write(phy_rx1, "hardwaregain", buf);
            iio_channel_attr_write(phy_rx1, "gain_control_mode", "manual");
        }
    }

    /* Enable channels per mask. RX0 = voltage0/voltage1, RX1 = voltage2/voltage3. */
    struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
    struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);
    struct iio_channel *rx1_i = iio_device_find_channel(dev, "voltage2", false);
    struct iio_channel *rx1_q = iio_device_find_channel(dev, "voltage3", false);

    if (g_sdr_config.enabled_rx_mask & 0x01) {
        if (rx0_i) iio_channel_enable(rx0_i);
        if (rx0_q) iio_channel_enable(rx0_q);
    } else {
        if (rx0_i) iio_channel_disable(rx0_i);
        if (rx0_q) iio_channel_disable(rx0_q);
    }
    if (g_sdr_config.enabled_rx_mask & 0x02) {
        if (rx1_i) iio_channel_enable(rx1_i);
        if (rx1_q) iio_channel_enable(rx1_q);
    } else {
        if (rx1_i) iio_channel_disable(rx1_i);
        if (rx1_q) iio_channel_disable(rx1_q);
    }

    printf("[Config] Configured: %.1f MHz, %.1f MSPS, %.1f dB, rx_mask=0x%02X\n",
           g_sdr_config.center_freq_hz / 1e6,
           g_sdr_config.sample_rate_hz / 1e6,
           g_sdr_config.gain_db,
           g_sdr_config.enabled_rx_mask);

    pthread_mutex_unlock(&g_sdr_config.mutex);

    return 0;
}

/* Configure AD9361 TX side. Opens cf-ad9361-dds-core-lpc and writes
 * TX LO + per-channel gain via ad9361-phy. Sample rate is shared with
 * RX (one BBPLL feeds both ADC and DAC) and is NOT written here. */
static int configure_tx(struct iio_context *ctx, struct iio_device **tx_dev_out) {
    struct iio_device *tx_dev = iio_context_find_device(ctx, "cf-ad9361-dds-core-lpc");
    if (!tx_dev) {
        fprintf(stderr, "[TX Config] ERROR: cf-ad9361-dds-core-lpc not found\n");
        return -1;
    }
    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    if (!phy) {
        fprintf(stderr, "[TX Config] ERROR: ad9361-phy not found\n");
        return -1;
    }

    pthread_mutex_lock(&g_tx_config.mutex);

    /* TX LO via phy altvoltage1 */
    struct iio_channel *tx_lo = iio_device_find_channel(phy, "altvoltage1", true);
    if (tx_lo) {
        char buf[32];
        snprintf(buf, sizeof(buf), "%llu", (unsigned long long)g_tx_config.center_freq_hz);
        iio_channel_attr_write(tx_lo, "frequency", buf);
    } else {
        fprintf(stderr, "[TX Config] WARNING: altvoltage1 (TX LO) not found\n");
    }

    /* TX0 gain: phy voltage0 with is_output=true.
     * TX1 gain: phy voltage1 with is_output=true. */
    if (g_tx_config.enabled_tx_mask & 0x01) {
        struct iio_channel *tx0_phy = iio_device_find_channel(phy, "voltage0", true);
        if (tx0_phy) {
            char buf[32];
            snprintf(buf, sizeof(buf), "%.2f", g_tx_config.gain_db);
            iio_channel_attr_write(tx0_phy, "hardwaregain", buf);
        }
    }
    if (g_tx_config.enabled_tx_mask & 0x02) {
        struct iio_channel *tx1_phy = iio_device_find_channel(phy, "voltage1", true);
        if (tx1_phy) {
            char buf[32];
            snprintf(buf, sizeof(buf), "%.2f", g_tx_config.gain_db);
            iio_channel_attr_write(tx1_phy, "hardwaregain", buf);
        }
    }

    /* Enable DAC channels on the dds-core-lpc device. TX0 = voltage0/voltage1,
     * TX1 = voltage2/voltage3. All with is_output=true. Disable unused ones. */
    struct iio_channel *tx0_i = iio_device_find_channel(tx_dev, "voltage0", true);
    struct iio_channel *tx0_q = iio_device_find_channel(tx_dev, "voltage1", true);
    struct iio_channel *tx1_i = iio_device_find_channel(tx_dev, "voltage2", true);
    struct iio_channel *tx1_q = iio_device_find_channel(tx_dev, "voltage3", true);

    if (g_tx_config.enabled_tx_mask & 0x01) {
        if (tx0_i) iio_channel_enable(tx0_i);
        if (tx0_q) iio_channel_enable(tx0_q);
    } else {
        if (tx0_i) iio_channel_disable(tx0_i);
        if (tx0_q) iio_channel_disable(tx0_q);
    }
    if (g_tx_config.enabled_tx_mask & 0x02) {
        if (tx1_i) iio_channel_enable(tx1_i);
        if (tx1_q) iio_channel_enable(tx1_q);
    } else {
        if (tx1_i) iio_channel_disable(tx1_i);
        if (tx1_q) iio_channel_disable(tx1_q);
    }

    printf("[TX Config] Configured: %.1f MHz, %.2f dB, tx_mask=0x%02X\n",
           g_tx_config.center_freq_hz / 1e6,
           g_tx_config.gain_db,
           g_tx_config.enabled_tx_mask);

    pthread_mutex_unlock(&g_tx_config.mutex);

    *tx_dev_out = tx_dev;
    return 0;
}

/* TX thread: listen on UDP TX_DATA_PORT for VITA-49 IF Data packets,
 * decode, and push to the AD9361 TX DAC via libiio. v1 design: one
 * inbound UDP packet -> one iio_buffer_push (synchronous, no ring
 * buffer). Per-packet payload is padded with zeros to TX buffer size,
 * or truncated if larger. */
static void *tx_thread(void *arg) {
    struct iio_context *ctx = (struct iio_context *)arg;
    struct iio_device *tx_dev = NULL;

    if (configure_tx(ctx, &tx_dev) < 0) {
        fprintf(stderr, "[TX] Hardware configuration failed; thread exiting\n");
        return NULL;
    }

    struct iio_buffer *txbuf = iio_device_create_buffer(tx_dev, DEFAULT_TX_BUFFER_SIZE, false);
    if (!txbuf) {
        fprintf(stderr, "[TX] iio_device_create_buffer failed\n");
        return NULL;
    }

    int tx_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (tx_sock < 0) {
        fprintf(stderr, "[TX] Failed to create UDP socket\n");
        iio_buffer_destroy(txbuf);
        return NULL;
    }
    int rcvbuf = 1024 * 1024;
    setsockopt(tx_sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    struct sockaddr_in tx_addr = {0};
    tx_addr.sin_family = AF_INET;
    tx_addr.sin_addr.s_addr = INADDR_ANY;
    tx_addr.sin_port = htons(TX_DATA_PORT);
    if (bind(tx_sock, (struct sockaddr *)&tx_addr, sizeof(tx_addr)) < 0) {
        fprintf(stderr, "[TX] Failed to bind port %d\n", TX_DATA_PORT);
        close(tx_sock);
        iio_buffer_destroy(txbuf);
        return NULL;
    }
    struct timeval timeout = { .tv_sec = 1, .tv_usec = 0 };
    setsockopt(tx_sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    printf("[TX] Listening for IF Data packets on port %d (push %d samples/buffer)\n",
           TX_DATA_PORT, DEFAULT_TX_BUFFER_SIZE);

    static uint8_t recv_buf[MAX_PACKET_BUFFER];

    while (g_running) {
        ssize_t recv_len = recvfrom(tx_sock, recv_buf, sizeof(recv_buf), 0, NULL, NULL);
        if (recv_len < 0) continue;  /* timeout -> re-check g_running */
        if (recv_len < 20) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.packets_dropped_decode++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            continue;
        }

        pthread_mutex_lock(&g_tx_stats.mutex);
        g_tx_stats.packets_received++;
        pthread_mutex_unlock(&g_tx_stats.mutex);

        uint32_t hdr_word = ntohl(*(uint32_t *)recv_buf);
        uint32_t sid = ntohl(*(uint32_t *)(recv_buf + 4));
        uint8_t pkt_type = (hdr_word >> 28) & 0xF;
        bool has_trailer = (hdr_word >> 26) & 0x1;
        bool class_id_present = (hdr_word >> 27) & 0x1;

        if (pkt_type != VRT_PKT_TYPE_DATA) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.packets_dropped_decode++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            continue;
        }

        uint8_t sid_high = (sid >> 24) & 0xFF;
        if (sid_high != TX_STREAM_ID_HIGH_BYTE) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.packets_dropped_wrong_stream++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            continue;
        }

        uint8_t channel = sid & 0xFF;
        if (channel > 1 || !(g_tx_config.enabled_tx_mask & (1 << channel))) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.packets_dropped_disabled_ch++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            continue;
        }

        /* Skip header(4) + stream_id(4) + optional class_id(8) + timestamp(12).
         * Our wire format always uses TSI=UTC + TSF=picoseconds. */
        size_t payload_offset = 4 + 4 + (class_id_present ? 8 : 0) + 12;
        size_t trailer_size = has_trailer ? 4 : 0;
        if ((size_t)recv_len <= payload_offset + trailer_size) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.packets_dropped_decode++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            continue;
        }
        size_t payload_bytes = (size_t)recv_len - payload_offset - trailer_size;
        if ((payload_bytes % 4) != 0) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.packets_dropped_decode++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            continue;
        }

        size_t payload_int16s = payload_bytes / sizeof(int16_t);  /* I/Q interleaved */
        int16_t *be_payload = (int16_t *)(recv_buf + payload_offset);

        /* Locate the I channel of the target TX channel in the iio buffer.
         * For single-channel TX, layout is contiguous [I,Q,I,Q,...] so we
         * write straight in with no stride math. */
        struct iio_channel *tx_i_ch = iio_device_find_channel(
            tx_dev, channel == 0 ? "voltage0" : "voltage2", true);
        if (!tx_i_ch) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.push_failures++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            continue;
        }
        int16_t *tx_data = (int16_t *)iio_buffer_first(txbuf, tx_i_ch);
        if (!tx_data) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.push_failures++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            continue;
        }

        size_t buf_capacity_int16 = DEFAULT_TX_BUFFER_SIZE * 2;  /* I+Q */
        size_t to_copy = payload_int16s < buf_capacity_int16 ? payload_int16s : buf_capacity_int16;

        for (size_t i = 0; i < to_copy; i++) {
            tx_data[i] = (int16_t)ntohs(be_payload[i]);
        }
        /* Pad remainder with silence so the DAC doesn't replay stale samples. */
        for (size_t i = to_copy; i < buf_capacity_int16; i++) {
            tx_data[i] = 0;
        }

        ssize_t pushed = iio_buffer_push(txbuf);
        if (pushed < 0) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.push_failures++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
        } else {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.packets_transmitted++;
            g_tx_stats.bytes_pushed += pushed;
            pthread_mutex_unlock(&g_tx_stats.mutex);
        }
    }

    close(tx_sock);
    iio_buffer_destroy(txbuf);
    printf("[TX] Stopped\n");
    return NULL;
}

/* TX replay thread: stream raw int16 I/Q samples from a file to the
 * DAC. Mutually exclusive with the UDP tx_thread; main() picks one. */
static void *tx_replay_thread(void *arg) {
    struct iio_context *ctx = (struct iio_context *)arg;
    struct iio_device *tx_dev = NULL;

    if (configure_tx(ctx, &tx_dev) < 0) {
        fprintf(stderr, "[TX Replay] Hardware configuration failed\n");
        return NULL;
    }

    struct iio_buffer *txbuf = iio_device_create_buffer(tx_dev, DEFAULT_TX_BUFFER_SIZE, false);
    if (!txbuf) {
        fprintf(stderr, "[TX Replay] iio_device_create_buffer failed\n");
        return NULL;
    }

    int fd = open(g_tx_replay_path, O_RDONLY);
    if (fd < 0) {
        fprintf(stderr, "[TX Replay] Failed to open '%s': %s\n",
                g_tx_replay_path, strerror(errno));
        iio_buffer_destroy(txbuf);
        return NULL;
    }

    /* Exactly one TX channel is enabled (CLI rejects both for replay v1). */
    int channel = (g_tx_config.enabled_tx_mask & 0x01) ? 0 : 1;
    struct iio_channel *tx_i_ch = iio_device_find_channel(
        tx_dev, channel == 0 ? "voltage0" : "voltage2", true);
    if (!tx_i_ch) {
        fprintf(stderr, "[TX Replay] TX I channel not found\n");
        close(fd);
        iio_buffer_destroy(txbuf);
        return NULL;
    }

    size_t buf_bytes = DEFAULT_TX_BUFFER_SIZE * 2 * sizeof(int16_t);  /* I+Q interleaved */
    printf("[TX Replay] Streaming '%s' -> TX%d, %zu bytes/push%s\n",
           g_tx_replay_path, channel, buf_bytes,
           g_tx_replay_loop ? " (looping)" : "");

    uint64_t buffers_pushed = 0;

    while (g_running) {
        int16_t *tx_data = (int16_t *)iio_buffer_first(txbuf, tx_i_ch);
        if (!tx_data) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.push_failures++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            break;
        }

        /* Fill the iio buffer from disk. Handles short reads via a loop
         * so a single buffer push always contains a full block. */
        size_t filled = 0;
        bool hit_eof = false;
        while (filled < buf_bytes) {
            ssize_t n = read(fd, (uint8_t *)tx_data + filled, buf_bytes - filled);
            if (n < 0) {
                if (errno == EINTR) continue;
                fprintf(stderr, "[TX Replay] read() error: %s\n", strerror(errno));
                close(fd);
                iio_buffer_destroy(txbuf);
                return NULL;
            }
            if (n == 0) {
                hit_eof = true;
                break;
            }
            filled += n;
        }

        if (hit_eof) {
            if (filled == 0) {
                /* Clean EOF on a buffer boundary */
                if (!g_tx_replay_loop) {
                    printf("[TX Replay] EOF (no --tx-loop), exiting after %llu buffers\n",
                           (unsigned long long)buffers_pushed);
                    break;
                }
                if (lseek(fd, 0, SEEK_SET) < 0) {
                    fprintf(stderr, "[TX Replay] lseek failed: %s\n", strerror(errno));
                    break;
                }
                continue;  /* re-read from start */
            } else {
                /* Partial buffer at EOF: pad with silence and push, then
                 * either loop or exit on next iteration. */
                memset((uint8_t *)tx_data + filled, 0, buf_bytes - filled);
                if (g_tx_replay_loop) {
                    if (lseek(fd, 0, SEEK_SET) < 0) {
                        fprintf(stderr, "[TX Replay] lseek failed: %s\n", strerror(errno));
                        break;
                    }
                }
            }
        }

        ssize_t pushed = iio_buffer_push(txbuf);
        if (pushed < 0) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.push_failures++;
            pthread_mutex_unlock(&g_tx_stats.mutex);
        } else {
            buffers_pushed++;
            pthread_mutex_lock(&g_tx_stats.mutex);
            g_tx_stats.packets_transmitted++;
            g_tx_stats.bytes_pushed += pushed;
            pthread_mutex_unlock(&g_tx_stats.mutex);
        }

        if (hit_eof && !g_tx_replay_loop) {
            /* We just pushed the final partial buffer; exit. */
            printf("[TX Replay] EOF reached, pushed final partial buffer (%zu bytes), exiting\n",
                   filled);
            break;
        }
    }

    close(fd);
    iio_buffer_destroy(txbuf);
    printf("[TX Replay] Stopped (pushed %llu buffers)\n", (unsigned long long)buffers_pushed);
    return NULL;
}

/* Main */
int main(int argc, char **argv) {
    /* Parse command-line arguments for MTU */
    size_t mtu = MTU_STANDARD;  /* Default to standard Ethernet */
    bool use_jumbo = false;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--jumbo") == 0) {
            use_jumbo = true;
            mtu = MTU_JUMBO;
        } else if (strcmp(argv[i], "--mtu") == 0 && i + 1 < argc) {
            mtu = (size_t)atoi(argv[++i]);
        } else if (strcmp(argv[i], "--rx-channels") == 0 && i + 1 < argc) {
            /* Accepts "0", "1", or "0,1". Bit 0=RX0, bit 1=RX1. */
            const char *spec = argv[++i];
            uint8_t mask = 0;
            for (const char *p = spec; *p; p++) {
                if (*p == '0') mask |= 0x01;
                else if (*p == '1') mask |= 0x02;
            }
            if (mask == 0) {
                fprintf(stderr, "ERROR: --rx-channels must include 0 or 1 (got '%s')\n", spec);
                return 1;
            }
            g_sdr_config.enabled_rx_mask = mask;
        } else if (strcmp(argv[i], "--tx-channels") == 0 && i + 1 < argc) {
            /* v1 supports a single TX channel at a time: "0" or "1". */
            const char *spec = argv[++i];
            uint8_t mask = 0;
            for (const char *p = spec; *p; p++) {
                if (*p == '0') mask |= 0x01;
                else if (*p == '1') mask |= 0x02;
            }
            if (mask == 0) {
                fprintf(stderr, "ERROR: --tx-channels must include 0 or 1 (got '%s')\n", spec);
                return 1;
            }
            if (mask == 0x03) {
                fprintf(stderr, "ERROR: dual TX (both channels) not supported in v1; pick one\n");
                return 1;
            }
            g_tx_config.enabled_tx_mask = mask;
        } else if (strcmp(argv[i], "--tx-freq") == 0 && i + 1 < argc) {
            g_tx_config.center_freq_hz = strtoull(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--tx-gain") == 0 && i + 1 < argc) {
            g_tx_config.gain_db = atof(argv[++i]);
        } else if (strcmp(argv[i], "--tx-replay") == 0 && i + 1 < argc) {
            g_tx_replay_path = argv[++i];
        } else if (strcmp(argv[i], "--tx-loop") == 0) {
            g_tx_replay_loop = true;
        } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            printf("Usage: %s [options]\n", argv[0]);
            printf("Options:\n");
            printf("  --jumbo                  Use jumbo frames (MTU 9000)\n");
            printf("  --mtu <size>             Set custom MTU size in bytes\n");
            printf("  --rx-channels <list>     RX channels: 0, 1, or 0,1 (default: 0)\n");
            printf("                           Emits on port %d with stream_id 0x0100000N.\n", DATA_PORT);
            printf("  --tx-channels <list>     TX channel: 0 or 1 (default: TX disabled)\n");
            printf("                           v1: one channel at a time.\n");
            printf("  --tx-freq <hz>           TX center frequency (default: %llu)\n",
                   (unsigned long long)DEFAULT_FREQ_HZ);
            printf("  --tx-gain <db>           TX gain in dB; AD9361 TX is attenuation,\n");
            printf("                           range -89.75..0 (default: %.1f)\n", DEFAULT_TX_GAIN_DB);
            printf("\nTX source (pick one when TX is enabled):\n");
            printf("  (default)                Listen on UDP port %d for VITA-49 IF Data\n", TX_DATA_PORT);
            printf("                           packets with stream_id 0x0200000N\n");
            printf("  --tx-replay <path>       Stream raw int16 I/Q (interleaved, little-endian,\n");
            printf("                           no header) from a file instead of UDP\n");
            printf("  --tx-loop                With --tx-replay, restart from the beginning on EOF\n");
            printf("\n  --help, -h               Show this help message\n");
            printf("\nExamples:\n");
            printf("  %s                                          # Single RX0 (default)\n", argv[0]);
            printf("  %s --rx-channels 0,1                        # Dual RX on port %d\n", argv[0], DATA_PORT);
            printf("  %s --tx-channels 0 --tx-freq 915000000      # TX0 from UDP at 915 MHz\n", argv[0]);
            printf("  %s --tx-channels 0 --tx-replay tone.iq16le --tx-loop  # Replay loop\n", argv[0]);
            return 0;
        }
    }

    /* Replay-mode validation. Replay requires exactly one TX channel and
     * is mutually exclusive with the UDP TX path (we just don't spawn the
     * UDP recv when replay is set). */
    if (g_tx_replay_path != NULL && g_tx_config.enabled_tx_mask == 0) {
        fprintf(stderr, "ERROR: --tx-replay requires --tx-channels to be set\n");
        return 1;
    }

    /* Calculate optimal packet size based on MTU */
    g_samples_per_packet = calculate_optimal_samples_per_packet(mtu);

    /* Calculate actual packet sizes for verification */
    size_t packet_payload = g_samples_per_packet * 2 * sizeof(int16_t);
    size_t total_vita49_packet = packet_payload + VITA49_OVERHEAD;
    size_t total_udp_datagram = total_vita49_packet + IP_UDP_OVERHEAD;

    printf("========================================\n");
    printf("VITA49 Standalone Streamer for Pluto\n");
    printf("========================================\n");
    printf("MTU: %zu bytes%s\n", mtu, use_jumbo ? " (Jumbo frames)" : "");
    printf("Samples per packet: %zu\n", g_samples_per_packet);
    printf("VITA49 packet size: %zu bytes\n", total_vita49_packet);
    printf("UDP datagram size: %zu bytes\n", total_udp_datagram);

    if (total_udp_datagram > mtu) {
        fprintf(stderr, "WARNING: Packet size exceeds MTU! Will fragment.\n");
    } else {
        double efficiency = 100.0 * total_udp_datagram / mtu;
        printf("✓ Packet fits in MTU (efficiency: %.1f%%)\n", efficiency);
    }
    printf("\n");

    /* Register signal handler */
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Initialize statistics mutex */
    pthread_mutex_init(&g_stats.mutex, NULL);
    pthread_mutex_init(&g_tx_stats.mutex, NULL);

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

    printf("Control port: %d\n", CONTROL_PORT);
    printf("Data port: %d\n\n", DATA_PORT);

    /* Start threads */
    pthread_t control_tid, streaming_tid, tx_tid;
    bool tx_thread_started = false;

    pthread_create(&control_tid, NULL, control_thread, &control_sock);
    pthread_create(&streaming_tid, NULL, streaming_thread, ctx);

    if (g_tx_config.enabled_tx_mask != 0) {
        void *(*entry)(void *) = (g_tx_replay_path != NULL) ? tx_replay_thread : tx_thread;
        if (pthread_create(&tx_tid, NULL, entry, ctx) == 0) {
            tx_thread_started = true;
            printf("TX thread started (tx_mask=0x%02X, source=%s)\n",
                   g_tx_config.enabled_tx_mask,
                   g_tx_replay_path != NULL ? "file replay" : "UDP");
        } else {
            fprintf(stderr, "ERROR: Failed to start TX thread\n");
        }
    }

    /* Monitor */
    while (g_running) {
        sleep(5);

        pthread_mutex_lock(&g_stats.mutex);
        uint64_t packets = g_stats.packets_sent;
        uint64_t bytes = g_stats.bytes_sent;
        uint32_t contexts = g_stats.contexts_sent;
        uint64_t underflows = g_stats.underflows;
        uint64_t overflows = g_stats.overflows;
        uint64_t refill_fails = g_stats.refill_failures;
        uint64_t ts_jumps = g_stats.timestamp_jumps;
        uint64_t min_loop = g_stats.min_loop_time_us;
        uint64_t max_loop = g_stats.max_loop_time_us;
        double avg_loop = g_stats.loop_iterations > 0 ?
            (double)g_stats.total_loop_time_us / g_stats.loop_iterations : 0;
        pthread_mutex_unlock(&g_stats.mutex);

        printf("[Stats] Packets: %llu, Bytes: %llu MB, Contexts: %u, Subs: %d\n",
               (unsigned long long)packets,
               (unsigned long long)(bytes / 1048576),
               contexts, g_subscriber_count);

        printf("[Health] Underflows: %llu, Overflows: %llu, Refill Fails: %llu, TS Jumps: %llu\n",
               (unsigned long long)underflows,
               (unsigned long long)overflows,
               (unsigned long long)refill_fails,
               (unsigned long long)ts_jumps);

        printf("[Timing] Loop: avg=%.1f us, min=%llu us, max=%llu us\n",
               avg_loop,
               (unsigned long long)min_loop,
               (unsigned long long)max_loop);

        if (g_tx_config.enabled_tx_mask != 0) {
            pthread_mutex_lock(&g_tx_stats.mutex);
            uint64_t tx_rx = g_tx_stats.packets_received;
            uint64_t tx_tx = g_tx_stats.packets_transmitted;
            uint64_t tx_ws = g_tx_stats.packets_dropped_wrong_stream;
            uint64_t tx_dc = g_tx_stats.packets_dropped_disabled_ch;
            uint64_t tx_dec = g_tx_stats.packets_dropped_decode;
            uint64_t tx_pf = g_tx_stats.push_failures;
            uint64_t tx_bytes = g_tx_stats.bytes_pushed;
            pthread_mutex_unlock(&g_tx_stats.mutex);
            printf("[TX] Recv: %llu, Tx: %llu, Drops(wrong_sid=%llu, disabled_ch=%llu, decode=%llu), PushFails: %llu, Bytes: %llu\n",
                   (unsigned long long)tx_rx, (unsigned long long)tx_tx,
                   (unsigned long long)tx_ws, (unsigned long long)tx_dc,
                   (unsigned long long)tx_dec, (unsigned long long)tx_pf,
                   (unsigned long long)tx_bytes);
        }

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
    pthread_join(streaming_tid, NULL);
    if (tx_thread_started) {
        pthread_join(tx_tid, NULL);
    }

    close(control_sock);
    iio_context_destroy(ctx);
    pthread_mutex_destroy(&g_stats.mutex);
    pthread_mutex_destroy(&g_tx_stats.mutex);

    printf("\n✓ Stopped\n");
    return 0;
}
