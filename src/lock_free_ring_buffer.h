/*
 * Lock-Free Ring Buffer for VITA49 Multicore Optimization
 *
 * This implements a single-producer, single-consumer lock-free ring buffer
 * optimized for ARM dual-core architecture. Used to pass IQ data from
 * DMA reader thread (Core 0) to network transmitter thread (Core 1).
 *
 * Key Features:
 * - Zero-copy design with pre-allocated buffer pool
 * - Cache-line aligned for ARM Cortex-A9
 * - Memory ordering optimized for ARM architecture
 * - Power-of-2 sizing for efficient modulo operations
 *
 * Author: VITA49-Pluto Multicore Optimization
 * License: MIT
 */

#ifndef LOCK_FREE_RING_BUFFER_H
#define LOCK_FREE_RING_BUFFER_H

#include <stdatomic.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* Configuration */
#define RING_BUFFER_CAPACITY    64      /* Must be power of 2 */
#define RING_BUFFER_MASK        (RING_BUFFER_CAPACITY - 1)
#define MAX_IQ_SAMPLES          65536   /* Maximum samples per buffer */
#define CACHE_LINE_SIZE         64      /* ARM Cortex-A9 cache line */

/* Ensure power of 2 at compile time */
_Static_assert((RING_BUFFER_CAPACITY & (RING_BUFFER_CAPACITY - 1)) == 0,
               "RING_BUFFER_CAPACITY must be power of 2");

/* IQ Buffer Entry - Contains metadata and pointer to sample data */
typedef struct {
    int16_t* data;              /* Pointer to I/Q samples (interleaved) */
    size_t sample_count;        /* Number of I/Q pairs in this buffer */
    uint64_t timestamp_us;      /* Timestamp when DMA buffer was filled */
    uint32_t sequence_num;      /* Monotonically increasing sequence number */
    uint32_t buffer_id;         /* Buffer pool index (for debugging) */
} iq_buffer_entry_t;

/* Lock-Free Ring Buffer Structure */
typedef struct {
    /* Cache-line aligned atomic indices to avoid false sharing */
    atomic_size_t write_idx __attribute__((aligned(CACHE_LINE_SIZE)));
    atomic_size_t read_idx __attribute__((aligned(CACHE_LINE_SIZE)));
    
    /* Ring buffer entries */
    iq_buffer_entry_t entries[RING_BUFFER_CAPACITY] __attribute__((aligned(CACHE_LINE_SIZE)));
    
    /* Pre-allocated sample buffer pool - eliminates malloc/free */
    int16_t sample_pool[RING_BUFFER_CAPACITY][MAX_IQ_SAMPLES * 2] __attribute__((aligned(CACHE_LINE_SIZE)));
    
    /* Statistics for monitoring */
    _Atomic uint64_t pushes_attempted __attribute__((aligned(CACHE_LINE_SIZE)));
    _Atomic uint64_t pushes_successful;
    _Atomic uint64_t pops_attempted;
    _Atomic uint64_t pops_successful;
    _Atomic uint64_t buffer_full_events;
    _Atomic uint64_t buffer_empty_events;
    
} lock_free_ring_buffer_t;

/* Initialize ring buffer - call once at startup */
static inline void ring_buffer_init(lock_free_ring_buffer_t* rb) {
    atomic_init(&rb->write_idx, 0);
    atomic_init(&rb->read_idx, 0);
    atomic_init(&rb->pushes_attempted, 0);
    atomic_init(&rb->pushes_successful, 0);
    atomic_init(&rb->pops_attempted, 0);
    atomic_init(&rb->pops_successful, 0);
    atomic_init(&rb->buffer_full_events, 0);
    atomic_init(&rb->buffer_empty_events, 0);
    
    /* Initialize buffer entries with pool pointers */
    for (size_t i = 0; i < RING_BUFFER_CAPACITY; i++) {
        rb->entries[i].data = rb->sample_pool[i];
        rb->entries[i].sample_count = 0;
        rb->entries[i].timestamp_us = 0;
        rb->entries[i].sequence_num = 0;
        rb->entries[i].buffer_id = i;
    }
}

/* Producer: Push IQ buffer entry (called by DMA thread on Core 0) */
static inline bool ring_buffer_push(lock_free_ring_buffer_t* rb, 
                                    const iq_buffer_entry_t* entry) {
    atomic_fetch_add_explicit(&rb->pushes_attempted, 1, memory_order_relaxed);
    
    /* Load current write index */
    const size_t write = atomic_load_explicit(&rb->write_idx, memory_order_relaxed);
    const size_t next_write = (write + 1) & RING_BUFFER_MASK;
    
    /* Check if buffer is full */
    if (next_write == atomic_load_explicit(&rb->read_idx, memory_order_acquire)) {
        atomic_fetch_add_explicit(&rb->buffer_full_events, 1, memory_order_relaxed);
        return false;
    }
    
    /* Copy entry data to ring buffer slot */
    rb->entries[write] = *entry;
    
    /* Update write index - release semantics ensure data is visible before index */
    atomic_store_explicit(&rb->write_idx, next_write, memory_order_release);
    
    atomic_fetch_add_explicit(&rb->pushes_successful, 1, memory_order_relaxed);
    return true;
}

/* Consumer: Pop IQ buffer entry (called by network thread on Core 1) */
static inline bool ring_buffer_pop(lock_free_ring_buffer_t* rb, 
                                   iq_buffer_entry_t* entry) {
    atomic_fetch_add_explicit(&rb->pops_attempted, 1, memory_order_relaxed);
    
    /* Load current read index */
    const size_t read = atomic_load_explicit(&rb->read_idx, memory_order_relaxed);
    
    /* Check if buffer is empty */
    if (read == atomic_load_explicit(&rb->write_idx, memory_order_acquire)) {
        atomic_fetch_add_explicit(&rb->buffer_empty_events, 1, memory_order_relaxed);
        return false;
    }
    
    /* Copy entry from ring buffer slot */
    *entry = rb->entries[read];
    
    /* Update read index - release semantics ensure we're done with data */
    atomic_store_explicit(&rb->read_idx, (read + 1) & RING_BUFFER_MASK, 
                         memory_order_release);
    
    atomic_fetch_add_explicit(&rb->pops_successful, 1, memory_order_relaxed);
    return true;
}

/* Get current buffer utilization (0.0 to 1.0) */
static inline double ring_buffer_utilization(const lock_free_ring_buffer_t* rb) {
    const size_t write = atomic_load_explicit(&rb->write_idx, memory_order_acquire);
    const size_t read = atomic_load_explicit(&rb->read_idx, memory_order_acquire);
    
    size_t used;
    if (write >= read) {
        used = write - read;
    } else {
        used = RING_BUFFER_CAPACITY - read + write;
    }
    
    return (double)used / (double)RING_BUFFER_CAPACITY;
}

/* Get number of entries currently in buffer */
static inline size_t ring_buffer_size(const lock_free_ring_buffer_t* rb) {
    const size_t write = atomic_load_explicit(&rb->write_idx, memory_order_acquire);
    const size_t read = atomic_load_explicit(&rb->read_idx, memory_order_acquire);
    
    if (write >= read) {
        return write - read;
    } else {
        return RING_BUFFER_CAPACITY - read + write;
    }
}

/* Check if buffer is empty */
static inline bool ring_buffer_empty(const lock_free_ring_buffer_t* rb) {
    return atomic_load_explicit(&rb->read_idx, memory_order_acquire) == 
           atomic_load_explicit(&rb->write_idx, memory_order_acquire);
}

/* Check if buffer is full */
static inline bool ring_buffer_full(const lock_free_ring_buffer_t* rb) {
    const size_t write = atomic_load_explicit(&rb->write_idx, memory_order_relaxed);
    const size_t next_write = (write + 1) & RING_BUFFER_MASK;
    return next_write == atomic_load_explicit(&rb->read_idx, memory_order_acquire);
}

/* Get performance statistics */
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

static inline ring_buffer_stats_t ring_buffer_get_stats(const lock_free_ring_buffer_t* rb) {
    ring_buffer_stats_t stats;
    
    stats.pushes_attempted = atomic_load_explicit(&rb->pushes_attempted, memory_order_relaxed);
    stats.pushes_successful = atomic_load_explicit(&rb->pushes_successful, memory_order_relaxed);
    stats.pops_attempted = atomic_load_explicit(&rb->pops_attempted, memory_order_relaxed);
    stats.pops_successful = atomic_load_explicit(&rb->pops_successful, memory_order_relaxed);
    stats.buffer_full_events = atomic_load_explicit(&rb->buffer_full_events, memory_order_relaxed);
    stats.buffer_empty_events = atomic_load_explicit(&rb->buffer_empty_events, memory_order_relaxed);
    
    stats.push_success_rate = stats.pushes_attempted > 0 ? 
        (double)stats.pushes_successful / (double)stats.pushes_attempted : 0.0;
    stats.pop_success_rate = stats.pops_attempted > 0 ? 
        (double)stats.pops_successful / (double)stats.pops_attempted : 0.0;
    
    stats.current_utilization = ring_buffer_utilization(rb);
    stats.current_size = ring_buffer_size(rb);
    
    return stats;
}

/* Reset statistics (for benchmarking) */
static inline void ring_buffer_reset_stats(lock_free_ring_buffer_t* rb) {
    atomic_store_explicit(&rb->pushes_attempted, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->pushes_successful, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->pops_attempted, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->pops_successful, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->buffer_full_events, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->buffer_empty_events, 0, memory_order_relaxed);
}

#endif /* LOCK_FREE_RING_BUFFER_H */