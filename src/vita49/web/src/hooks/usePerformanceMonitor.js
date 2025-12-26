import { useEffect, useRef, useState } from 'react'

/**
 * Performance monitoring hook
 * Tracks FPS, render times, memory usage, and network stats
 */
export function usePerformanceMonitor(enabled = true) {
  const [stats, setStats] = useState({
    fps: 0,
    avgFrameTime: 0,
    maxFrameTime: 0,
    minFrameTime: Infinity,
    renderCount: 0,
    droppedFrames: 0,
    memoryUsage: 0,
    messagesPerSec: 0,
    avgMessageLatency: 0,
    totalMessages: 0
  })

  const frameTimesRef = useRef([])
  const lastFrameTimeRef = useRef(performance.now())
  const frameCountRef = useRef(0)
  const messageCountRef = useRef(0)
  const lastStatsUpdateRef = useRef(performance.now())
  const messageLatenciesRef = useRef([])
  const renderTimesRef = useRef([])

  // FPS monitoring
  useEffect(() => {
    if (!enabled) return

    let frameId
    let lastTime = performance.now()

    const measureFPS = () => {
      const now = performance.now()
      const frameTime = now - lastTime
      lastTime = now

      frameTimesRef.current.push(frameTime)
      frameCountRef.current++

      // Keep only last 60 frames
      if (frameTimesRef.current.length > 60) {
        frameTimesRef.current.shift()
      }

      // Update stats every second
      if (now - lastStatsUpdateRef.current >= 1000) {
        const frameTimes = frameTimesRef.current
        const avgFrameTime = frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length
        const maxFrameTime = Math.max(...frameTimes)
        const minFrameTime = Math.min(...frameTimes)
        const fps = 1000 / avgFrameTime

        // Dropped frames = frames that took >16.67ms (60 FPS threshold)
        const droppedFrames = frameTimes.filter(t => t > 16.67).length

        // Memory usage (if available)
        const memoryUsage = performance.memory
          ? (performance.memory.usedJSHeapSize / 1048576).toFixed(1)
          : 0

        // Message rate
        const messagesPerSec = messageCountRef.current
        messageCountRef.current = 0

        // Average message latency
        const avgMessageLatency = messageLatenciesRef.current.length > 0
          ? messageLatenciesRef.current.reduce((a, b) => a + b, 0) / messageLatenciesRef.current.length
          : 0
        messageLatenciesRef.current = []

        setStats(prev => ({
          fps: Math.round(fps),
          avgFrameTime: avgFrameTime.toFixed(2),
          maxFrameTime: maxFrameTime.toFixed(2),
          minFrameTime: minFrameTime.toFixed(2),
          renderCount: frameCountRef.current,
          droppedFrames,
          memoryUsage,
          messagesPerSec,
          avgMessageLatency: avgMessageLatency.toFixed(2),
          totalMessages: prev.totalMessages + messagesPerSec
        }))

        lastStatsUpdateRef.current = now
      }

      frameId = requestAnimationFrame(measureFPS)
    }

    frameId = requestAnimationFrame(measureFPS)

    return () => {
      if (frameId) {
        cancelAnimationFrame(frameId)
      }
    }
  }, [enabled])

  // Track component render time
  const measureRender = (componentName, renderFn) => {
    if (!enabled) {
      return renderFn()
    }

    const start = performance.now()
    const result = renderFn()
    const duration = performance.now() - start

    renderTimesRef.current.push({
      component: componentName,
      duration,
      timestamp: Date.now()
    })

    // Keep only last 100 render measurements
    if (renderTimesRef.current.length > 100) {
      renderTimesRef.current.shift()
    }

    return result
  }

  // Track message processing
  const trackMessage = (messageTimestamp) => {
    if (!enabled) return

    messageCountRef.current++

    if (messageTimestamp) {
      const latency = Date.now() - messageTimestamp * 1000
      messageLatenciesRef.current.push(latency)
    }
  }

  // Get detailed render stats for specific component
  const getComponentStats = (componentName) => {
    const renders = renderTimesRef.current.filter(r => r.component === componentName)
    if (renders.length === 0) return null

    const times = renders.map(r => r.duration)
    return {
      count: renders.length,
      avg: (times.reduce((a, b) => a + b, 0) / times.length).toFixed(2),
      max: Math.max(...times).toFixed(2),
      min: Math.min(...times).toFixed(2),
      last: times[times.length - 1].toFixed(2)
    }
  }

  // Reset stats
  const reset = () => {
    frameTimesRef.current = []
    frameCountRef.current = 0
    messageCountRef.current = 0
    messageLatenciesRef.current = []
    renderTimesRef.current = []
    setStats({
      fps: 0,
      avgFrameTime: 0,
      maxFrameTime: 0,
      minFrameTime: Infinity,
      renderCount: 0,
      droppedFrames: 0,
      memoryUsage: 0,
      messagesPerSec: 0,
      avgMessageLatency: 0,
      totalMessages: 0
    })
  }

  return {
    stats,
    measureRender,
    trackMessage,
    getComponentStats,
    reset
  }
}
