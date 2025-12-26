import React, { useState, useEffect } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import { usePerformanceMonitor } from './hooks/usePerformanceMonitor'
import ControlPanel from './components/ControlPanel'
import SpectrumPlotUPlot from './components/SpectrumPlotUPlot'
import WaterfallCanvas from './components/WaterfallCanvas'
import PacketInspector from './components/PacketInspector'
import Statistics from './components/Statistics'
import PerformanceStats from './components/PerformanceStats'
import { Radio, AlertCircle } from 'lucide-react'
import './App.css'

function App() {
  const [status, setStatus] = useState(null)
  const [spectrumData, setSpectrumData] = useState(null)
  const [waterfallData, setWaterfallData] = useState(null)
  const [metadata, setMetadata] = useState(null)
  const [statistics, setStatistics] = useState(null)
  const [isPageVisible, setIsPageVisible] = useState(true)

  // Performance monitoring (create before WebSocket so it can be passed in)
  const perfMonitor = usePerformanceMonitor(true)

  // WebSocket connection (pass perfMonitor for latency tracking)
  const wsUrl = `ws://${window.location.hostname}:8001/ws/stream`
  const ws = useWebSocket(wsUrl, perfMonitor)

  // Track page visibility to prevent buffering when tab is not active
  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsPageVisible(!document.hidden)
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  // Register WebSocket message handlers
  useEffect(() => {
    const cleanups = [
      ws.on('status', (data) => {
        setStatus(data)
        if (data.metadata) setMetadata(data.metadata)
        if (data.statistics) setStatistics(data.statistics)
      }),
      ws.on('metadata', (data) => {
        setMetadata(data)
      }),
      ws.on('spectrum', (data, metadata) => {
        // Track message processing time for performance monitoring
        perfMonitor.trackMessage(metadata?.type, metadata?.sequence)

        // Only update spectrum when page is visible to prevent buffering
        if (isPageVisible) {
          setSpectrumData(data)
        }
      }),
      ws.on('waterfall', (data, metadata) => {
        // Track message processing time for performance monitoring
        perfMonitor.trackMessage(metadata?.type, metadata?.sequence)

        // Only update waterfall when page is visible to prevent buffering
        if (isPageVisible) {
          setWaterfallData(data)
        }
      })
    ]

    return () => cleanups.forEach(cleanup => cleanup())
  }, [ws, isPageVisible, perfMonitor])

  // Fetch status periodically
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const response = await fetch('/api/status')
        const data = await response.json()
        setStatus(data)
        if (data.metadata) setMetadata(data.metadata)
        if (data.statistics) setStatistics(data.statistics)
      } catch (err) {
        console.error('Error fetching status:', err)
      }
    }

    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleConfigChange = async (config) => {
    try {
      const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
      })
      const data = await response.json()
      if (data.success) {
        console.log('Configuration updated successfully')
      }
    } catch (err) {
      console.error('Error updating configuration:', err)
    }
  }

  const handleStreamControl = async (action) => {
    try {
      const endpoint = action === 'start' ? '/api/stream/start' : '/api/stream/stop'
      const response = await fetch(endpoint, { method: 'POST' })
      const data = await response.json()
      if (data.success) {
        console.log(`Stream ${action}ed successfully`)
      }
    } catch (err) {
      console.error(`Error ${action}ing stream:`, err)
    }
  }

  return (
    <div className="app">
      {/* Header */}
      <header className="app-header">
        <div className="header-content">
          <div className="logo">
            <Radio size={32} className="logo-icon" />
            <div>
              <h1>VITA49 Pluto SDR</h1>
              <p>Web Interface for Real-Time IQ Streaming</p>
            </div>
          </div>
          <div className="connection-indicator">
            {ws.isConnected ? (
              <div className="indicator connected">
                <div className="pulse-dot"></div>
                WebSocket Connected
              </div>
            ) : (
              <div className="indicator disconnected">
                <AlertCircle size={16} />
                Disconnected
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="app-main">
        {/* Left Sidebar - Controls */}
        <aside className="sidebar">
          <ControlPanel
            onConfigChange={handleConfigChange}
            onStreamControl={handleStreamControl}
            status={status}
            websocket={ws}
          />
        </aside>

        {/* Center - Plots */}
        <section className="main-content">
          <div className="plot-grid">
            <div className="plot-item spectrum">
              <SpectrumPlotUPlot spectrumData={spectrumData} metadata={metadata} perfMonitor={perfMonitor} />
            </div>
            <div className="plot-item waterfall">
              <WaterfallCanvas waterfallData={waterfallData} metadata={metadata} perfMonitor={perfMonitor} />
            </div>
          </div>

          {/* Statistics */}
          <div className="statistics-section card">
            <Statistics statistics={statistics} metadata={metadata} />
          </div>

          {/* Performance Monitor */}
          <div className="performance-section">
            <PerformanceStats
              stats={perfMonitor.stats}
              componentStats={{
                'Spectrum': perfMonitor.getComponentStats('SpectrumPlot'),
                'Waterfall': perfMonitor.getComponentStats('Waterfall')
              }}
              onReset={perfMonitor.reset}
            />
          </div>

          {/* Packet Inspector */}
          <div className="packet-section">
            <PacketInspector />
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="app-footer">
        <p>
          VITA49 Pluto Web UI v1.0.0 |
          Built with React + FastAPI |
          <a href="https://github.com/yourusername/vita49-pluto" target="_blank" rel="noopener noreferrer">
            View on GitHub
          </a>
        </p>
      </footer>
    </div>
  )
}

export default App
