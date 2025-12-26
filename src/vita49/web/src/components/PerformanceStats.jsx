import React from 'react'
import { Activity, Cpu, Zap } from 'lucide-react'
import './PerformanceStats.css'

export default function PerformanceStats({ stats, componentStats, onReset }) {
  const getFPSColor = (fps) => {
    if (fps >= 55) return '#10b981' // green
    if (fps >= 40) return '#f59e0b' // yellow
    return '#ef4444' // red
  }

  const getLatencyColor = (latency) => {
    if (latency <= 50) return '#10b981' // green
    if (latency <= 100) return '#f59e0b' // yellow
    return '#ef4444' // red
  }

  return (
    <div className="performance-stats card">
      <div className="perf-header">
        <h3><Zap size={18} /> Performance Monitor</h3>
        <button className="reset-btn" onClick={onReset} title="Reset stats">
          Reset
        </button>
      </div>

      <div className="perf-grid">
        {/* Frame Rate */}
        <div className="perf-item">
          <div className="perf-label">
            <Activity size={14} />
            FPS
          </div>
          <div className="perf-value" style={{ color: getFPSColor(stats.fps) }}>
            {stats.fps}
          </div>
          <div className="perf-detail">
            Target: 60 FPS
          </div>
        </div>

        {/* Frame Time */}
        <div className="perf-item">
          <div className="perf-label">Frame Time</div>
          <div className="perf-value">
            {stats.avgFrameTime} ms
          </div>
          <div className="perf-detail">
            Min: {stats.minFrameTime} / Max: {stats.maxFrameTime}
          </div>
        </div>

        {/* Dropped Frames */}
        <div className="perf-item">
          <div className="perf-label">Dropped Frames</div>
          <div className="perf-value" style={{ color: stats.droppedFrames > 5 ? '#ef4444' : '#10b981' }}>
            {stats.droppedFrames}
          </div>
          <div className="perf-detail">
            Last 60 frames
          </div>
        </div>

        {/* Message Rate */}
        <div className="perf-item">
          <div className="perf-label">Message Rate</div>
          <div className="perf-value">
            {stats.messagesPerSec}/s
          </div>
          <div className="perf-detail">
            Total: {stats.totalMessages}
          </div>
        </div>

        {/* Processing Delay */}
        <div className="perf-item">
          <div className="perf-label">Processing Delay</div>
          <div className="perf-value" style={{ color: getLatencyColor(stats.avgMessageLatency) }}>
            {stats.avgMessageLatency} ms
          </div>
          <div className="perf-detail">
            WebSocket → Handler
          </div>
        </div>

        {/* Memory Usage */}
        {stats.memoryUsage > 0 && (
          <div className="perf-item">
            <div className="perf-label">
              <Cpu size={14} />
              Memory
            </div>
            <div className="perf-value">
              {stats.memoryUsage} MB
            </div>
            <div className="perf-detail">
              JS Heap
            </div>
          </div>
        )}

        {/* Component Render Times */}
        {componentStats && Object.entries(componentStats).map(([name, stat]) => (
          stat && (
            <div key={name} className="perf-item">
              <div className="perf-label">{name} Render</div>
              <div className="perf-value">
                {stat.last} ms
              </div>
              <div className="perf-detail">
                Avg: {stat.avg} / Max: {stat.max}
              </div>
            </div>
          )
        ))}
      </div>
    </div>
  )
}
