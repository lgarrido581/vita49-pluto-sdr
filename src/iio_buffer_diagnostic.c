/*
 * IIO Buffer Diagnostic Tool for ADALM-Pluto
 *
 * Standalone program to characterize IIO buffer behavior independent of
 * VITA49 encoding or network transmission. Helps isolate performance issues.
 *
 * Build: arm-linux-gnueabihf-gcc -O2 -o iio_buffer_diagnostic iio_buffer_diagnostic.c -liio -lm
 *
 * Usage:
 *   ./iio_buffer_diagnostic [options]
 *   ./iio_buffer_diagnostic --all-rates
 *   ./iio_buffer_diagnostic --rate 30000000 --duration 10
 */

#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <unistd.h>
#include <math.h>
#include <sys/time.h>
#include <signal.h>
#include <iio.h>

static volatile bool g_running = true;

/* Get current timestamp in microseconds */
static uint64_t get_timestamp_us(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000000ULL + tv.tv_usec;
}

/* Signal handler */
static void signal_handler(int sig) {
    (void)sig;
    g_running = false;
}

/* Print device info */
static void print_device_info(struct iio_context *ctx) {
    printf("\n--- IIO Context Info ---\n");

    unsigned int dev_count = iio_context_get_devices_count(ctx);
    printf("Devices found: %u\n", dev_count);

    for (unsigned int i = 0; i < dev_count; i++) {
        struct iio_device *dev = iio_context_get_device(ctx, i);
        const char *name = iio_device_get_name(dev);
        printf("  [%u] %s\n", i, name ? name : "unnamed");
    }

    /* Check for ad9361-phy */
    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    if (phy) {
        char buf[64];
        struct iio_channel *ch = iio_device_find_channel(phy, "voltage0", false);
        if (ch) {
            if (iio_channel_attr_read(ch, "sampling_frequency", buf, sizeof(buf)) > 0) {
                printf("Current sample rate: %s Hz\n", buf);
            }
        }
    }
    printf("\n");
}

/* Configure sample rate */
static int configure_sample_rate(struct iio_context *ctx, uint32_t rate) {
    struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
    if (!phy) {
        fprintf(stderr, "ERROR: ad9361-phy not found\n");
        return -1;
    }

    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    if (!dev) {
        fprintf(stderr, "ERROR: cf-ad9361-lpc not found\n");
        return -1;
    }

    char buf[64];

    /* Disable channels first */
    struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
    struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);
    if (rx0_i) iio_channel_disable(rx0_i);
    if (rx0_q) iio_channel_disable(rx0_q);

    /* Set sample rate */
    struct iio_channel *phy_ch = iio_device_find_channel(phy, "voltage0", false);
    if (phy_ch) {
        snprintf(buf, sizeof(buf), "%u", rate);
        ssize_t ret = iio_channel_attr_write(phy_ch, "sampling_frequency", buf);
        if (ret < 0) {
            fprintf(stderr, "WARNING: Failed to set sample rate: %zd\n", ret);
        }

        /* Verify */
        if (iio_channel_attr_read(phy_ch, "sampling_frequency", buf, sizeof(buf)) > 0) {
            uint32_t actual = atoi(buf);
            if (actual != rate) {
                printf("NOTE: Requested %u Hz, got %u Hz\n", rate, actual);
            }
        }

        /* Set bandwidth */
        snprintf(buf, sizeof(buf), "%u", (uint32_t)(rate * 0.8));
        iio_channel_attr_write(phy_ch, "rf_bandwidth", buf);
    }

    usleep(50000);  /* 50ms settle time */

    /* Re-enable channels */
    if (rx0_i) iio_channel_enable(rx0_i);
    if (rx0_q) iio_channel_enable(rx0_q);

    return 0;
}

/* Test 1: Buffer Refill Timing */
static void test_refill_timing(struct iio_context *ctx, uint32_t sample_rate,
                                size_t buffer_size, int duration_sec) {
    printf("\n=== Test 1: Buffer Refill Timing ===\n");
    printf("Sample Rate: %.2f MSPS\n", sample_rate / 1e6);
    printf("Buffer Size: %zu samples\n", buffer_size);

    double expected_ms = (double)buffer_size / sample_rate * 1000.0;
    printf("Expected Refill Time: %.3f ms\n\n", expected_ms);

    configure_sample_rate(ctx, sample_rate);

    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_size, false);

    if (!rxbuf) {
        fprintf(stderr, "ERROR: Failed to create buffer\n");
        return;
    }

    uint64_t total_refill_time_us = 0;
    uint64_t min_refill_us = UINT64_MAX;
    uint64_t max_refill_us = 0;
    int refill_count = 0;

    /* Histogram buckets (0-1ms, 1-2ms, ..., 10+ms) */
    int histogram[12] = {0};

    uint64_t start_time = get_timestamp_us();
    uint64_t end_time = start_time + (duration_sec * 1000000ULL);

    printf("Running for %d seconds...\n", duration_sec);

    while (get_timestamp_us() < end_time && g_running) {
        uint64_t t1 = get_timestamp_us();
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        uint64_t t2 = get_timestamp_us();

        if (nbytes < 0) {
            printf("ERROR: iio_buffer_refill failed: %zd\n", nbytes);
            continue;
        }

        uint64_t refill_us = t2 - t1;
        total_refill_time_us += refill_us;
        refill_count++;

        if (refill_us < min_refill_us) min_refill_us = refill_us;
        if (refill_us > max_refill_us) max_refill_us = refill_us;

        int bucket = refill_us / 1000;
        if (bucket > 11) bucket = 11;
        histogram[bucket]++;

        size_t expected_bytes = buffer_size * 4;
        if ((size_t)nbytes != expected_bytes) {
            printf("WARNING: Expected %zu bytes, got %zd\n", expected_bytes, nbytes);
        }
    }

    if (refill_count == 0) {
        printf("ERROR: No successful refills\n");
        iio_buffer_destroy(rxbuf);
        return;
    }

    double avg_refill_ms = (double)total_refill_time_us / refill_count / 1000.0;
    double actual_rate = (double)buffer_size * refill_count / (duration_sec * 1e6);

    printf("\nResults:\n");
    printf("  Refills completed: %d\n", refill_count);
    printf("  Avg refill time: %.3f ms (expected: %.3f ms)\n", avg_refill_ms, expected_ms);
    printf("  Min refill time: %.3f ms\n", min_refill_us / 1000.0);
    printf("  Max refill time: %.3f ms\n", max_refill_us / 1000.0);
    printf("  Timing ratio: %.2f%% of expected\n", (avg_refill_ms / expected_ms) * 100.0);
    printf("  Effective sample rate: %.2f MSPS\n", actual_rate);
    printf("  Rate accuracy: %.2f%% of requested\n", (actual_rate / (sample_rate / 1e6)) * 100.0);

    printf("\nHistogram (refill times):\n");
    for (int i = 0; i < 12; i++) {
        if (histogram[i] > 0) {
            const char *label = (i == 11) ? "10+ " : "";
            printf("  %s%d-%d ms: %d (%.1f%%)\n",
                   label, i, i + 1, histogram[i],
                   100.0 * histogram[i] / refill_count);
        }
    }

    double timing_error = fabs(avg_refill_ms - expected_ms) / expected_ms;
    if (timing_error < 0.10) {
        printf("\n[PASS] Refill timing within 10%% of expected\n");
    } else {
        printf("\n[FAIL] Refill timing off by %.1f%%\n", timing_error * 100);
        if (avg_refill_ms < expected_ms * 0.5) {
            printf("  -> Buffer returning too fast - DMA may not be blocking properly\n");
        }
    }

    iio_buffer_destroy(rxbuf);
}

/* Test 2: Channel Configuration */
static void test_channel_configuration(struct iio_context *ctx) {
    printf("\n=== Test 2: Channel Configuration ===\n");

    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    if (!dev) {
        fprintf(stderr, "ERROR: cf-ad9361-lpc not found\n");
        return;
    }

    unsigned int ch_count = iio_device_get_channels_count(dev);
    printf("Total channels on cf-ad9361-lpc: %u\n\n", ch_count);

    for (unsigned int i = 0; i < ch_count; i++) {
        struct iio_channel *ch = iio_device_get_channel(dev, i);
        const char *name = iio_channel_get_name(ch);
        const char *id = iio_channel_get_id(ch);
        bool is_output = iio_channel_is_output(ch);
        bool is_enabled = iio_channel_is_enabled(ch);

        printf("Channel %u: id=%s, name=%s, output=%d, enabled=%d\n",
               i, id ? id : "NULL", name ? name : "NULL", is_output, is_enabled);
    }

    struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
    struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);
    struct iio_channel *rx1_i = iio_device_find_channel(dev, "voltage2", false);
    struct iio_channel *rx1_q = iio_device_find_channel(dev, "voltage3", false);

    /* Disable all first */
    if (rx0_i) iio_channel_disable(rx0_i);
    if (rx0_q) iio_channel_disable(rx0_q);
    if (rx1_i) iio_channel_disable(rx1_i);
    if (rx1_q) iio_channel_disable(rx1_q);

    /* Test Case A: Enable only RX0 */
    printf("\nTest A: Enable only voltage0 + voltage1 (RX0 I/Q)\n");
    if (rx0_i) iio_channel_enable(rx0_i);
    if (rx0_q) iio_channel_enable(rx0_q);

    struct iio_buffer *buf = iio_device_create_buffer(dev, 1024, false);
    if (buf) {
        ptrdiff_t step = iio_buffer_step(buf);
        printf("  Buffer step: %td bytes (expect 4 for 1 channel pair)\n", step);

        iio_buffer_refill(buf);

        void *start = iio_buffer_start(buf);
        void *end = iio_buffer_end(buf);
        size_t total_bytes = (char *)end - (char *)start;
        printf("  Buffer size: %zu bytes\n", total_bytes);
        printf("  Samples per channel: %zu\n", total_bytes / step);

        iio_buffer_destroy(buf);
    } else {
        printf("  ERROR: Failed to create buffer\n");
    }

    /* Test Case B: Enable both RX0 and RX1 (2R2T mode) */
    if (rx1_i && rx1_q) {
        printf("\nTest B: Enable voltage0-3 (RX0 + RX1 I/Q)\n");
        if (rx0_i) iio_channel_enable(rx0_i);
        if (rx0_q) iio_channel_enable(rx0_q);
        if (rx1_i) iio_channel_enable(rx1_i);
        if (rx1_q) iio_channel_enable(rx1_q);

        buf = iio_device_create_buffer(dev, 1024, false);
        if (buf) {
            ptrdiff_t step = iio_buffer_step(buf);
            printf("  Buffer step: %td bytes (expect 8 for 2 channel pairs)\n", step);

            iio_buffer_refill(buf);

            void *start = iio_buffer_start(buf);
            void *end = iio_buffer_end(buf);
            size_t total_bytes = (char *)end - (char *)start;
            printf("  Buffer size: %zu bytes\n", total_bytes);
            printf("  Samples per channel: %zu\n", total_bytes / step);

            iio_buffer_destroy(buf);
        }

        /* Restore to single channel */
        if (rx1_i) iio_channel_disable(rx1_i);
        if (rx1_q) iio_channel_disable(rx1_q);
    } else {
        printf("\nTest B: SKIPPED (RX1 channels not available - single channel device)\n");
    }
}

/* Test 3: Memory Bandwidth */
static void test_memory_bandwidth(void) {
    printf("\n=== Test 3: Memory Bandwidth ===\n");

    size_t buffer_size = 256 * 1024;  /* 256 KB */
    int16_t *src = aligned_alloc(64, buffer_size);
    int16_t *dst = aligned_alloc(64, buffer_size);

    if (!src || !dst) {
        printf("ERROR: Failed to allocate test buffers\n");
        if (src) free(src);
        if (dst) free(dst);
        return;
    }

    for (size_t i = 0; i < buffer_size / sizeof(int16_t); i++) {
        src[i] = (int16_t)(i & 0xFFFF);
    }

    int iterations = 1000;

    /* Test 1: Raw memcpy */
    printf("\nTest: memcpy %zu KB x %d iterations\n", buffer_size / 1024, iterations);
    uint64_t t1 = get_timestamp_us();
    for (int i = 0; i < iterations; i++) {
        memcpy(dst, src, buffer_size);
    }
    uint64_t t2 = get_timestamp_us();

    double total_mb = (double)buffer_size * iterations / (1024 * 1024);
    double time_sec = (t2 - t1) / 1e6;
    double bandwidth_mbs = total_mb / time_sec;

    printf("  Time: %.3f ms\n", (t2 - t1) / 1000.0);
    printf("  Bandwidth: %.1f MB/s = %.1f Mbps\n", bandwidth_mbs, bandwidth_mbs * 8);

    /* Test 2: memcpy with byte swap (simulating old htons approach) */
    printf("\nTest: byte swap loop (old approach)\n");
    t1 = get_timestamp_us();
    for (int iter = 0; iter < iterations; iter++) {
        for (size_t i = 0; i < buffer_size / sizeof(int16_t); i++) {
            dst[i] = __builtin_bswap16(src[i]);
        }
    }
    t2 = get_timestamp_us();

    time_sec = (t2 - t1) / 1e6;
    bandwidth_mbs = total_mb / time_sec;

    printf("  Time: %.3f ms\n", (t2 - t1) / 1000.0);
    printf("  Bandwidth: %.1f MB/s = %.1f Mbps\n", bandwidth_mbs, bandwidth_mbs * 8);

    /* Test 3: Packet-sized copies */
    printf("\nTest: Packet-sized memcpy (1448 bytes x N)\n");
    size_t packet_size = 1448;
    int packets_per_iter = buffer_size / packet_size;

    t1 = get_timestamp_us();
    for (int iter = 0; iter < iterations; iter++) {
        for (int p = 0; p < packets_per_iter; p++) {
            memcpy((char *)dst + (p * packet_size),
                   (char *)src + (p * packet_size), packet_size);
        }
    }
    t2 = get_timestamp_us();

    time_sec = (t2 - t1) / 1e6;
    bandwidth_mbs = total_mb / time_sec;

    printf("  Time: %.3f ms\n", (t2 - t1) / 1000.0);
    printf("  Bandwidth: %.1f MB/s = %.1f Mbps\n", bandwidth_mbs, bandwidth_mbs * 8);

    free(src);
    free(dst);
}

/* Test 4: Sample Rate Sweep */
static void test_sample_rate_sweep(struct iio_context *ctx) {
    printf("\n=== Test 4: Sample Rate Sweep ===\n");

    uint32_t rates[] = {2083334, 5000000, 10000000, 15000000, 20000000, 25000000, 30000000, 30720000};
    int num_rates = sizeof(rates) / sizeof(rates[0]);

    size_t buffer_size = 16384;
    int test_duration_sec = 5;

    printf("\nBuffer size: %zu samples\n", buffer_size);
    printf("Test duration: %d seconds per rate\n\n", test_duration_sec);

    printf("%-12s  %-12s  %-12s  %-12s  %-10s\n",
           "Rate (MSPS)", "Expected(ms)", "Actual(ms)", "Eff.Rate", "Status");
    printf("%-12s  %-12s  %-12s  %-12s  %-10s\n",
           "------------", "------------", "------------", "------------", "----------");

    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");

    for (int r = 0; r < num_rates && g_running; r++) {
        uint32_t rate = rates[r];
        double expected_ms = (double)buffer_size / rate * 1000.0;

        configure_sample_rate(ctx, rate);
        usleep(100000);  /* 100ms settle time */

        struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_size, false);

        if (!rxbuf) {
            printf("%-12.2f  FAILED TO CREATE BUFFER\n", rate / 1e6);
            continue;
        }

        uint64_t total_time = 0;
        int count = 0;
        uint64_t end_time = get_timestamp_us() + (test_duration_sec * 1000000ULL);

        while (get_timestamp_us() < end_time && g_running) {
            uint64_t t1 = get_timestamp_us();
            ssize_t nbytes = iio_buffer_refill(rxbuf);
            uint64_t t2 = get_timestamp_us();

            if (nbytes > 0) {
                total_time += (t2 - t1);
                count++;
            }
        }

        double actual_ms = (count > 0) ? (double)total_time / count / 1000.0 : 0;
        double eff_rate = (double)buffer_size * count / test_duration_sec / 1e6;

        const char *status;
        double ratio = (expected_ms > 0) ? actual_ms / expected_ms : 0;
        if (ratio > 0.9 && ratio < 1.1) {
            status = "PASS";
        } else if (ratio < 0.5) {
            status = "TOO FAST";
        } else {
            status = "FAIL";
        }

        printf("%-12.2f  %-12.3f  %-12.3f  %-12.2f  %-10s\n",
               rate / 1e6, expected_ms, actual_ms, eff_rate, status);

        iio_buffer_destroy(rxbuf);
    }
}

/* Test 5: Buffer Size Impact */
static void test_buffer_sizes(struct iio_context *ctx, uint32_t sample_rate) {
    printf("\n=== Test 5: Buffer Size Impact ===\n");
    printf("Sample Rate: %.2f MSPS\n\n", sample_rate / 1e6);

    size_t sizes[] = {1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072};
    int num_sizes = sizeof(sizes) / sizeof(sizes[0]);

    configure_sample_rate(ctx, sample_rate);

    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");

    printf("%-12s  %-12s  %-12s  %-12s  %-12s\n",
           "Buffer", "Expected(ms)", "Actual(ms)", "Overhead", "Efficiency");
    printf("%-12s  %-12s  %-12s  %-12s  %-12s\n",
           "------------", "------------", "------------", "------------", "------------");

    for (int s = 0; s < num_sizes && g_running; s++) {
        size_t buffer_size = sizes[s];
        double expected_ms = (double)buffer_size / sample_rate * 1000.0;

        struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_size, false);

        if (!rxbuf) {
            printf("%-12zu  FAILED TO CREATE BUFFER\n", buffer_size);
            continue;
        }

        /* Warm up */
        for (int i = 0; i < 10; i++) {
            iio_buffer_refill(rxbuf);
        }

        uint64_t total_time = 0;
        int count = 100;

        for (int i = 0; i < count && g_running; i++) {
            uint64_t t1 = get_timestamp_us();
            iio_buffer_refill(rxbuf);
            uint64_t t2 = get_timestamp_us();
            total_time += (t2 - t1);
        }

        double actual_ms = (double)total_time / count / 1000.0;
        double overhead_ms = actual_ms - expected_ms;
        double efficiency = expected_ms / actual_ms * 100.0;

        printf("%-12zu  %-12.3f  %-12.3f  %-+12.3f  %-12.1f%%\n",
               buffer_size, expected_ms, actual_ms, overhead_ms, efficiency);

        iio_buffer_destroy(rxbuf);
    }
}

/* Test 6: Data Integrity Check */
static void test_data_integrity(struct iio_context *ctx, uint32_t sample_rate) {
    printf("\n=== Test 6: Data Integrity Check ===\n");

    configure_sample_rate(ctx, sample_rate);

    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    struct iio_channel *rx_i = iio_device_find_channel(dev, "voltage0", false);

    size_t buffer_size = 8192;
    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_size, false);

    if (!rxbuf) {
        fprintf(stderr, "ERROR: Failed to create buffer\n");
        return;
    }

    uint32_t prev_checksum = 0;
    int identical_count = 0;
    int total_buffers = 100;

    int16_t prev_samples[16] = {0};
    int samples_identical = 0;

    printf("Checking %d consecutive buffers for duplicate data...\n\n", total_buffers);

    for (int i = 0; i < total_buffers && g_running; i++) {
        iio_buffer_refill(rxbuf);

        int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, rx_i);

        uint32_t checksum = 0;
        for (size_t j = 0; j < buffer_size * 2; j += 16) {
            checksum += samples[j];
        }

        if (i > 0) {
            if (checksum == prev_checksum) {
                identical_count++;
            }

            bool first_samples_same = true;
            for (int k = 0; k < 16; k++) {
                if (samples[k] != prev_samples[k]) {
                    first_samples_same = false;
                    break;
                }
            }
            if (first_samples_same) {
                samples_identical++;
            }
        }

        prev_checksum = checksum;
        memcpy(prev_samples, samples, sizeof(prev_samples));
    }

    printf("Results:\n");
    printf("  Buffers with identical checksum: %d/%d (%.1f%%)\n",
           identical_count, total_buffers - 1,
           100.0 * identical_count / (total_buffers - 1));
    printf("  Buffers with identical first samples: %d/%d (%.1f%%)\n",
           samples_identical, total_buffers - 1,
           100.0 * samples_identical / (total_buffers - 1));

    if (identical_count == 0 && samples_identical == 0) {
        printf("\n[PASS] All buffers contain unique data\n");
    } else {
        printf("\n[FAIL] Detected duplicate data - DMA may not be working correctly\n");
    }

    /* Print sample statistics */
    iio_buffer_refill(rxbuf);
    int16_t *samples = (int16_t *)iio_buffer_first(rxbuf, rx_i);

    int16_t min_val = INT16_MAX, max_val = INT16_MIN;
    int64_t sum = 0;
    for (size_t j = 0; j < buffer_size * 2; j++) {
        if (samples[j] < min_val) min_val = samples[j];
        if (samples[j] > max_val) max_val = samples[j];
        sum += samples[j];
    }
    double mean = (double)sum / (buffer_size * 2);

    printf("\nSample statistics:\n");
    printf("  Min: %d, Max: %d, Mean: %.1f\n", min_val, max_val, mean);
    printf("  Range: %d (%.1f%% of full scale)\n",
           max_val - min_val,
           100.0 * (max_val - min_val) / 65536);

    iio_buffer_destroy(rxbuf);
}

/* Test 7: Sustained Throughput */
static void test_sustained_throughput(struct iio_context *ctx, uint32_t sample_rate,
                                       int duration_sec) {
    printf("\n=== Test 7: Sustained Throughput ===\n");
    printf("Sample Rate: %.2f MSPS\n", sample_rate / 1e6);
    printf("Duration: %d seconds\n\n", duration_sec);

    configure_sample_rate(ctx, sample_rate);

    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    size_t buffer_size = 65536;
    struct iio_buffer *rxbuf = iio_device_create_buffer(dev, buffer_size, false);

    if (!rxbuf) {
        fprintf(stderr, "ERROR: Failed to create buffer\n");
        return;
    }

    uint64_t total_bytes = 0;
    uint64_t total_refills = 0;
    uint64_t start_time = get_timestamp_us();
    uint64_t end_time = start_time + (duration_sec * 1000000ULL);

    printf("Running sustained throughput test...\n");

    while (get_timestamp_us() < end_time && g_running) {
        ssize_t nbytes = iio_buffer_refill(rxbuf);
        if (nbytes > 0) {
            total_bytes += nbytes;
            total_refills++;
        }
    }

    uint64_t actual_duration = get_timestamp_us() - start_time;
    double seconds = actual_duration / 1e6;

    double throughput_mbps = (total_bytes * 8.0) / (seconds * 1e6);
    double samples_per_sec = (total_bytes / 4.0) / seconds;
    double refills_per_sec = total_refills / seconds;

    printf("\nResults:\n");
    printf("  Total data: %.2f MB\n", total_bytes / (1024.0 * 1024.0));
    printf("  Total refills: %llu\n", (unsigned long long)total_refills);
    printf("  Throughput: %.2f Mbps\n", throughput_mbps);
    printf("  Effective sample rate: %.2f MSPS\n", samples_per_sec / 1e6);
    printf("  Refills/sec: %.1f\n", refills_per_sec);

    double expected_mbps = sample_rate * 4.0 * 8.0 / 1e6;
    double efficiency = throughput_mbps / expected_mbps * 100.0;
    printf("  Efficiency: %.1f%% of %.1f Mbps theoretical\n", efficiency, expected_mbps);

    if (efficiency > 95.0) {
        printf("\n[PASS] Achieving >95%% of theoretical throughput\n");
    } else if (efficiency > 80.0) {
        printf("\n[WARN] Achieving only %.1f%% of theoretical throughput\n", efficiency);
    } else {
        printf("\n[FAIL] Major throughput issue - only %.1f%% of expected\n", efficiency);
    }

    iio_buffer_destroy(rxbuf);
}

/* Print usage */
static void print_usage(const char *prog) {
    printf("Usage: %s [options]\n\n", prog);
    printf("Options:\n");
    printf("  --rate <Hz>        Sample rate to test (default: 30000000)\n");
    printf("  --buffer <samples> Buffer size in samples (default: 65536)\n");
    printf("  --duration <sec>   Test duration in seconds (default: 10)\n");
    printf("  --all-rates        Test all standard rates: 2M, 5M, 10M, 20M, 30M\n");
    printf("  --channel-test     Test 1-channel vs 2-channel configurations\n");
    printf("  --memory-test      Run memory bandwidth tests\n");
    printf("  --help             Show this help\n");
}

int main(int argc, char **argv) {
    printf("========================================\n");
    printf("IIO Buffer Diagnostic Tool for Pluto\n");
    printf("========================================\n");

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    uint32_t sample_rate = 30000000;
    size_t buffer_size = 65536;
    int duration = 10;
    bool run_all_rates = false;
    bool run_channel_test = false;
    bool run_memory_test = false;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--rate") == 0 && i + 1 < argc) {
            sample_rate = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--buffer") == 0 && i + 1 < argc) {
            buffer_size = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--duration") == 0 && i + 1 < argc) {
            duration = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--all-rates") == 0) {
            run_all_rates = true;
        } else if (strcmp(argv[i], "--channel-test") == 0) {
            run_channel_test = true;
        } else if (strcmp(argv[i], "--memory-test") == 0) {
            run_memory_test = true;
        } else if (strcmp(argv[i], "--help") == 0) {
            print_usage(argv[0]);
            return 0;
        }
    }

    struct iio_context *ctx = iio_create_local_context();
    if (!ctx) {
        ctx = iio_create_network_context("192.168.2.1");
    }
    if (!ctx) {
        fprintf(stderr, "ERROR: Failed to create IIO context\n");
        return 1;
    }

    printf("IIO context created\n");
    print_device_info(ctx);

    /* Enable RX channels */
    struct iio_device *dev = iio_context_find_device(ctx, "cf-ad9361-lpc");
    if (dev) {
        struct iio_channel *rx0_i = iio_device_find_channel(dev, "voltage0", false);
        struct iio_channel *rx0_q = iio_device_find_channel(dev, "voltage1", false);
        if (rx0_i) iio_channel_enable(rx0_i);
        if (rx0_q) iio_channel_enable(rx0_q);
    }

    if (run_memory_test) {
        test_memory_bandwidth();
    }

    if (run_channel_test) {
        test_channel_configuration(ctx);
    }

    if (run_all_rates) {
        test_sample_rate_sweep(ctx);
    } else {
        test_refill_timing(ctx, sample_rate, buffer_size, duration);
    }

    test_buffer_sizes(ctx, sample_rate);
    test_data_integrity(ctx, sample_rate);
    test_sustained_throughput(ctx, sample_rate, duration);

    iio_context_destroy(ctx);

    printf("\n========================================\n");
    printf("Diagnostic Complete\n");
    printf("========================================\n");

    return 0;
}
