import React, { useEffect, useRef } from 'react'
import { Waves } from 'lucide-react'
import './Waterfall.css'

export default function WaterfallCanvas({ waterfallData, metadata, perfMonitor }) {
  const canvasRef = useRef(null)
  const containerRef = useRef(null)

  // Track render performance
  useEffect(() => {
    if (perfMonitor && waterfallData) {
      const start = performance.now()

      setTimeout(() => {
        const renderTime = performance.now() - start
        perfMonitor.measureRender('Waterfall', () => renderTime)
      }, 0)
    }
  }, [waterfallData, perfMonitor])

  // Render waterfall to canvas
  useEffect(() => {
    if (!canvasRef.current || !waterfallData || !waterfallData.waterfall || waterfallData.waterfall.length === 0) {
      return
    }

    // Validate we have actual data in first row
    if (!waterfallData.waterfall[0] || waterfallData.waterfall[0].length === 0) {
      console.warn('Skipping canvas update - empty waterfall row')
      return
    }

    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d', { alpha: false })
    const container = containerRef.current

    if (!container) {
      return
    }

    try {
      // Use RAF to ensure timing is correct and avoid race conditions
      requestAnimationFrame(() => {
        if (!canvasRef.current || !containerRef.current) return

        // Set canvas size to match container
        const width = container.clientWidth
        const height = container.clientHeight

        if (width === 0 || height === 0) {
          console.warn('Skipping canvas update - zero dimensions')
          return
        }

    // Use device pixel ratio for crisp rendering on high-DPI displays
    const dpr = window.devicePixelRatio || 1
    canvas.width = width * dpr
    canvas.height = height * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`
    ctx.scale(dpr, dpr)

    const waterfall = waterfallData.waterfall
    const numLines = waterfall.length
    const numBins = waterfall[0].length

    // Calculate pixel dimensions
    const pixelWidth = width / numBins
    const pixelHeight = height / numLines

    // Find min/max for color scaling
    let minVal = Infinity
    let maxVal = -Infinity
    for (let i = 0; i < numLines; i++) {
      for (let j = 0; j < numBins; j++) {
        const val = waterfall[i][j]
        if (val < minVal) minVal = val
        if (val > maxVal) maxVal = val
      }
    }

    const range = maxVal - minVal || 1

    // Color mapping function (matches Plotly colorscale)
    const getColor = (value) => {
      const normalized = (value - minVal) / range

      if (normalized < 0.2) {
        // Dark blue to blue
        const t = normalized / 0.2
        return `rgb(${Math.floor(10 + (30 - 10) * t)}, ${Math.floor(14 + (58 - 14) * t)}, ${Math.floor(23 + (138 - 23) * t)})`
      } else if (normalized < 0.4) {
        // Blue to purple
        const t = (normalized - 0.2) / 0.2
        return `rgb(${Math.floor(30 + (124 - 30) * t)}, ${Math.floor(58 + (58 - 58) * t)}, ${Math.floor(138 + (237 - 138) * t)})`
      } else if (normalized < 0.6) {
        // Purple to red
        const t = (normalized - 0.4) / 0.2
        return `rgb(${Math.floor(124 + (220 - 124) * t)}, ${Math.floor(58 + (38 - 58) * t)}, ${Math.floor(237 + (38 - 237) * t)})`
      } else if (normalized < 0.8) {
        // Red to orange
        const t = (normalized - 0.6) / 0.2
        return `rgb(${Math.floor(220 + (245 - 220) * t)}, ${Math.floor(38 + (158 - 38) * t)}, ${Math.floor(38 + (11 - 38) * t)})`
      } else {
        // Orange to yellow
        const t = (normalized - 0.8) / 0.2
        return `rgb(${Math.floor(245 + (254 - 245) * t)}, ${Math.floor(158 + (240 - 158) * t)}, ${Math.floor(11 + (138 - 11) * t)})`
      }
    }

    // Render waterfall (bottom to top, newest at bottom)
    for (let i = 0; i < numLines; i++) {
      for (let j = 0; j < numBins; j++) {
        const value = waterfall[i][j]
        ctx.fillStyle = getColor(value)

        // Draw pixel (reversed y-axis so newest is at bottom)
        const x = j * pixelWidth
        const y = (numLines - 1 - i) * pixelHeight
        ctx.fillRect(x, y, Math.ceil(pixelWidth) + 1, Math.ceil(pixelHeight) + 1)
      }
    }

    // Add frequency labels
    const sampleRate = metadata?.sample_rate_hz || 30e6
    const freqMin = -sampleRate / 2 / 1e6
    const freqMax = sampleRate / 2 / 1e6

    ctx.fillStyle = '#a0a8b8'
    ctx.font = '11px Inter, sans-serif'
    ctx.textAlign = 'center'

    // Draw frequency markers
    const numMarkers = 5
    for (let i = 0; i < numMarkers; i++) {
      const freq = freqMin + (freqMax - freqMin) * i / (numMarkers - 1)
      const x = width * i / (numMarkers - 1)
      ctx.fillText(`${freq.toFixed(1)}`, x, height - 5)
    }
      })
    } catch (err) {
      console.error('Error rendering waterfall canvas:', err)
    }
  }, [waterfallData, metadata])

  // Handle window resize
  useEffect(() => {
    const handleResize = () => {
      // Trigger re-render by updating a dummy state or just rely on the effect above
      if (canvasRef.current && waterfallData) {
        // The effect will re-run when dependencies change
      }
    }

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [waterfallData])

  if (!waterfallData || !waterfallData.waterfall || waterfallData.waterfall.length === 0) {
    return (
      <div className="waterfall card">
        <div className="plot-header">
          <h3><Waves size={18} /> Waterfall</h3>
        </div>
        <div className="plot-placeholder">
          <Waves size={48} className="pulse" />
          <p>Waiting for waterfall data...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="waterfall card">
      <div className="plot-header">
        <h3><Waves size={18} /> Waterfall (Canvas)</h3>
        <span className="info-text">
          {waterfallData.waterfall.length} lines
        </span>
      </div>
      <div className="plot-container" ref={containerRef} style={{ width: '100%', height: '400px', position: 'relative' }}>
        <canvas
          ref={canvasRef}
          style={{
            display: 'block',
            width: '100%',
            height: '100%',
            imageRendering: 'pixelated'
          }}
        />
      </div>
    </div>
  )
}
