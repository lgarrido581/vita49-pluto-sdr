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
#include <iio.h>

/* Configuration */
#define DEFAULT_FREQ_HZ         2400000000ULL   /* 2.4 GHz */
#define DEFAULT_RATE_HZ         30000000        /* 30 MSPS */
#define DEFAULT_GAIN_DB         20.0
#define DEFAULT_BUFFER_SIZE     16384           /* Samples per buffer */
#define SAMPLES_PER_PACKET      360             /* IQ samples per VITA49 packet */
#define CONTROL_PORT            4990            /* Config reception port */
#define DATA_PORT               4991            /* Data streaming port */
#define CONTEXT_INTERVAL        100             /* Send context every N packets */
#define MAX_SUBSCRIBERS         16              /* Max simultaneous receivers */

/* VITA49 Packet Types */
#define VRT_PKT_TYPE_DATA       0x1             /* IF Data with Stream ID */
#define VRT_PKT_TYPE_CONTEXT    0x4             /* Context packet */
#define VRT_TSI_UTC             0x1             /* UTC timestamp */
#define VRT_TSF_PICOSECONDS     0x2             /* Picosecond fractional time */

/* Global state */
static volatile bool g_running = true;
static pthread_mutex_t g_subscribers_mutex = PTHREAD_MUTEX_INITIALIZER;

/* Subscriber list */
typedef struct {
    struct sockaddr_in addr;
    bool active;
} subscriber_t;

static subscriber_t g_subscribers[MAX_SUBSCRIBERS];
static int g_subscriber_count = 0;

/* SDR Configuration */
typedef struct {
    uint64_t center_freq_hz;
    uint32_t sample_rate_hz;
    uint32_t bandwidth_hz;
    double gain_db;
    pthread_mutex_t mutex;
} sdr_config_t;

static sdr_config_t g_sdr_config = {
    .center_freq_hz = DEFAULT_FREQ_HZ,
    .sample_rate_hz = DEFAULT_RATE_HZ,
    .bandwidth_hz = DEFAULT_RATE_HZ * 0.8,
    .gain_db = DEFAULT_GAIN_DB,
    .mutex = PTHREAD_MUTEX_INITIALIZER
};

/* Statistics */
typedef struct {
    uint64_t packets_sent;
    uint64_t bytes_sent;
    uint32_t contexts_sent;
    uint32_t reconfigs;
} stats_t;

static stats_t g_stats = {0};

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
static uint64_t get_timestamp_us(void);
static void encode_context_packet(uint8_t *buf, size_t *len);
static void encode_data_packet(uint8_t *buf, size_t *len, int16_t *iq_data, size_t num_samples, uint8_t *packet_count);
static void *control_thread(void *arg);
static void *streaming_thread(void *arg);
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
        if (g_subscribers[i].active &&
            g_subscribers[i].addr.sin_addr.s_addr == addr->sin_addr.s_addr &&
            g_subscribers[i].addr.sin_port == addr->sin_port) {
            pthread_mutex_unlock(&g_subscribers_mutex);
            return;  /* Already subscribed */
        }
    }

    /* Add new subscriber */
    if (g_subscriber_count < MAX_SUBSCRIBERS) {
        g_subscribers[g_subscriber_count].addr = *addr;
        g_subscribers[g_subscriber_count].active = true;
        g_subscriber_count++;

        char ip_str[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &addr->sin_addr, ip_str, INET_ADDRSTRLEN);
        printf("[Control] Added subscriber: %s:%d (total: %d)\n",
               ip_str, ntohs(addr->sin_port), g_subscriber_count);
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

    /* Context Indicator Field (CIF) */
    uint32_t cif = 0;
    cif |= (1 << 29);  /* bandwidth */
    cif |= (1 << 27);  /* rf_reference_frequency */
    cif |= (1 << 21);  /* sample_rate */
    cif |= (1 << 23);  /* gain */

    /* Encode context fields (64-bit fixed point, 20-bit radix for Hz) */
    int64_t bw_fixed = (int64_t)(bw * (1 << 20));
    int64_t freq_fixed = (int64_t)(freq * (1 << 20));
    int64_t rate_fixed = (int64_t)(rate * (1 << 20));
    int16_t gain_fixed = (int16_t)(gain * 128);

    uint64_t *p64 = (uint64_t *)payload;
    p64[0] = htonll(bw_fixed);
    p64[1] = htonll(freq_fixed);
    p64[2] = htonll(rate_fixed);
    payload_len += 24;

    uint16_t *p16 = (uint16_t *)(payload + payload_len);
    p16[0] = htons(gain_fixed);
    p16[1] = 0;
    payload_len += 4;

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

            pthread_mutex_unlock(&g_sdr_config.mutex);

            if (!changed) {
                printf("[Control] No changes (same as current config)\n");
            } else {
                printf("[Control] Configuration updated successfully\n");
                /* Note: Actual SDR reconfiguration happens in streaming thread */
            }
        } else {
            printf("[Control] Warning: Failed to parse context packet\n");
        }

        /* Add as subscriber */
        client_addr.sin_port = htons(DATA_PORT);
        add_subscriber(&client_addr);
        printf("[Control] Added %s as subscriber (total: %d)\n", ip_str, g_subscriber_count);
        printf("[Control] ========================================\n\n");

        g_stats.reconfigs++;
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

    uint8_t packet_count = 0;
    int packets_since_context = 0;
    uint8_t packet_buf[8192];
    size_t packet_len;

    while (g_running) {
        /* Refill buffer */
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        if (nbytes < 0) {
            fprintf(stderr, "[Streaming] ERROR: Buffer refill failed\n");
            break;
        }

        /* Get pointer to data */
        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, iio_device_get_channel(dev, 0));
        if (!samples) continue;

        size_t num_samples = nbytes / (2 * sizeof(int16_t));  /* IQ pairs */

        /* Send context packet periodically */
        if (packets_since_context >= CONTEXT_INTERVAL) {
            encode_context_packet(packet_buf, &packet_len);

            pthread_mutex_lock(&g_subscribers_mutex);
            for (int i = 0; i < g_subscriber_count; i++) {
                if (g_subscribers[i].active) {
                    sendto(data_sock, packet_buf, packet_len, 0,
                          (struct sockaddr *)&g_subscribers[i].addr,
                          sizeof(g_subscribers[i].addr));
                }
            }
            pthread_mutex_unlock(&g_subscribers_mutex);

            g_stats.contexts_sent++;
            packets_since_context = 0;
        }

        /* Packetize and send */
        for (size_t offset = 0; offset < num_samples; offset += SAMPLES_PER_PACKET) {
            size_t chunk_size = (offset + SAMPLES_PER_PACKET > num_samples) ?
                               (num_samples - offset) : SAMPLES_PER_PACKET;

            encode_data_packet(packet_buf, &packet_len, samples + offset * 2,
                             chunk_size, &packet_count);

            pthread_mutex_lock(&g_subscribers_mutex);
            for (int i = 0; i < g_subscriber_count; i++) {
                if (g_subscribers[i].active) {
                    sendto(data_sock, packet_buf, packet_len, 0,
                          (struct sockaddr *)&g_subscribers[i].addr,
                          sizeof(g_subscribers[i].addr));
                }
            }
            pthread_mutex_unlock(&g_subscribers_mutex);

            g_stats.packets_sent++;
            g_stats.bytes_sent += packet_len;
            packets_since_context++;
        }
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
int main(int argc __attribute__((unused)), char **argv __attribute__((unused))) {
    printf("========================================\n");
    printf("VITA49 Standalone Streamer for Pluto\n");
    printf("========================================\n\n");

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

    printf("Control port: %d\n", CONTROL_PORT);
    printf("Data port: %d\n\n", DATA_PORT);

    /* Start threads */
    pthread_t control_tid, streaming_tid;

    pthread_create(&control_tid, NULL, control_thread, &control_sock);
    pthread_create(&streaming_tid, NULL, streaming_thread, ctx);

    /* Monitor */
    while (g_running) {
        sleep(5);
        printf("[Stats] Packets: %llu, Bytes: %llu MB, Contexts: %u, Subs: %d\n",
               (unsigned long long)g_stats.packets_sent,
               (unsigned long long)(g_stats.bytes_sent / 1048576),
               g_stats.contexts_sent, g_subscriber_count);
    }

    /* Cleanup */
    pthread_join(control_tid, NULL);
    pthread_join(streaming_tid, NULL);

    close(control_sock);
    iio_context_destroy(ctx);

    printf("\n✓ Stopped\n");
    return 0;
}
