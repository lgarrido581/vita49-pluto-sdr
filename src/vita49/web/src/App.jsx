import React, { useState, useEffect } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import ControlPanel from './components/ControlPanel'
import SpectrumPlot from './components/SpectrumPlot'
import Waterfall from './components/Waterfall'
import PacketInspector from './components/PacketInspector'
import Statistics from './components/Statistics'
import { Radio, AlertCircle } from 'lucide-react'
import './App.css'

function App() {
  const [status, setStatus] = useState(null)
  const [spectrumData, setSpectrumData] = useState(null)
  const [waterfallData, setWaterfallData] = useState(null)
  const [metadata, setMetadata] = useState(null)
  const [statistics, setStatistics] = useState(null)

  // WebSocket connection
  const wsUrl = `ws://${window.location.hostname}:8000/ws/stream`
  const ws = useWebSocket(wsUrl)

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
      ws.on('spectrum', (data) => {
        setSpectrumData(data)
      }),
      ws.on('waterfall', (data) => {
        setWaterfallData(data)
      })
    ]

    return () => cleanups.forEach(cleanup => cleanup())
  }, [ws])

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
          />
        </aside>

        {/* Center - Plots */}
        <section className="main-content">
          <div className="plot-grid">
            <div className="plot-item spectrum">
              <SpectrumPlot spectrumData={spectrumData} metadata={metadata} />
            </div>
            <div className="plot-item waterfall">
              <Waterfall waterfallData={waterfallData} metadata={metadata} />
            </div>
          </div>

          {/* Statistics */}
          <div className="statistics-section card">
            <Statistics statistics={statistics} metadata={metadata} />
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
