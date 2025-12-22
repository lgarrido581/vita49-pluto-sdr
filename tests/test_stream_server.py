#!/usr/bin/env python3
"""
Unit tests for VITA49 stream server and client

Tests streaming, configuration, and SDR interfaces.
Run with: pytest tests/test_stream_server.py -v
"""

import pytest
import time
import numpy as np
import socket

from vita49.stream_server import (
    VITA49StreamServer,
    VITA49StreamClient,
    SimulatedSDRInterface,
    SDRConfig,
    StreamConfig,
    StreamStatistics,
    StreamMode,
    GainMode
)


class TestSDRConfig:
    """Test SDR configuration"""

    def test_default_config(self):
        """Test default SDR configuration"""
        config = SDRConfig()
        assert config.sample_rate_hz == 30.72e6
        assert config.center_freq_hz == 2.4e9
        assert config.rx_gain_db == 20.0
        assert config.buffer_size == 16384

    def test_custom_config(self):
        """Test custom SDR configuration"""
        config = SDRConfig(
            sample_rate_hz=10e6,
            center_freq_hz=915e6,
            rx_gain_db=30.0,
            buffer_size=8192,
            rx_channels=[0, 1]
        )

        assert config.sample_rate_hz == 10e6
        assert config.center_freq_hz == 915e6
        assert config.rx_gain_db == 30.0
        assert config.buffer_size == 8192
        assert config.rx_channels == [0, 1]


class TestStreamConfig:
    """Test stream configuration"""

    def test_default_stream_config(self):
        """Test default stream configuration"""
        config = StreamConfig()
        assert config.destination == "127.0.0.1"
        assert config.port == 4991
        assert config.samples_per_packet == 360

    def test_custom_stream_config(self):
        """Test custom stream configuration"""
        config = StreamConfig(
            destination="192.168.1.100",
            port=5000,
            samples_per_packet=512,
            context_packet_interval=50
        )

        assert config.destination == "192.168.1.100"
        assert config.port == 5000
        assert config.samples_per_packet == 512
        assert config.context_packet_interval == 50


class TestSimulatedSDR:
    """Test simulated SDR interface"""

    def test_create_simulated_sdr(self):
        """Test creating simulated SDR"""
        config = SDRConfig(rx_channels=[0])
        sdr = SimulatedSDRInterface(config)

        assert sdr.connected == False
        assert sdr.config == config

    def test_simulated_connect(self):
        """Test connecting to simulated SDR"""
        config = SDRConfig(rx_channels=[0, 1])
        sdr = SimulatedSDRInterface(config)

        assert sdr.connect() == True
        assert sdr.connected == True

    def test_simulated_disconnect(self):
        """Test disconnecting simulated SDR"""
        config = SDRConfig()
        sdr = SimulatedSDRInterface(config)
        sdr.connect()
        assert sdr.connected == True

        sdr.disconnect()
        assert sdr.connected == False

    def test_simulated_receive_single_channel(self):
        """Test receiving from single channel"""
        config = SDRConfig(
            sample_rate_hz=30e6,
            buffer_size=1024,
            rx_channels=[0]
        )
        sdr = SimulatedSDRInterface(config)
        sdr.connect()

        data = sdr.receive()
        assert data is not None
        assert len(data) == 1
        assert len(data[0]) == 1024
        assert data[0].dtype == np.complex64

    def test_simulated_receive_dual_channel(self):
        """Test receiving from dual channels"""
        config = SDRConfig(
            sample_rate_hz=30e6,
            buffer_size=1024,
            rx_channels=[0, 1]
        )
        sdr = SimulatedSDRInterface(config)
        sdr.connect()

        data = sdr.receive()
        assert len(data) == 2
        assert len(data[0]) == 1024
        assert len(data[1]) == 1024

    def test_simulated_receive_without_connect(self):
        """Test receiving without connecting first"""
        config = SDRConfig()
        sdr = SimulatedSDRInterface(config)

        data = sdr.receive()
        assert data is None

    def test_simulated_signal_types(self):
        """Test different simulated signal types"""
        for signal_type in ['tone', 'noise', 'pulse']:
            config = SDRConfig(buffer_size=1024)
            sdr = SimulatedSDRInterface(config, signal_type=signal_type)
            sdr.connect()

            data = sdr.receive()
            assert data is not None
            assert len(data[0]) == 1024


class TestStreamServer:
    """Test VITA49 stream server"""

    def test_create_server(self):
        """Test creating stream server"""
        server = VITA49StreamServer(
            center_freq_hz=2.4e9,
            sample_rate_hz=10e6,
            destination="127.0.0.1",
            port=14991,
            use_simulation=True
        )

        assert server is not None

    def test_server_start_stop_simulation(self):
        """Test starting and stopping server in simulation mode"""
        server = VITA49StreamServer(
            center_freq_hz=2.4e9,
            sample_rate_hz=10e6,
            destination="127.0.0.1",
            port=14991,
            use_simulation=True
        )

        assert server.start() == True
        time.sleep(0.5)

        stats = server.get_statistics()
        assert 0 in stats

        server.stop()

    def test_server_streams_packets(self):
        """Test that server actually streams packets"""
        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=14992,
            use_simulation=True
        )

        server.start()
        time.sleep(1.0)

        stats = server.get_statistics()
        assert stats[0]['packets_sent'] > 0
        assert stats[0]['bytes_sent'] > 0

        server.stop()

    def test_server_multiple_channels(self):
        """Test server with multiple channels"""
        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=14993,
            rx_channels=[0, 1],
            use_simulation=True
        )

        server.start()
        time.sleep(1.0)

        stats = server.get_statistics()
        assert 0 in stats
        assert 1 in stats

        server.stop()

    def test_server_custom_packet_size(self):
        """Test server with custom packet size"""
        for pkt_size in [100, 360, 512, 1000]:
            server = VITA49StreamServer(
                destination="127.0.0.1",
                port=14990 + pkt_size % 100,
                samples_per_packet=pkt_size,
                use_simulation=True
            )

            server.start()
            time.sleep(0.5)
            server.stop()


class TestStreamClient:
    """Test VITA49 stream client"""

    def test_create_client(self):
        """Test creating stream client"""
        client = VITA49StreamClient(port=14993)
        assert client is not None

    def test_client_start_stop(self):
        """Test client start/stop"""
        client = VITA49StreamClient(port=14994)

        assert client.start() == True
        assert client.socket is not None

        client.stop()
        assert client.socket is None

    def test_client_receive_callback(self):
        """Test client receive callback"""
        received_packets = []

        def on_packet(packet, samples):
            received_packets.append((packet, samples))

        client = VITA49StreamClient(port=14995)
        client.on_samples(on_packet)
        client.start()

        # Let it receive some packets (if any are being sent)
        time.sleep(0.5)

        client.stop()

    def test_client_context_callback(self):
        """Test client context packet callback"""
        context_packets = []

        def on_context(context_data):
            context_packets.append(context_data)

        client = VITA49StreamClient(port=14996)
        client.on_context(on_context)
        client.start()

        time.sleep(0.5)

        client.stop()


class TestEndToEndStreaming:
    """End-to-end streaming tests"""

    def test_server_client_communication(self):
        """Test server to client packet delivery"""
        port = 14997

        # Start client first
        client = VITA49StreamClient(port=port)
        client.start()

        # Start server
        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            samples_per_packet=100,
            use_simulation=True
        )
        server.start()

        # Wait for packets
        time.sleep(2.0)

        server.stop()
        client.stop()

        # Verify packets were received
        assert client.packets_received > 0
        assert client.samples_received > 0

    def test_dual_channel_streaming(self):
        """Test dual-channel streaming"""
        port = 14998

        client = VITA49StreamClient(port=port)
        client.start()

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            rx_channels=[0, 1],
            use_simulation=True
        )
        server.start()

        time.sleep(2.0)

        server.stop()
        client.stop()

        assert client.packets_received > 0

    def test_context_packet_delivery(self):
        """Test that context packets are delivered"""
        port = 14999
        context_received = []

        def on_context(context_data):
            context_received.append(context_data)

        client = VITA49StreamClient(port=port)
        client.on_context(on_context)
        client.start()

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            use_simulation=True,
            context_packet_interval=10  # Send context frequently
        )
        server.start()

        time.sleep(2.0)

        server.stop()
        client.stop()

        # Should have received at least one context packet
        assert len(context_received) > 0


class TestStreamStatistics:
    """Test streaming statistics"""

    def test_server_statistics(self):
        """Test server statistics tracking"""
        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=15000,
            use_simulation=True
        )

        server.start()
        time.sleep(1.5)

        stats = server.get_statistics()
        assert 0 in stats

        channel_stats = stats[0]
        assert 'packets_sent' in channel_stats
        assert 'bytes_sent' in channel_stats
        assert channel_stats['packets_sent'] > 0

        server.stop()

    def test_client_statistics(self):
        """Test client statistics tracking"""
        port = 15001

        client = VITA49StreamClient(port=port)
        client.start()

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            use_simulation=True
        )
        server.start()

        time.sleep(1.5)

        assert client.packets_received > 0
        assert client.samples_received > 0

        server.stop()
        client.stop()


class TestErrorConditions:
    """Test error handling"""

    def test_server_already_running(self):
        """Test starting server that's already running"""
        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=15002,
            use_simulation=True
        )

        assert server.start() == True
        # Starting again should return False or True but not crash
        result = server.start()
        assert isinstance(result, bool)

        server.stop()

    def test_stop_without_start(self):
        """Test stopping server that wasn't started"""
        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=15003,
            use_simulation=True
        )

        # Should not crash
        server.stop()

    def test_client_double_start(self):
        """Test starting client twice"""
        client = VITA49StreamClient(port=15004)

        assert client.start() == True
        # Starting again
        result = client.start()
        assert isinstance(result, bool)

        client.stop()

    def test_client_double_stop(self):
        """Test stopping client twice"""
        client = VITA49StreamClient(port=15005)
        client.start()
        client.stop()
        # Stopping again should not crash
        client.stop()


class TestConfiguration:
    """Test various configuration options"""

    def test_different_sample_rates(self):
        """Test different sample rates"""
        for rate in [1e6, 10e6, 30e6, 61.44e6]:
            server = VITA49StreamServer(
                sample_rate_hz=rate,
                destination="127.0.0.1",
                port=15006,
                use_simulation=True
            )
            server.start()
            time.sleep(0.5)
            server.stop()

    def test_different_frequencies(self):
        """Test different center frequencies"""
        for freq in [915e6, 2.4e9, 5.8e9]:
            server = VITA49StreamServer(
                center_freq_hz=freq,
                destination="127.0.0.1",
                port=15007,
                use_simulation=True
            )
            server.start()
            time.sleep(0.5)
            server.stop()

    def test_different_gains(self):
        """Test different gain settings"""
        for gain in [0, 10, 20, 40, 70]:
            server = VITA49StreamServer(
                rx_gain_db=gain,
                destination="127.0.0.1",
                port=15008,
                use_simulation=True
            )
            server.start()
            time.sleep(0.5)
            server.stop()


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
