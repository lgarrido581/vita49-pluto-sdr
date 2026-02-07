import { useEffect, useRef, useState, useCallback } from 'react'

/**
 * Custom hook for WebSocket connection to VITA49 stream
 * Implements frame dropping and sequence number tracking for optimal performance
 */
export function useWebSocket(url, perfMonitor = null) {
  const [isConnected, setIsConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState(null)
  const [error, setError] = useState(null)
  const wsRef = useRef(null)
  const reconnectTimeoutRef = useRef(null)
  const handlersRef = useRef({})
  const isBacklogReconnectRef = useRef(false)

  // Sequence tracking to drop out-of-order messages
  const lastSequenceRef = useRef({})

  // Track message arrival rate to detect backlog issues
  const messageStatsRef = useRef({
    spectrum: { count: 0, lastTime: 0, droppedCount: 0 },
    waterfall: { count: 0, lastTime: 0, droppedCount: 0 }
  })

  // Latest message queue for high-frequency types (spectrum, waterfall)
  const latestMessagesRef = useRef({})
  const processingFrameRef = useRef(false)
  const isPageVisibleRef = useRef(true)

  // Store perfMonitor ref to avoid stale closures
  const perfMonitorRef = useRef(perfMonitor)
  useEffect(() => {
    perfMonitorRef.current = perfMonitor
  }, [perfMonitor])

  // Track page visibility to pause processing when hidden
  useEffect(() => {
    let backlogCheckTimeout = null

    const handleVisibilityChange = () => {
      const isVisible = !document.hidden
      const wasVisible = isPageVisibleRef.current
      isPageVisibleRef.current = isVisible

      if (!isVisible) {
        // Page is being hidden
        latestMessagesRef.current = {}
        console.log('🔒 Page hidden - cleared message queue', {
          wsConnected: wsRef.current?.readyState === WebSocket.OPEN,
          lastSequences: { ...lastSequenceRef.current }
        })

        // Clear any pending backlog check
        if (backlogCheckTimeout) {
          clearTimeout(backlogCheckTimeout)
          backlogCheckTimeout = null
        }
      } else if (!wasVisible && isVisible) {
        // Page is becoming visible after being hidden
        console.log('👁️ Page visible - resuming message processing', {
          wsConnected: wsRef.current?.readyState === WebSocket.OPEN,
          lastSequences: { ...lastSequenceRef.current },
          queuedMessages: Object.keys(latestMessagesRef.current)
        })

        // Check for backlog after 3 seconds
        // If we're still dropping lots of messages, reconnect to clear buffer
        backlogCheckTimeout = setTimeout(() => {
          const spectrumStats = messageStatsRef.current.spectrum
          const totalMessages = spectrumStats.count + spectrumStats.droppedCount

          if (totalMessages > 50 && spectrumStats.droppedCount > totalMessages * 0.3) {
            console.warn(`🔄 High backlog detected (${spectrumStats.droppedCount}/${totalMessages} dropped). Reconnecting to clear buffer...`)

            // Mark this as a backlog reconnect (for immediate reconnection)
            isBacklogReconnectRef.current = true

            // Force reconnect to clear WebSocket buffer
            if (wsRef.current) {
              wsRef.current.close()
              // The onclose handler will trigger reconnection
            }
          } else {
            console.log(`✅ Backlog check passed: ${spectrumStats.droppedCount}/${totalMessages} dropped`)
          }
        }, 3000)
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      if (backlogCheckTimeout) {
        clearTimeout(backlogCheckTimeout)
      }
    }
  }, [])

  // Register message handler for specific message types
  const on = useCallback((type, handler) => {
    if (!handlersRef.current[type]) {
      handlersRef.current[type] = []
    }
    handlersRef.current[type].push(handler)

    // Return cleanup function
    return () => {
      handlersRef.current[type] = handlersRef.current[type].filter(h => h !== handler)
    }
  }, [])

  // Send message through WebSocket
  const send = useCallback((data) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
      return true
    }
    return false
  }, [])

  // Connect to WebSocket
  const connect = useCallback(() => {
    try {
      const ws = new WebSocket(url)

      ws.onopen = () => {
        console.log('✅ WebSocket connected')
        setIsConnected(true)
        setError(null)

        // Initialize message stats timers
        const now = performance.now()
        messageStatsRef.current.spectrum.lastTime = now
        messageStatsRef.current.waterfall.lastTime = now
      }

      ws.onclose = () => {
        console.log('WebSocket disconnected')
        setIsConnected(false)

        // If this was a backlog reconnect, reconnect immediately
        // Otherwise wait 3 seconds (normal reconnection)
        const delay = isBacklogReconnectRef.current ? 100 : 3000
        isBacklogReconnectRef.current = false

        reconnectTimeoutRef.current = setTimeout(() => {
          console.log('Attempting to reconnect...')
          connect()
        }, delay)
      }

      ws.onerror = (event) => {
        console.error('WebSocket error:', event)
        setError('WebSocket connection error')
      }

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data)
          const { type, sequence, timestamp, data } = message
          const now = performance.now()

          // Track message arrival stats
          if (messageStatsRef.current[type]) {
            const stats = messageStatsRef.current[type]
            stats.count++

            // Log arrival rate every 5 seconds
            if (now - stats.lastTime > 5000) {
              const rate = stats.count / ((now - stats.lastTime) / 1000)
              console.debug(`📨 ${type} arrival: ${stats.count} msgs in 5s (${rate.toFixed(1)} Hz), dropped: ${stats.droppedCount}`)
              stats.count = 0
              stats.droppedCount = 0
              stats.lastTime = now
            }
          }

          // Track message receive time for performance monitoring
          if (perfMonitorRef.current?.trackMessageReceived) {
            perfMonitorRef.current.trackMessageReceived(type, sequence)
          }

          // Check sequence number to drop out-of-order messages
          if (sequence !== undefined) {
            const lastSeq = lastSequenceRef.current[type] || 0
            if (sequence < lastSeq) {
              // Track dropped messages
              if (messageStatsRef.current[type]) {
                messageStatsRef.current[type].droppedCount++
              }

              // If message is significantly behind (>50 messages), it's from a backlog
              // Log less frequently to avoid console spam
              const gap = lastSeq - sequence
              if (gap > 50 && sequence % 100 === 0) {
                console.debug(`⏭️ Dropping ${gap} old ${type} messages (backlog from hidden page)`)
              } else if (gap <= 50) {
                console.debug(`⏭️ Dropping out-of-order ${type} message: ${sequence} < ${lastSeq}`)
              }
              return
            }

            // Log sequence jumps (potential message loss or recovery)
            if (lastSeq > 0 && sequence > lastSeq + 10) {
              console.log(`⚡ ${type} sequence jump: ${lastSeq} -> ${sequence} (gap: ${sequence - lastSeq})`)
            }

            lastSequenceRef.current[type] = sequence
          }

          setLastMessage(message)

          // For high-frequency message types (spectrum, waterfall), queue the latest
          // and process in animation frame to prevent backlog
          if (type === 'spectrum' || type === 'waterfall') {
            // Only queue if page is visible or if this is fresh data
            // This prevents queueing during page visibility transitions
            if (isPageVisibleRef.current) {
              const wasEmpty = !latestMessagesRef.current[type]
              latestMessagesRef.current[type] = { data, metadata: { sequence, timestamp, type } }

              // Log first message after becoming visible (only once per visibility change)
              if (wasEmpty && sequence % 5 === 0) {
                console.debug(`✅ ${type} queued: seq=${sequence}`)
              }
            } else {
              // Count how many messages we're skipping while hidden
              if (sequence % 100 === 0) {
                console.debug(`🚫 Skipping ${type} while hidden: seq=${sequence}`)
              }
            }
          } else {
            // For low-frequency messages (status, metadata, config_applied), process immediately
            const handlers = handlersRef.current[type] || []
            handlers.forEach(handler => {
              try {
                handler(data, { sequence, timestamp })
              } catch (err) {
                console.error('Error in message handler:', err)
              }
            })
          }
        } catch (err) {
          console.error('Error parsing WebSocket message:', err)
        }
      }

      wsRef.current = ws
    } catch (err) {
      console.error('Error creating WebSocket:', err)
      setError(err.message)
    }
  }, [url])

  // Disconnect WebSocket
  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setIsConnected(false)
  }, [])

  // Process queued high-frequency messages in animation frame
  useEffect(() => {
    let frameId
    let lastProcessTime = 0
    let processCount = 0

    const processQueue = () => {
      const now = performance.now()

      // Only process if page is visible, not already processing, and has messages
      if (isPageVisibleRef.current &&
          !processingFrameRef.current &&
          Object.keys(latestMessagesRef.current).length > 0) {
        processingFrameRef.current = true
        processCount++

        // Log processing stats every 5 seconds
        if (now - lastProcessTime > 5000) {
          console.debug(`📊 RAF processing: ${processCount} frames in last 5s, queue: ${Object.keys(latestMessagesRef.current).join(', ')}`)
          lastProcessTime = now
          processCount = 0
        }

        // Process all queued messages
        Object.entries(latestMessagesRef.current).forEach(([type, { data, metadata }]) => {
          const handlers = handlersRef.current[type] || []
          if (handlers.length === 0) {
            console.warn(`⚠️ No handlers registered for ${type}`)
          }
          handlers.forEach(handler => {
            try {
              handler(data, metadata)
            } catch (err) {
              console.error(`❌ Error in ${type} handler:`, err)
            }
          })
        })

        // Clear the queue
        latestMessagesRef.current = {}
        processingFrameRef.current = false
      }

      // Continue processing loop
      frameId = requestAnimationFrame(processQueue)
    }

    console.log('🔄 Starting RAF processing loop')
    frameId = requestAnimationFrame(processQueue)

    return () => {
      if (frameId) {
        console.log('🛑 Stopping RAF processing loop')
        cancelAnimationFrame(frameId)
      }
    }
  }, [])

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    connect()
    return () => {
      disconnect()
    }
  }, [connect, disconnect])

  return {
    isConnected,
    lastMessage,
    error,
    send,
    on,
    connect,
    disconnect
  }
}
