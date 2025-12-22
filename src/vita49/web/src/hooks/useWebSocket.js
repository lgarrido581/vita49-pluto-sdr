import { useEffect, useRef, useState, useCallback } from 'react'

/**
 * Custom hook for WebSocket connection to VITA49 stream
 */
export function useWebSocket(url) {
  const [isConnected, setIsConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState(null)
  const [error, setError] = useState(null)
  const wsRef = useRef(null)
  const reconnectTimeoutRef = useRef(null)
  const handlersRef = useRef({})

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
          setLastMessage(message)

          // Call registered handlers for this message type
          const handlers = handlersRef.current[message.type] || []
          handlers.forEach(handler => {
            try {
              handler(message.data)
            } catch (err) {
              console.error('Error in message handler:', err)
            }
          })
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
