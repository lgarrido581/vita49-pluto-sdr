/*
 * Lock-Free Ring Buffer Test Suite
 *
 * Tests for correctness, performance, and thread safety of the ring buffer
 * implementation used in VITA49 multicore optimization.
 *
 * Compile: gcc -o test_ring_buffer test_ring_buffer.c -pthread -std=c11
 * Usage: ./test_ring_buffer [test_name]
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>
#include <time.h>
#include <assert.h>
#include <stdatomic.h>
#include <sys/time.h>
#include "../src/lock_free_ring_buffer.h"

/* Test configuration */
#define TEST_DURATION_SEC       5
#define TEST_SAMPLES_PER_BUFFER 1024
#define STRESS_TEST_ITERATIONS  1000000

/* Global test state */
static lock_free_ring_buffer_t g_ring_buffer;
static atomic_bool g_test_running = ATOMIC_VAR_INIT(true);
static atomic_uint64_t g_producer_count = ATOMIC_VAR_INIT(0);
static atomic_uint64_t g_consumer_count = ATOMIC_VAR_INIT(0);

/* Test results */
typedef struct {
    bool passed;
    const char* name;
    const char* error_msg;
    double duration_ms;
    uint64_t operations;
} test_result_t;

/* Utility functions */
static double get_time_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1000000.0;
}

static uint64_t get_time_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000000ULL + ts.tv_nsec / 1000;
}

/* Test 1: Basic functionality test */
static test_result_t test_basic_functionality(void) {
    test_result_t result = {.name = "Basic Functionality"};
    double start_time = get_time_ms();
    
    printf("Running basic functionality test...\n");
    
    /* Initialize ring buffer */
    ring_buffer_init(&g_ring_buffer);
    
    /* Test empty buffer */
    if (!ring_buffer_empty(&g_ring_buffer)) {
        result.error_msg = "New buffer should be empty";
        goto fail;
    }
    
    if (ring_buffer_size(&g_ring_buffer) != 0) {
        result.error_msg = "New buffer size should be 0";
        goto fail;
    }
    
    /* Test single push/pop */
    iq_buffer_entry_t entry = {
        .data = g_ring_buffer.sample_pool[0],
        .sample_count = TEST_SAMPLES_PER_BUFFER,
        .timestamp_us = get_time_us(),
        .sequence_num = 1,
        .buffer_id = 0
    };
    
    /* Fill sample data */
    for (size_t i = 0; i < TEST_SAMPLES_PER_BUFFER * 2; i++) {
        entry.data[i] = (int16_t)i;
    }
    
    if (!ring_buffer_push(&g_ring_buffer, &entry)) {
        result.error_msg = "Failed to push to empty buffer";
        goto fail;
    }
    
    if (ring_buffer_empty(&g_ring_buffer)) {
        result.error_msg = "Buffer should not be empty after push";
        goto fail;
    }
    
    if (ring_buffer_size(&g_ring_buffer) != 1) {
        result.error_msg = "Buffer size should be 1 after single push";
        goto fail;
    }
    
    /* Test pop */
    iq_buffer_entry_t popped_entry;
    if (!ring_buffer_pop(&g_ring_buffer, &popped_entry)) {
        result.error_msg = "Failed to pop from non-empty buffer";
        goto fail;
    }
    
    /* Verify data integrity */
    if (popped_entry.sample_count != entry.sample_count ||
        popped_entry.sequence_num != entry.sequence_num ||
        popped_entry.buffer_id != entry.buffer_id) {
        result.error_msg = "Popped data doesn't match pushed data";
        goto fail;
    }
    
    /* Verify sample data */
    for (size_t i = 0; i < TEST_SAMPLES_PER_BUFFER * 2; i++) {
        if (popped_entry.data[i] != (int16_t)i) {
            result.error_msg = "Sample data corrupted";
            goto fail;
        }
    }
    
    if (!ring_buffer_empty(&g_ring_buffer)) {
        result.error_msg = "Buffer should be empty after pop";
        goto fail;
    }
    
    printf("✓ Basic functionality test passed\n");
    result.passed = true;
    result.duration_ms = get_time_ms() - start_time;
    return result;
    
fail:
    result.passed = false;
    result.duration_ms = get_time_ms() - start_time;
    return result;
}

/* Test 2: Buffer full/wrap-around test */
static test_result_t test_buffer_wraparound(void) {
    test_result_t result = {.name = "Buffer Wrap-around"};
    double start_time = get_time_ms();
    
    printf("Running buffer wrap-around test...\n");
    
    ring_buffer_init(&g_ring_buffer);
    
    /* Fill buffer to capacity */
    for (size_t i = 0; i < RING_BUFFER_CAPACITY - 1; i++) {
        iq_buffer_entry_t entry = {
            .data = g_ring_buffer.sample_pool[i],
            .sample_count = 100,
            .timestamp_us = get_time_us(),
            .sequence_num = i,
            .buffer_id = i
        };
        
        if (!ring_buffer_push(&g_ring_buffer, &entry)) {
            result.error_msg = "Failed to fill buffer";
            goto fail;
        }
    }
    
    if (ring_buffer_size(&g_ring_buffer) != RING_BUFFER_CAPACITY - 1) {
        result.error_msg = "Buffer size incorrect after fill";
        goto fail;
    }
    
    /* Buffer should now be full (one slot reserved) */
    iq_buffer_entry_t overflow_entry = {
        .sequence_num = 999,
        .buffer_id = 999
    };
    
    if (ring_buffer_push(&g_ring_buffer, &overflow_entry)) {
        result.error_msg = "Should not be able to push to full buffer";
        goto fail;
    }
    
    /* Pop half the entries */
    for (size_t i = 0; i < (RING_BUFFER_CAPACITY - 1) / 2; i++) {
        iq_buffer_entry_t entry;
        if (!ring_buffer_pop(&g_ring_buffer, &entry)) {
            result.error_msg = "Failed to pop from buffer";
            goto fail;
        }
        
        if (entry.sequence_num != i) {
            result.error_msg = "Popped entry sequence mismatch";
            goto fail;
        }
    }
    
    /* Push new entries (testing wrap-around) */
    for (size_t i = 0; i < 10; i++) {
        iq_buffer_entry_t entry = {
            .data = g_ring_buffer.sample_pool[i],
            .sequence_num = 1000 + i,
            .buffer_id = i
        };
        
        if (!ring_buffer_push(&g_ring_buffer, &entry)) {
            result.error_msg = "Failed to push after partial drain";
            goto fail;
        }
    }
    
    printf("✓ Buffer wrap-around test passed\n");
    result.passed = true;
    result.duration_ms = get_time_ms() - start_time;
    return result;
    
fail:
    result.passed = false;
    result.duration_ms = get_time_ms() - start_time;
    return result;
}

/* Producer thread for stress test */
static void* producer_thread(void* arg) {
    uint32_t* sequence = (uint32_t*)arg;
    
    while (atomic_load(&g_test_running)) {
        iq_buffer_entry_t entry = {
            .data = g_ring_buffer.sample_pool[*sequence % RING_BUFFER_CAPACITY],
            .sample_count = TEST_SAMPLES_PER_BUFFER,
            .timestamp_us = get_time_us(),
            .sequence_num = *sequence,
            .buffer_id = *sequence % RING_BUFFER_CAPACITY
        };
        
        /* Fill with test pattern */
        for (size_t i = 0; i < TEST_SAMPLES_PER_BUFFER * 2; i++) {
            entry.data[i] = (int16_t)(*sequence + i);
        }
        
        if (ring_buffer_push(&g_ring_buffer, &entry)) {
            (*sequence)++;
            atomic_fetch_add(&g_producer_count, 1);
        }
        
        /* Small yield to allow consumer to run */
        usleep(1);
    }
    
    return NULL;
}

/* Consumer thread for stress test */
static void* consumer_thread(void* arg) {
    uint32_t expected_sequence = 0;
    uint32_t* errors = (uint32_t*)arg;
    
    while (atomic_load(&g_test_running)) {
        iq_buffer_entry_t entry;
        
        if (ring_buffer_pop(&g_ring_buffer, &entry)) {
            atomic_fetch_add(&g_consumer_count, 1);
            
            /* Verify sequence ordering */
            if (entry.sequence_num != expected_sequence) {
                (*errors)++;
                printf("Sequence error: expected %u, got %u\n", 
                       expected_sequence, entry.sequence_num);
            }
            
            /* Verify sample data integrity */
            for (size_t i = 0; i < TEST_SAMPLES_PER_BUFFER * 2; i++) {
                int16_t expected_value = (int16_t)(expected_sequence + i);
                if (entry.data[i] != expected_value) {
                    (*errors)++;
                    printf("Data corruption: sample[%zu] expected %d, got %d\n",
                           i, expected_value, entry.data[i]);
                    break;
                }
            }
            
            expected_sequence++;
        }
        
        usleep(1);
    }
    
    return NULL;
}

/* Test 3: Multi-threaded stress test */
static test_result_t test_multithread_stress(void) {
    test_result_t result = {.name = "Multithread Stress"};
    double start_time = get_time_ms();
    
    printf("Running %d second multithread stress test...\n", TEST_DURATION_SEC);
    
    ring_buffer_init(&g_ring_buffer);
    atomic_store(&g_test_running, true);
    atomic_store(&g_producer_count, 0);
    atomic_store(&g_consumer_count, 0);
    
    pthread_t producer_tid, consumer_tid;
    uint32_t producer_sequence = 0;
    uint32_t consumer_errors = 0;
    
    /* Create threads */
    if (pthread_create(&producer_tid, NULL, producer_thread, &producer_sequence) != 0) {
        result.error_msg = "Failed to create producer thread";
        goto fail;
    }
    
    if (pthread_create(&consumer_tid, NULL, consumer_thread, &consumer_errors) != 0) {
        result.error_msg = "Failed to create consumer thread";
        pthread_cancel(producer_tid);
        goto fail;
    }
    
    /* Run test for specified duration */
    sleep(TEST_DURATION_SEC);
    
    /* Stop threads */
    atomic_store(&g_test_running, false);
    pthread_join(producer_tid, NULL);
    pthread_join(consumer_tid, NULL);
    
    /* Check results */
    uint64_t produced = atomic_load(&g_producer_count);
    uint64_t consumed = atomic_load(&g_consumer_count);
    ring_buffer_stats_t stats = ring_buffer_get_stats(&g_ring_buffer);
    
    printf("Producer: %lu entries/sec\n", produced / TEST_DURATION_SEC);
    printf("Consumer: %lu entries/sec\n", consumed / TEST_DURATION_SEC);
    printf("Buffer utilization: %.1f%%\n", stats.current_utilization * 100);
    printf("Push success rate: %.1f%%\n", stats.push_success_rate * 100);
    printf("Pop success rate: %.1f%%\n", stats.pop_success_rate * 100);
    printf("Data errors: %u\n", consumer_errors);
    
    if (consumer_errors > 0) {
        result.error_msg = "Data corruption detected";
        goto fail;
    }
    
    if (stats.push_success_rate < 0.8) {
        result.error_msg = "Push success rate too low";
        goto fail;
    }
    
    printf("✓ Multithread stress test passed\n");
    result.passed = true;
    result.duration_ms = get_time_ms() - start_time;
    result.operations = produced + consumed;
    return result;
    
fail:
    result.passed = false;
    result.duration_ms = get_time_ms() - start_time;
    return result;
}

/* Test 4: Performance benchmark */
static test_result_t test_performance_benchmark(void) {
    test_result_t result = {.name = "Performance Benchmark"};
    double start_time = get_time_ms();
    
    printf("Running performance benchmark...\n");
    
    ring_buffer_init(&g_ring_buffer);
    
    /* Benchmark push/pop operations */
    double push_start = get_time_ms();
    
    for (uint64_t i = 0; i < STRESS_TEST_ITERATIONS; i++) {
        iq_buffer_entry_t entry = {
            .data = g_ring_buffer.sample_pool[i % RING_BUFFER_CAPACITY],
            .sample_count = TEST_SAMPLES_PER_BUFFER,
            .timestamp_us = get_time_us(),
            .sequence_num = i,
            .buffer_id = i % RING_BUFFER_CAPACITY
        };
        
        ring_buffer_push(&g_ring_buffer, &entry);
        
        /* Pop immediately to prevent overflow */
        iq_buffer_entry_t popped;
        ring_buffer_pop(&g_ring_buffer, &popped);
    }
    
    double push_duration = get_time_ms() - push_start;
    double ops_per_sec = (STRESS_TEST_ITERATIONS * 2) / (push_duration / 1000.0);
    
    printf("Operations: %d push/pop pairs\n", STRESS_TEST_ITERATIONS);
    printf("Duration: %.2f ms\n", push_duration);
    printf("Throughput: %.0f operations/sec\n", ops_per_sec);
    printf("Latency: %.3f μs per operation\n", (push_duration * 1000) / (STRESS_TEST_ITERATIONS * 2));
    
    printf("✓ Performance benchmark completed\n");
    result.passed = true;
    result.duration_ms = get_time_ms() - start_time;
    result.operations = STRESS_TEST_ITERATIONS * 2;
    return result;
}

/* Main test runner */
int main(int argc, char* argv[]) {
    printf("Lock-Free Ring Buffer Test Suite\n");
    printf("================================\n\n");
    
    test_result_t tests[] = {
        test_basic_functionality(),
        test_buffer_wraparound(),
        test_multithread_stress(),
        test_performance_benchmark()
    };
    
    size_t num_tests = sizeof(tests) / sizeof(tests[0]);
    size_t passed = 0;
    
    printf("\nTest Results:\n");
    printf("=============\n");
    
    for (size_t i = 0; i < num_tests; i++) {
        printf("%s: %s", tests[i].name, tests[i].passed ? "PASS" : "FAIL");
        
        if (!tests[i].passed) {
            printf(" (%s)", tests[i].error_msg);
        }
        
        printf(" [%.2f ms", tests[i].duration_ms);
        if (tests[i].operations > 0) {
            printf(", %lu ops", tests[i].operations);
        }
        printf("]\n");
        
        if (tests[i].passed) passed++;
    }
    
    printf("\nSummary: %zu/%zu tests passed\n", passed, num_tests);
    
    return (passed == num_tests) ? 0 : 1;
}