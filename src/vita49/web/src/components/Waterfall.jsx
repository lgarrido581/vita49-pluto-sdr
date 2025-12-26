import React, { useEffect, useState } from 'react'
import Plot from 'react-plotly.js'
import { Waves } from 'lucide-react'
import './Waterfall.css'

export default function Waterfall({ waterfallData, metadata, perfMonitor }) {
  // Initialize layout with default values immediately
  const [layout, setLayout] = useState({
    autosize: true,
    margin: { l: 50, r: 60, t: 40, b: 50 },
    paper_bgcolor: '#131820',
    plot_bgcolor: '#0a0e17',
    font: {
      family: 'Inter, sans-serif',
      size: 11,
      color: '#a0a8b8'
    },
    title: {
      text: 'Waterfall (Spectrogram)',
      font: { size: 14, color: '#e8eaf0' },
      x: 0.05,
      xanchor: 'left'
    },
    xaxis: {
      title: 'Frequency Offset (MHz)',
      gridcolor: '#2a3142',
      color: '#a0a8b8',
      range: [-15, 15]  // Default range for 30 MHz
    },
    yaxis: {
      title: 'Time',
      gridcolor: '#2a3142',
      color: '#a0a8b8',
      autorange: 'reversed'
    }
  })
  const [config] = useState({
    displayModeBar: false,
    responsive: true
  })

  // Track render performance
  useEffect(() => {
    if (perfMonitor && waterfallData) {
      const start = performance.now()

      // Use setTimeout to measure after React finishes rendering
      setTimeout(() => {
        const renderTime = performance.now() - start
        perfMonitor.measureRender('Waterfall', () => renderTime)
      }, 0)
    }
  }, [waterfallData, perfMonitor])

  // Update layout when metadata becomes available
  useEffect(() => {
    const sampleRate = metadata?.sample_rate_hz || 30e6

    setLayout({
      autosize: true,
      margin: { l: 50, r: 60, t: 40, b: 50 },
      paper_bgcolor: '#131820',
      plot_bgcolor: '#0a0e17',
      font: {
        family: 'Inter, sans-serif',
        size: 11,
        color: '#a0a8b8'
      },
      title: {
        text: 'Waterfall (Spectrogram)',
        font: { size: 14, color: '#e8eaf0' },
        x: 0.05,
        xanchor: 'left'
      },
      xaxis: {
        title: 'Frequency Offset (MHz)',
        gridcolor: '#2a3142',
        color: '#a0a8b8',
        range: [-sampleRate / 2 / 1e6, sampleRate / 2 / 1e6]
      },
      yaxis: {
        title: 'Time',
        gridcolor: '#2a3142',
        color: '#a0a8b8',
        autorange: 'reversed'
      }
    })
  }, [metadata])

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

  // Compute frequency bins for x-axis
  const sampleRate = metadata?.sample_rate_hz || 30e6
  const numBins = waterfallData.waterfall[0].length
  const freqBins = Array.from({ length: numBins }, (_, i) => {
    return ((i - numBins / 2) * sampleRate / numBins) / 1e6
  })

  const trace = {
    z: waterfallData.waterfall,
    x: freqBins,
    type: 'heatmapgl',  // WebGL accelerated
    colorscale: [
      [0, '#0a0e17'],
      [0.2, '#1e3a8a'],
      [0.4, '#7c3aed'],
      [0.6, '#dc2626'],
      [0.8, '#f59e0b'],
      [1, '#fef08a']
    ],
    showscale: true,
    colorbar: {
      title: 'Power<br>(dBFS)',
      titleside: 'right',
      tickfont: { size: 10 },
      len: 0.7,
      bgcolor: '#131820',
      bordercolor: '#2a3142',
      borderwidth: 1
    },
    hovertemplate: '%{x:.2f} MHz<br>%{z:.1f} dBFS<extra></extra>'
  }

  return (
    <div className="waterfall card">
      <div className="plot-header">
        <h3><Waves size={18} /> Waterfall</h3>
        <span className="info-text">
          {waterfallData.waterfall.length} lines
        </span>
      </div>
      <div className="plot-container">
        <Plot
          data={[trace]}
          layout={layout}
          config={config}
          style={{ width: '100%', height: '100%' }}
          useResizeHandler={true}
        />
      </div>
    </div>
  )
}
