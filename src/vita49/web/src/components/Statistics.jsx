import React from 'react'
import { BarChart3, Clock, Zap, HardDrive, Signal } from 'lucide-react'
import './Statistics.css'

export default function Statistics({ statistics, metadata }) {
  const formatNumber = (num) => {
    if (num >= 1e9) return `${(num / 1e9).toFixed(2)}G`
    if (num >= 1e6) return `${(num / 1e6).toFixed(2)}M`
    if (num >= 1e3) return `${(num / 1e3).toFixed(2)}K`
    return num?.toFixed(0) || '0'
  }

  const formatTime = (seconds) => {
    if (!seconds) return '0s'
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    const secs = Math.floor(seconds % 60)

    if (hours > 0) return `${hours}h ${minutes}m ${secs}s`
    if (minutes > 0) return `${minutes}m ${secs}s`
    return `${secs}s`
  }

  const stats = statistics || {}
  const meta = metadata || {}

  const statCards = [
    {
      icon: <BarChart3 size={20} />,
      label: 'Packets Received',
      value: formatNumber(stats.packets_received),
      detail: `${stats.context_packets_received || 0} context`,
      color: 'blue'
    },
    {
      icon: <Signal size={20} />,
      label: 'Samples Received',
      value: formatNumber(stats.samples_received),
      detail: `${stats.packet_rate_hz?.toFixed(1) || 0} pkt/s`,
      color: 'green'
    },
    {
      icon: <Zap size={20} />,
      label: 'Throughput',
      value: `${stats.throughput_mbps?.toFixed(1) || 0}`,
      detail: 'Mbps',
      color: 'yellow'
    },
    {
      icon: <Clock size={20} />,
      label: 'Elapsed Time',
      value: formatTime(stats.elapsed_time_s),
      detail: stats.start_time ? new Date(stats.start_time * 1000).toLocaleTimeString() : 'N/A',
      color: 'purple'
    }
  ]

  const configInfo = [
    { label: 'Center Frequency', value: `${(meta.center_freq_hz / 1e9)?.toFixed(3) || 0} GHz` },
    { label: 'Sample Rate', value: `${(meta.sample_rate_hz / 1e6)?.toFixed(1) || 0} MSPS` },
    { label: 'Bandwidth', value: `${(meta.bandwidth_hz / 1e6)?.toFixed(1) || 0} MHz` },
    { label: 'RX Gain', value: `${meta.gain_db?.toFixed(0) || 0} dB` }
  ]

  return (
    <div className="statistics">
      <h3 className="stats-title">
        <HardDrive size={18} /> Stream Statistics
      </h3>

      <div className="stat-cards">
        {statCards.map((card, idx) => (
          <div key={idx} className={`stat-card ${card.color}`}>
            <div className="stat-icon">{card.icon}</div>
            <div className="stat-content">
              <div className="stat-label">{card.label}</div>
              <div className="stat-value">{card.value}</div>
              <div className="stat-detail">{card.detail}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="config-info">
        <h4>Configuration</h4>
        <div className="config-grid">
          {configInfo.map((item, idx) => (
            <div key={idx} className="config-item">
              <span className="config-label">{item.label}:</span>
              <span className="config-value">{item.value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
