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

  // Sequence tracking to drop out-of-order messages
  const lastSequenceRef = useRef({})

  // Latest message queue for high-frequency types (spectrum, waterfall)
  const latestMessagesRef = useRef({})
  const processingFrameRef = useRef(false)

  // Store perfMonitor ref to avoid stale closures
  const perfMonitorRef = useRef(perfMonitor)
  useEffect(() => {
    perfMonitorRef.current = perfMonitor
  }, [perfMonitor])

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
        console.log('WebSocket connected')
        setIsConnected(true)
        setError(null)
      }

      ws.onclose = () => {
        console.log('WebSocket disconnected')
        setIsConnected(false)

        // Attempt reconnection after 3 seconds
        reconnectTimeoutRef.current = setTimeout(() => {
          console.log('Attempting to reconnect...')
          connect()
        }, 3000)
      }

      ws.onerror = (event) => {
        console.error('WebSocket error:', event)
        setError('WebSocket connection error')
      }

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data)
          const { type, sequence, timestamp, data } = message

          // Track message receive time for performance monitoring
          if (perfMonitorRef.current?.trackMessageReceived) {
            perfMonitorRef.current.trackMessageReceived(type, sequence)
          }

          // Check sequence number to drop out-of-order messages
          if (sequence !== undefined) {
            const lastSeq = lastSequenceRef.current[type] || 0
            if (sequence < lastSeq) {
              console.debug(`Dropping out-of-order ${type} message: ${sequence} < ${lastSeq}`)
              return
            }
            lastSequenceRef.current[type] = sequence
          }

          setLastMessage(message)

          // For high-frequency message types (spectrum, waterfall), queue the latest
          // and process in animation frame to prevent backlog
          if (type === 'spectrum' || type === 'waterfall') {
            latestMessagesRef.current[type] = { data, metadata: { sequence, timestamp, type } }
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

    const processQueue = () => {
      // Only process if not already processing to avoid re-entrant calls
      if (!processingFrameRef.current && Object.keys(latestMessagesRef.current).length > 0) {
        processingFrameRef.current = true

        // Process all queued messages
        Object.entries(latestMessagesRef.current).forEach(([type, { data, metadata }]) => {
          const handlers = handlersRef.current[type] || []
          handlers.forEach(handler => {
            try {
              handler(data, metadata)
            } catch (err) {
              console.error(`Error in ${type} handler:`, err)
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

    frameId = requestAnimationFrame(processQueue)

    return () => {
      if (frameId) {
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
