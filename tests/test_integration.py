#!/usr/bin/env python3
"""
Integration tests for VITA49 streaming system

Tests complete workflows and component interactions.
Run with: pytest tests/test_integration.py -v
"""

import pytest
import time
import numpy as np

from vita49.packets import (
    VRTSignalDataPacket,
    VRTContextPacket,
    PacketType,
    create_stream_id
)
from vita49.stream_server import (
    VITA49StreamServer,
    VITA49StreamClient,
    SimulatedSDRInterface,
    SDRConfig
)


class TestPacketIntegration:
    """Test integration between packets and streaming"""

    def test_signal_packet_through_network(self):
        """Test sending signal packet over network"""
        port = 16000

        # Create and encode packet
        iq = 0.5 * np.random.randn(100) + 1j * 0.5 * np.random.randn(100)
        iq = iq.astype(np.complex64)

        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=iq,
            stream_id=create_stream_id(0, 1),
            sample_rate=30e6,
            timestamp=time.time()
        )

        encoded = packet.encode()

        # Send through actual UDP socket
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Receive it
        received_packets = []

        def on_packet(pkt, samples):
            received_packets.append((pkt, samples))

        client = VITA49StreamClient(port=port)
        client.on_samples(on_packet)
        client.start()

        time.sleep(0.1)

        # Send packet
        sock.sendto(encoded, ("127.0.0.1", port))

        time.sleep(0.2)

        client.stop()
        sock.close()

        # Verify received
        assert len(received_packets) > 0
        _, received_samples = received_packets[0]
        assert len(received_samples) == len(iq)

    def test_context_packet_parsing(self):
        """Test context packet encoding and parsing"""
        port = 16001

        context_list = []

        def on_context(ctx_data):
            context_list.append(ctx_data)

        client = VITA49StreamClient(port=port)
        client.on_context(on_context)
        client.start()

        # Create context packet
        context = VRTContextPacket(
            stream_id=create_stream_id(0, 1),
            bandwidth_hz=20e6,
            rf_reference_frequency_hz=2.4e9,
            sample_rate_hz=30e6,
            gain_db=25.0
        )

        encoded = context.encode()

        # Send it
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(encoded, ("127.0.0.1", port))

        time.sleep(0.2)

        client.stop()
        sock.close()

        # Should have received context packet
        assert len(context_list) > 0


class TestServerClientIntegration:
    """Test server-client integration"""

    def test_full_streaming_pipeline(self):
        """Test complete streaming from server to client"""
        port = 16002

        received_samples = []
        received_contexts = []

        def on_samples(pkt, samples):
            received_samples.append(samples)

        def on_context(ctx):
            received_contexts.append(ctx)

        # Setup client
        client = VITA49StreamClient(port=port)
        client.on_samples(on_samples)
        client.on_context(on_context)
        client.start()

        # Setup server
        server = VITA49StreamServer(
            center_freq_hz=2.4e9,
            sample_rate_hz=30e6,
            rx_gain_db=20.0,
            destination="127.0.0.1",
            port=port,
            samples_per_packet=360,
            context_packet_interval=50,
            use_simulation=True
        )

        server.start()

        # Let it stream
        time.sleep(3.0)

        # Stop
        server.stop()
        client.stop()

        # Verify data was received
        assert len(received_samples) > 0
        assert len(received_contexts) > 0

        # Verify sample integrity
        for samples in received_samples:
            assert isinstance(samples, np.ndarray)
            assert samples.dtype == np.complex64
            assert len(samples) > 0

    def test_multi_channel_integration(self):
        """Test multi-channel streaming"""
        port = 16003

        packets_ch0 = []
        packets_ch1 = []

        def on_samples(pkt, samples):
            stream_id = pkt.stream_id
            channel = stream_id & 0xFF
            if channel == 0:
                packets_ch0.append(samples)
            elif channel == 1:
                packets_ch1.append(samples)

        client = VITA49StreamClient(port=port)
        client.on_samples(on_samples)
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

        # Both channels should have data
        assert len(packets_ch0) > 0
        assert len(packets_ch1) > 0

    def test_high_throughput_streaming(self):
        """Test high throughput streaming"""
        port = 16004

        sample_count = [0]

        def on_samples(pkt, samples):
            sample_count[0] += len(samples)

        client = VITA49StreamClient(port=port)
        client.on_samples(on_samples)
        client.start()

        # High sample rate server
        server = VITA49StreamServer(
            sample_rate_hz=61.44e6,  # Max rate for Pluto
            destination="127.0.0.1",
            port=port,
            samples_per_packet=1000,
            use_simulation=True
        )

        server.start()
        time.sleep(2.0)

        server.stop()
        client.stop()

        # Should have received many samples
        assert sample_count[0] > 100000

    def test_statistics_accuracy(self):
        """Test that statistics are accurate"""
        port = 16005

        client = VITA49StreamClient(port=port)
        client.start()

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            samples_per_packet=360,
            use_simulation=True
        )

        server.start()
        time.sleep(2.0)

        # Get statistics
        server_stats = server.get_statistics()
        client_packets = client.packets_received
        client_samples = client.samples_received

        server.stop()
        client.stop()

        # Verify statistics make sense
        assert server_stats[0]['packets_sent'] > 0
        assert client_packets > 0
        assert client_samples > 0

        # Client should receive most packets (some may be lost in UDP)
        packet_ratio = client_packets / server_stats[0]['packets_sent']
        assert packet_ratio > 0.9  # At least 90% delivery on localhost


class TestSimulatedSDRIntegration:
    """Test simulated SDR integration with streaming"""

    def test_simulated_sdr_to_stream(self):
        """Test streaming from simulated SDR"""
        port = 16006

        client = VITA49StreamClient(port=port)
        client.start()

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            use_simulation=True
        )

        server.start()
        time.sleep(1.5)

        stats = server.get_statistics()

        server.stop()
        client.stop()

        # Simulated SDR should be generating data
        assert stats[0]['packets_sent'] > 0

    def test_different_signal_types(self):
        """Test different simulated signal types"""
        port = 16007

        for signal_type in ['tone', 'noise', 'pulse']:
            received = []

            def on_samples(pkt, samples):
                received.append(samples)

            client = VITA49StreamClient(port=port)
            client.on_samples(on_samples)
            client.start()

            # Create SDR with specific signal type
            config = SDRConfig()
            sdr = SimulatedSDRInterface(config, signal_type=signal_type)

            server = VITA49StreamServer(
                destination="127.0.0.1",
                port=port,
                use_simulation=True
            )

            server.start()
            time.sleep(1.0)

            server.stop()
            client.stop()

            assert len(received) > 0


class TestRobustness:
    """Test system robustness and error recovery"""

    def test_client_restart(self):
        """Test restarting client while server is running"""
        port = 16008

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            use_simulation=True
        )
        server.start()

        # Start and stop client multiple times
        for i in range(3):
            client = VITA49StreamClient(port=port)
            client.start()
            time.sleep(0.5)
            assert client.packets_received > 0
            client.stop()

        server.stop()

    def test_server_restart(self):
        """Test restarting server while client is running"""
        port = 16009

        client = VITA49StreamClient(port=port)
        client.start()

        # Start and stop server multiple times
        for i in range(3):
            server = VITA49StreamServer(
                destination="127.0.0.1",
                port=port,
                use_simulation=True
            )
            server.start()
            time.sleep(0.5)
            server.stop()

        client.stop()

    def test_rapid_start_stop(self):
        """Test rapid start/stop cycles"""
        port = 16010

        for i in range(5):
            server = VITA49StreamServer(
                destination="127.0.0.1",
                port=port,
                use_simulation=True
            )
            server.start()
            time.sleep(0.2)
            server.stop()

    def test_concurrent_clients(self):
        """Test multiple clients receiving same stream"""
        port = 16011

        # Start server
        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            use_simulation=True
        )
        server.start()

        # Start multiple clients
        clients = []
        for i in range(3):
            client = VITA49StreamClient(port=port)
            client.start()
            clients.append(client)

        time.sleep(2.0)

        # All clients should receive packets
        for client in clients:
            assert client.packets_received > 0

        # Cleanup
        server.stop()
        for client in clients:
            client.stop()


class TestPerformance:
    """Test performance characteristics"""

    @pytest.mark.slow
    def test_sustained_streaming(self):
        """Test sustained streaming over longer period"""
        port = 16012

        client = VITA49StreamClient(port=port)
        client.start()

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            use_simulation=True
        )

        server.start()

        # Run for 10 seconds
        time.sleep(10.0)

        stats = server.get_statistics()

        server.stop()
        client.stop()

        # Should have sustained throughput
        assert stats[0]['packets_sent'] > 1000

    def test_packet_loss_measurement(self):
        """Test measuring packet loss"""
        port = 16013

        client = VITA49StreamClient(port=port)
        client.start()

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=port,
            samples_per_packet=100,
            use_simulation=True
        )

        server.start()
        time.sleep(2.0)

        server_stats = server.get_statistics()
        packets_sent = server_stats[0]['packets_sent']
        packets_received = client.packets_received

        server.stop()
        client.stop()

        # Calculate packet loss (should be minimal on localhost)
        loss_rate = 1.0 - (packets_received / packets_sent)
        assert loss_rate < 0.1  # Less than 10% loss


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
