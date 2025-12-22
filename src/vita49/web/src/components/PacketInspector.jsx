import React, { useState, useEffect } from 'react'
import { Package, Filter } from 'lucide-react'
import './PacketInspector.css'

export default function PacketInspector() {
  const [packets, setPackets] = useState([])
  const [filter, setFilter] = useState('all')
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    // Fetch recent packets from API
    const fetchPackets = async () => {
      try {
        const response = await fetch('/api/packets?count=50')
        const data = await response.json()
        if (data.packets) {
          setPackets(data.packets)
        }
      } catch (err) {
        console.error('Error fetching packets:', err)
      }
    }

    fetchPackets()
    const interval = setInterval(fetchPackets, 2000) // Update every 2 seconds

    return () => clearInterval(interval)
  }, [])

  const filteredPackets = packets.filter(packet => {
    if (filter === 'all') return true
    if (filter === 'data') return packet.type === 'DATA'
    if (filter === 'context') return packet.type === 'CONTEXT'
    return true
  })

  const formatTimestamp = (ts) => {
    if (!ts) return 'N/A'
    const date = new Date(ts * 1000)
    return date.toLocaleTimeString() + '.' + String(date.getMilliseconds()).padStart(3, '0')
  }

  return (
    <div className="packet-inspector card">
      <div className="inspector-header">
        <h3><Package size={18} /> Packet Inspector</h3>
        <div className="inspector-controls">
          <div className="filter-group">
            <Filter size={14} />
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">All Packets</option>
              <option value="data">Data Only</option>
              <option value="context">Context Only</option>
            </select>
          </div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
            />
            Auto-scroll
          </label>
        </div>
      </div>

      <div className="packet-table-container" style={{ overflow: autoScroll ? 'hidden' : 'auto' }}>
        <table className="packet-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Type</th>
              <th>Stream ID</th>
              <th>Pkt Count</th>
              <th>Samples</th>
            </tr>
          </thead>
          <tbody>
            {filteredPackets.length === 0 ? (
              <tr>
                <td colSpan="5" className="empty-message">
                  No packets captured yet...
                </td>
              </tr>
            ) : (
              filteredPackets.slice(-20).map((packet, idx) => (
                <tr key={idx} className="fade-in">
                  <td className="timestamp">{formatTimestamp(packet.timestamp)}</td>
                  <td>
                    <span className={`packet-type ${packet.type.toLowerCase()}`}>
                      {packet.type}
                    </span>
                  </td>
                  <td className="stream-id">{packet.stream_id}</td>
                  <td>{packet.packet_count}</td>
                  <td>{packet.sample_count}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="inspector-footer">
        <span>Total: {filteredPackets.length} packets</span>
        <span>Showing last 20</span>
      </div>
    </div>
  )
}
