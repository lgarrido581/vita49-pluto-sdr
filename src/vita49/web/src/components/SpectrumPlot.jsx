import React, { useEffect, useRef, useState } from 'react'
import Plot from 'react-plotly.js'
import { Activity } from 'lucide-react'
import './SpectrumPlot.css'

export default function SpectrumPlot({ spectrumData, metadata }) {
  const [layout, setLayout] = useState(null)
  const [config] = useState({
    displayModeBar: false,
    responsive: true
  })

  useEffect(() => {
    if (!metadata) return

    const sampleRate = metadata.sample_rate_hz || 30e6
    const centerFreq = metadata.center_freq_hz || 2.4e9

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
      showlegend: false
    })
  }, [metadata])

  if (!spectrumData || !spectrumData.frequencies || !spectrumData.spectrum || !layout) {
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
    type: 'scatter',
    mode: 'lines',
    line: {
      color: '#10b981',
      width: 1.5
    },
    fill: 'tozeroy',
    fillcolor: 'rgba(16, 185, 129, 0.1)',
    hovertemplate: '%{x:.2f} MHz<br>%{y:.1f} dBFS<extra></extra>'
  }

  return (
    <div className="spectrum-plot card">
      <div className="plot-header">
        <h3><Activity size={18} /> Spectrum Analyzer</h3>
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
