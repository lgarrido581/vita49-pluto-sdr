import React, { useEffect, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { Activity } from 'lucide-react'
import './SpectrumPlot.css'

export default function SpectrumPlotUPlot({ spectrumData, metadata, perfMonitor }) {
  const chartRef = useRef(null)
  const plotInstanceRef = useRef(null)
  const [maxHoldEnabled, setMaxHoldEnabled] = useState(false)
  const [maxHoldData, setMaxHoldData] = useState(null)

  // Track render performance
  useEffect(() => {
    if (perfMonitor && spectrumData) {
      const start = performance.now()

      setTimeout(() => {
        const renderTime = performance.now() - start
        perfMonitor.measureRender('SpectrumPlot', () => renderTime)
      }, 0)
    }
  }, [spectrumData, perfMonitor])

  // Update max hold data when new spectrum data arrives
  useEffect(() => {
    if (!maxHoldEnabled || !spectrumData || !spectrumData.spectrum) {
      return
    }

    setMaxHoldData(prevMaxHold => {
      if (!prevMaxHold || prevMaxHold.length !== spectrumData.spectrum.length) {
        return [...spectrumData.spectrum]
      }

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

  // Initialize uPlot chart
  useEffect(() => {
    if (!chartRef.current) return

    const sampleRate = metadata?.sample_rate_hz || 30e6
    const centerFreq = metadata?.center_freq_hz || 2.4e9

    const opts = {
      width: chartRef.current.clientWidth,
      height: 400,
      title: `Spectrum - ${(centerFreq / 1e9).toFixed(3)} GHz`,
      padding: [10, 10, 0, 0],
      cursor: {
        drag: { x: false, y: false }
      },
      legend: {
        show: maxHoldEnabled
      },
      scales: {
        x: {
          time: false,
          range: [-sampleRate / 2 / 1e6, sampleRate / 2 / 1e6]
        },
        y: {
          auto: true
        }
      },
      axes: [
        {
          label: 'Frequency Offset (MHz)',
          stroke: '#a0a8b8',
          grid: { stroke: '#2a3142', width: 1 }
        },
        {
          label: 'Power (dBFS)',
          stroke: '#a0a8b8',
          grid: { stroke: '#2a3142', width: 1 }
        }
      ],
      series: [
        {},
        {
          label: 'Current',
          stroke: '#10b981',
          width: 2,
          fill: 'rgba(16, 185, 129, 0.1)'
        }
      ]
    }

    // Add max hold series if enabled
    if (maxHoldEnabled) {
      opts.series.push({
        label: 'Max Hold',
        stroke: '#ef4444',
        width: 2,
        dash: [5, 5]
      })
    }

    plotInstanceRef.current = new uPlot(opts, [], chartRef.current)

    // Handle window resize
    const handleResize = () => {
      if (plotInstanceRef.current && chartRef.current) {
        plotInstanceRef.current.setSize({
          width: chartRef.current.clientWidth,
          height: 400
        })
      }
    }

    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      if (plotInstanceRef.current) {
        plotInstanceRef.current.destroy()
        plotInstanceRef.current = null
      }
    }
  }, [metadata, maxHoldEnabled])

  // Update chart data when spectrum data changes
  useEffect(() => {
    if (!plotInstanceRef.current || !spectrumData || !spectrumData.frequencies || !spectrumData.spectrum) {
      return
    }

    // Validate data arrays are not empty
    if (spectrumData.frequencies.length === 0 || spectrumData.spectrum.length === 0) {
      console.warn('Skipping uPlot update - empty data arrays')
      return
    }

    // Validate arrays have matching lengths
    if (spectrumData.frequencies.length !== spectrumData.spectrum.length) {
      console.warn('Skipping uPlot update - mismatched array lengths')
      return
    }

    try {
      const data = maxHoldEnabled && maxHoldData && maxHoldData.length > 0
        ? [spectrumData.frequencies, spectrumData.spectrum, maxHoldData]
        : [spectrumData.frequencies, spectrumData.spectrum]

      // Use requestAnimationFrame to ensure DOM is ready and avoid race conditions
      requestAnimationFrame(() => {
        if (plotInstanceRef.current) {
          plotInstanceRef.current.setData(data)
        }
      })
    } catch (err) {
      console.error('Error updating uPlot:', err)
    }
  }, [spectrumData, maxHoldData, maxHoldEnabled])

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

  return (
    <div className="spectrum-plot card">
      <div className="plot-header">
        <h3><Activity size={18} /> Spectrum Analyzer (uPlot)</h3>
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
      <div className="plot-container" ref={chartRef} style={{ width: '100%', height: '400px' }} />
    </div>
  )
}
