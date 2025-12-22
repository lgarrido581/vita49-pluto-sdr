import React, { useState, useEffect } from 'react'
import { Package, Filter } from 'lucide-react'
import './PacketInspector.css'

export default function PacketInspector() {
  const [packets, setPackets] = useState([])
  const [filter, setFilter] = useState('all')
  const [autoScroll, setAutoScroll] = useState(true)
  const [selectedPacket, setSelectedPacket] = useState(null)

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

  const handlePacketClick = (packet) => {
    setSelectedPacket(packet)
  }

  const closeModal = () => {
    setSelectedPacket(null)
  }

  const renderFieldValue = (value) => {
    if (typeof value === 'boolean') {
      return value ? 'True' : 'False'
    }
    if (value === null || value === undefined) {
      return 'N/A'
    }
    return String(value)
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
                <tr key={idx} className="fade-in clickable-row" onClick={() => handlePacketClick(packet)}>
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

      {selectedPacket && (
        <div className="modal-overlay" onClick={closeModal}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Packet Details</h3>
              <button className="modal-close" onClick={closeModal}>&times;</button>
            </div>
            <div className="modal-body">
              <div className="field-section">
                <h4>Basic Information</h4>
                <div className="field-grid">
                  <div className="field-row">
                    <span className="field-label">Type:</span>
                    <span className="field-value">{selectedPacket.type}</span>
                  </div>
                  <div className="field-row">
                    <span className="field-label">Stream ID:</span>
                    <span className="field-value">{selectedPacket.stream_id}</span>
                  </div>
                  <div className="field-row">
                    <span className="field-label">Packet Count:</span>
                    <span className="field-value">{selectedPacket.packet_count}</span>
                  </div>
                  <div className="field-row">
                    <span className="field-label">Sample Count:</span>
                    <span className="field-value">{selectedPacket.sample_count}</span>
                  </div>
                  <div className="field-row">
                    <span className="field-label">Timestamp:</span>
                    <span className="field-value">{formatTimestamp(selectedPacket.timestamp)}</span>
                  </div>
                </div>
              </div>

              {selectedPacket.header && (
                <div className="field-section">
                  <h4>Header</h4>
                  <div className="field-grid">
                    <div className="field-row">
                      <span className="field-label">Packet Type:</span>
                      <span className="field-value">{selectedPacket.header.packet_type}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">TSI (Timestamp Integer):</span>
                      <span className="field-value">{selectedPacket.header.tsi}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">TSF (Timestamp Fractional):</span>
                      <span className="field-value">{selectedPacket.header.tsf}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Packet Size (words):</span>
                      <span className="field-value">{selectedPacket.header.packet_size_words}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Class ID Present:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.header.class_id_present)}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Trailer Present:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.header.trailer_present)}</span>
                    </div>
                  </div>
                </div>
              )}

              {selectedPacket.timestamp_detail && (
                <div className="field-section">
                  <h4>Timestamp Detail</h4>
                  <div className="field-grid">
                    <div className="field-row">
                      <span className="field-label">Integer Seconds:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.timestamp_detail.integer_seconds)}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Fractional Seconds (ps):</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.timestamp_detail.fractional_seconds)}</span>
                    </div>
                  </div>
                </div>
              )}

              {selectedPacket.trailer && (
                <div className="field-section">
                  <h4>Trailer</h4>
                  <div className="field-grid">
                    <div className="field-row">
                      <span className="field-label">Calibrated Time:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.trailer.calibrated_time)}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Valid Data:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.trailer.valid_data)}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Reference Lock:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.trailer.reference_lock)}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">AGC/MGC:</span>
                      <span className="field-value">{selectedPacket.trailer.agc_mgc ? 'AGC' : 'MGC'}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Detected Signal:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.trailer.detected_signal)}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Spectral Inversion:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.trailer.spectral_inversion)}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Over Range:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.trailer.over_range)}</span>
                    </div>
                    <div className="field-row">
                      <span className="field-label">Sample Loss:</span>
                      <span className="field-value">{renderFieldValue(selectedPacket.trailer.sample_loss)}</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
