import React, { useEffect, useRef, useState } from 'react'
import Plot from 'react-plotly.js'
import { Activity } from 'lucide-react'
import './SpectrumPlot.css'

export default function SpectrumPlot({ spectrumData, metadata, perfMonitor }) {
  const renderStartRef = useRef(null)
  // Initialize layout with default values immediately
  const [layout, setLayout] = useState({
    autosize: true,
    margin: { l: 50, r: 20, t: 40, b: 50 },
    paper_bgcolor: '#131820',
    plot_bgcolor: '#0a0e17',
    font: {
      family: 'Inter, sans-serif',
      size: 11,
      color: '#a0a8b8'
    },
    title: {
      text: 'Spectrum',
      font: { size: 14, color: '#e8eaf0' },
      x: 0.05,
      xanchor: 'left'
    },
    xaxis: {
      title: 'Frequency Offset (MHz)',
      gridcolor: '#2a3142',
      zerolinecolor: '#2a3142',
      color: '#a0a8b8',
      range: [-15, 15]  // Default range for 30 MHz
    },
    yaxis: {
      title: 'Power (dBFS)',
      gridcolor: '#2a3142',
      zerolinecolor: '#2a3142',
      color: '#a0a8b8'
    },
    hovermode: 'closest',
    showlegend: false
  })
  const [config] = useState({
    displayModeBar: false,
    responsive: true
  })
  const [maxHoldEnabled, setMaxHoldEnabled] = useState(false)
  const [maxHoldData, setMaxHoldData] = useState(null)

  // Update layout when metadata becomes available
  useEffect(() => {
    const sampleRate = metadata?.sample_rate_hz || 30e6
    const centerFreq = metadata?.center_freq_hz || 2.4e9

    setLayout({
      autosize: true,
      margin: { l: 50, r: 20, t: 40, b: 50 },
      paper_bgcolor: '#131820',
      plot_bgcolor: '#0a0e17',
      font: {
        family: 'Inter, sans-serif',
        size: 11,
        color: '#a0a8b8'
      },
      title: {
        text: `Spectrum - ${(centerFreq / 1e9).toFixed(3)} GHz`,
        font: { size: 14, color: '#e8eaf0' },
        x: 0.05,
        xanchor: 'left'
      },
      xaxis: {
        title: 'Frequency Offset (MHz)',
        gridcolor: '#2a3142',
        zerolinecolor: '#2a3142',
        color: '#a0a8b8',
        range: [-sampleRate / 2 / 1e6, sampleRate / 2 / 1e6]
      },
      yaxis: {
        title: 'Power (dBFS)',
        gridcolor: '#2a3142',
        zerolinecolor: '#2a3142',
        color: '#a0a8b8'
      },
      hovermode: 'closest',
      showlegend: maxHoldEnabled
    })
  }, [metadata, maxHoldEnabled])

  // Update max hold data when new spectrum data arrives
  useEffect(() => {
    if (!maxHoldEnabled || !spectrumData || !spectrumData.spectrum) {
      return
    }

    setMaxHoldData(prevMaxHold => {
      if (!prevMaxHold || prevMaxHold.length !== spectrumData.spectrum.length) {
        // Initialize max hold with current spectrum
        return [...spectrumData.spectrum]
      }

      // Update max hold by taking maximum of current and previous values
      return prevMaxHold.map((maxVal, idx) =>
        Math.max(maxVal, spectrumData.spectrum[idx])
      )
    })
  }, [spectrumData, maxHoldEnabled])

  // Reset max hold data when disabled
  useEffect(() => {
    if (!maxHoldEnabled) {
      setMaxHoldData(null)
    }
  }, [maxHoldEnabled])

  // Track render performance
  useEffect(() => {
    if (perfMonitor && spectrumData) {
      const start = performance.now()

      // Use setTimeout to measure after React finishes rendering
      setTimeout(() => {
        const renderTime = performance.now() - start
        perfMonitor.measureRender('SpectrumPlot', () => renderTime)
      }, 0)
    }
  }, [spectrumData, perfMonitor])

  const handleMaxHoldToggle = () => {
    setMaxHoldEnabled(!maxHoldEnabled)
  }

  const handleMaxHoldReset = () => {
    setMaxHoldData(null)
  }

  if (!spectrumData || !spectrumData.frequencies || !spectrumData.spectrum) {
    return (
      <div className="spectrum-plot card">
        <div className="plot-header">
          <h3><Activity size={18} /> Spectrum Analyzer</h3>
        </div>
        <div className="plot-placeholder">
          <Activity size={48} className="pulse" />
          <p>Waiting for spectrum data...</p>
        </div>
      </div>
    )
  }

  const trace = {
    x: spectrumData.frequencies,
    y: spectrumData.spectrum,
    type: 'scattergl',  // WebGL accelerated
    mode: 'lines',
    name: 'Current',
    line: {
      color: '#10b981',
      width: 1.5
    },
    fill: 'tozeroy',
    fillcolor: 'rgba(16, 185, 129, 0.1)',
    hovertemplate: '%{x:.2f} MHz<br>%{y:.1f} dBFS<extra></extra>'
  }

  const maxHoldTrace = maxHoldEnabled && maxHoldData ? {
    x: spectrumData.frequencies,
    y: maxHoldData,
    type: 'scattergl',  // WebGL accelerated
    mode: 'lines',
    name: 'Max Hold',
    line: {
      color: '#ef4444',
      width: 1.5,
      dash: 'dot'
    },
    hovertemplate: '%{x:.2f} MHz<br>%{y:.1f} dBFS<extra></extra>'
  } : null

  const traces = maxHoldTrace ? [trace, maxHoldTrace] : [trace]

  return (
    <div className="spectrum-plot card">
      <div className="plot-header">
        <h3><Activity size={18} /> Spectrum Analyzer</h3>
        <div className="max-hold-controls">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={maxHoldEnabled}
              onChange={handleMaxHoldToggle}
            />
            Max Hold
          </label>
          {maxHoldEnabled && (
            <button
              className="reset-button"
              onClick={handleMaxHoldReset}
              title="Reset max hold"
            >
              Reset
            </button>
          )}
        </div>
        <div className="signal-indicators">
          {spectrumData.peak_power_db && (
            <span className="indicator">
              Peak: {spectrumData.peak_power_db.toFixed(1)} dB
            </span>
          )}
          {spectrumData.noise_floor_db && (
            <span className="indicator">
              Noise: {spectrumData.noise_floor_db.toFixed(1)} dB
            </span>
          )}
          {spectrumData.peak_power_db && spectrumData.noise_floor_db && (
            <span className="indicator highlight">
              SNR: {(spectrumData.peak_power_db - spectrumData.noise_floor_db).toFixed(1)} dB
            </span>
          )}
        </div>
      </div>
      <div className="plot-container">
        <Plot
          data={traces}
          layout={layout}
          config={config}
          style={{ width: '100%', height: '100%' }}
          useResizeHandler={true}
        />
      </div>
    </div>
  )
}
