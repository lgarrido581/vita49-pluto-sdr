import React, { useState, useEffect } from 'react'
import { Play, Pause, Settings, Radio, Gauge, Sliders } from 'lucide-react'
import './ControlPanel.css'

export default function ControlPanel({ onConfigChange, onStreamControl, status }) {
  const [config, setConfig] = useState({
    pluto_uri: 'ip:192.168.2.1',
    center_freq_hz: 2.4e9,
    sample_rate_hz: 30e6,
    bandwidth_hz: 20e6,
    rx_gain_db: 20.0
  })

  const [isStreaming, setIsStreaming] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [configInitialized, setConfigInitialized] = useState(false)

  useEffect(() => {
    if (status) {
      setIsStreaming(status.streaming)
      // Only update config from metadata on initial load, not on every status update
      if (status.metadata && status.metadata.context_received && !configInitialized) {
        setConfig(prev => ({
          ...prev,
          center_freq_hz: status.metadata.center_freq_hz,
          sample_rate_hz: status.metadata.sample_rate_hz,
          bandwidth_hz: status.metadata.bandwidth_hz,
          rx_gain_db: status.metadata.gain_db
        }))
        setConfigInitialized(true)
      }
    }
  }, [status, configInitialized])

  const handleChange = (field, value) => {
    setConfig(prev => ({ ...prev, [field]: value }))
  }

  const handleApply = () => {
    onConfigChange(config)
  }

  const handleStreamToggle = () => {
    onStreamControl(isStreaming ? 'stop' : 'start')
  }

  const formatFreq = (hz) => {
    if (hz >= 1e9) return `${(hz / 1e9).toFixed(3)} GHz`
    if (hz >= 1e6) return `${(hz / 1e6).toFixed(1)} MHz`
    return `${(hz / 1e3).toFixed(0)} kHz`
  }

  const formatRate = (hz) => {
    if (hz >= 1e6) return `${(hz / 1e6).toFixed(1)} MSPS`
    return `${(hz / 1e3).toFixed(0)} kSPS`
  }

  const presets = [
    { name: 'WiFi 2.4 GHz', freq: 2.437e9, rate: 20e6, bw: 20e6, gain: 30 },
    { name: 'FM Radio', freq: 103.7e6, rate: 2e6, bw: 200e3, gain: 40 },
    { name: 'GPS L1', freq: 1.57542e9, rate: 4e6, bw: 2e6, gain: 35 },
    { name: 'LTE Band 7', freq: 2.6e9, rate: 30e6, bw: 20e6, gain: 25 }
  ]

  const applyPreset = (preset) => {
    setConfig(prev => ({
      ...prev,
      center_freq_hz: preset.freq,
      sample_rate_hz: preset.rate,
      bandwidth_hz: preset.bw,
      rx_gain_db: preset.gain
    }))
  }

  return (
    <div className="control-panel card">
      <div className="control-header">
        <h2><Settings size={20} /> Control Panel</h2>
        <button
          className={`stream-toggle-btn ${isStreaming ? 'streaming' : ''}`}
          onClick={handleStreamToggle}
        >
          {isStreaming ? <Pause size={18} /> : <Play size={18} />}
          {isStreaming ? 'Stop Stream' : 'Start Stream'}
        </button>
      </div>

      {/* Quick Presets */}
      <div className="presets">
        <label>Quick Presets:</label>
        <div className="preset-buttons">
          {presets.map((preset, idx) => (
            <button
              key={idx}
              className="preset-btn"
              onClick={() => applyPreset(preset)}
              title={`${formatFreq(preset.freq)} @ ${formatRate(preset.rate)}`}
            >
              {preset.name}
            </button>
          ))}
        </div>
      </div>

      {/* Main Controls */}
      <div className="control-section">
        <div className="control-group">
          <label>
            <Radio size={16} />
            Center Frequency
          </label>
          <div className="input-with-display">
            <input
              type="range"
              min={70e6}
              max={6e9}
              step={1e6}
              value={config.center_freq_hz}
              onChange={(e) => handleChange('center_freq_hz', parseFloat(e.target.value))}
            />
            <div className="value-display">{formatFreq(config.center_freq_hz)}</div>
          </div>
          <input
            type="number"
            className="number-input"
            value={config.center_freq_hz}
            onChange={(e) => handleChange('center_freq_hz', parseFloat(e.target.value))}
            step={1e6}
          />
        </div>

        <div className="control-group">
          <label>
            <Gauge size={16} />
            RX Gain
          </label>
          <div className="input-with-display">
            <input
              type="range"
              min={0}
              max={73}
              step={1}
              value={config.rx_gain_db}
              onChange={(e) => handleChange('rx_gain_db', parseFloat(e.target.value))}
            />
            <div className="value-display">{config.rx_gain_db.toFixed(0)} dB</div>
          </div>
        </div>

        <div className="control-group">
          <label>
            <Sliders size={16} />
            Sample Rate
          </label>
          <div className="input-with-display">
            <input
              type="range"
              min={2.084e6}
              max={61.44e6}
              step={1e6}
              value={config.sample_rate_hz}
              onChange={(e) => handleChange('sample_rate_hz', parseFloat(e.target.value))}
            />
            <div className="value-display">{formatRate(config.sample_rate_hz)}</div>
          </div>
        </div>

        {showAdvanced && (
          <div className="control-group">
            <label>Bandwidth</label>
            <div className="input-with-display">
              <input
                type="range"
                min={200e3}
                max={56e6}
                step={1e6}
                value={config.bandwidth_hz}
                onChange={(e) => handleChange('bandwidth_hz', parseFloat(e.target.value))}
              />
              <div className="value-display">{formatFreq(config.bandwidth_hz)}</div>
            </div>
          </div>
        )}

        {showAdvanced && (
          <div className="control-group">
            <label>Pluto URI</label>
            <input
              type="text"
              className="text-input"
              value={config.pluto_uri}
              onChange={(e) => handleChange('pluto_uri', e.target.value)}
              placeholder="ip:192.168.2.1"
            />
          </div>
        )}
      </div>

      <div className="control-actions">
        <button
          className="advanced-toggle"
          onClick={() => setShowAdvanced(!showAdvanced)}
        >
          {showAdvanced ? 'Hide' : 'Show'} Advanced
        </button>
        <button
          className="apply-btn"
          onClick={handleApply}
        >
          Apply Configuration
        </button>
      </div>

      {/* Connection Status */}
      {status && (
        <div className="connection-status">
          <div className={`status-indicator ${status.streaming ? 'connected' : 'disconnected'}`}>
            {status.streaming ? 'Streaming' : 'Not Streaming'}
          </div>
          {status.clients_connected > 0 && (
            <span className="clients-count">{status.clients_connected} client(s) connected</span>
          )}
        </div>
      )}

      {/* Pluto Status - Show actual values from device */}
      {status && status.metadata && status.metadata.context_received && (
        <div className="pluto-status">
          <h3>Pluto Actual Values:</h3>
          <div className="status-grid">
            <div className="status-item">
              <label>Center Freq:</label>
              <span className={config.center_freq_hz !== status.metadata.center_freq_hz ? 'mismatch' : ''}>
                {formatFreq(status.metadata.center_freq_hz)}
              </span>
            </div>
            <div className="status-item">
              <label>Sample Rate:</label>
              <span className={config.sample_rate_hz !== status.metadata.sample_rate_hz ? 'mismatch' : ''}>
                {formatRate(status.metadata.sample_rate_hz)}
              </span>
            </div>
            <div className="status-item">
              <label>Bandwidth:</label>
              <span className={config.bandwidth_hz !== status.metadata.bandwidth_hz ? 'mismatch' : ''}>
                {formatFreq(status.metadata.bandwidth_hz)}
              </span>
            </div>
            <div className="status-item">
              <label>Gain:</label>
              <span className={config.rx_gain_db !== status.metadata.gain_db ? 'mismatch' : ''}>
                {status.metadata.gain_db?.toFixed(1)} dB
              </span>
            </div>
          </div>
          {(config.center_freq_hz !== status.metadata.center_freq_hz ||
            config.sample_rate_hz !== status.metadata.sample_rate_hz ||
            config.bandwidth_hz !== status.metadata.bandwidth_hz ||
            config.rx_gain_db !== status.metadata.gain_db) && (
            <div className="config-warning">
              ⚠️ Settings don't match Pluto's actual values - click "Apply Configuration"
            </div>
          )}
        </div>
      )}
    </div>
  )
}
