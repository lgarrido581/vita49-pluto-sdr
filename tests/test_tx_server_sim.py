"""End-to-end sim tests for VITA49TxServer + SimulatedSDRInterface.

Spins up the TX server bound to a real UDP socket, sends VITA-49 IF Data
packets to it, and asserts the simulated SDR's transmit() captured the
right samples on the right channels (and that the epoch filter works).

No hardware required.
"""

import socket
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vita49.packets import (  # noqa: E402
    VRTSignalDataPacket,
    create_stream_id,
)
from vita49.stream_server import (  # noqa: E402
    SDRConfig,
    SimulatedSDRInterface,
    VITA49TxServer,
)


SR = 30e6


def _free_udp_port() -> int:
    """Grab an ephemeral UDP port from the OS so concurrent test runs don't collide."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wave(n=64, freq=1e6, phase=0.0):
    t = np.arange(n) / SR
    return (0.7 * np.exp(1j * (2 * np.pi * freq * t + phase))).astype(np.complex64)


@pytest.fixture
def tx_setup():
    """SimulatedSDRInterface + VITA49TxServer wired up, both TX channels enabled."""
    cfg = SDRConfig(sample_rate_hz=SR, tx_channels=[0, 1])
    sdr = SimulatedSDRInterface(cfg)
    assert sdr.connect()
    port = _free_udp_port()
    server = VITA49TxServer(sdr, listen_address="127.0.0.1", port=port)
    assert server.start()
    # Sender socket
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    yield sdr, server, sender, port
    sender.close()
    server.stop()
    sdr.disconnect()


def _send_and_wait(server: VITA49TxServer, sender, port: int, packet_bytes: bytes,
                   expected_received: int, timeout: float = 1.0) -> None:
    """Fire UDP and busy-wait until the server has accounted for the packet."""
    sender.sendto(packet_bytes, ("127.0.0.1", port))
    deadline = time.time() + timeout
    while time.time() < deadline:
        total = (
            server.packets_received
            + server.packets_dropped_wrong_stream
            + server.packets_dropped_stale_epoch
        )
        if total >= expected_received:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"Timed out waiting for {expected_received} packets to be processed; "
        f"stats={server.get_statistics()}"
    )


class TestTxServerRouting:
    def test_packet_routed_to_correct_tx_channel(self, tx_setup):
        sdr, server, sender, port = tx_setup
        iq_ch0 = _wave(phase=0.0)
        iq_ch1 = _wave(phase=np.pi / 3)

        pkt0 = VRTSignalDataPacket.from_iq_samples(
            iq_ch0, create_stream_id(channel=0, device_id=2), SR, timestamp=1.0
        ).encode()
        pkt1 = VRTSignalDataPacket.from_iq_samples(
            iq_ch1, create_stream_id(channel=1, device_id=2), SR, timestamp=2.0
        ).encode()

        _send_and_wait(server, sender, port, pkt0, expected_received=1)
        _send_and_wait(server, sender, port, pkt1, expected_received=2)

        assert server.packets_transmitted == 2
        assert len(sdr.tx_captured[0]) == 1
        assert len(sdr.tx_captured[1]) == 1
        # Verify the samples that hit the (simulated) DAC match what was sent,
        # within int16 quantization tolerance from the encode round-trip.
        mse0 = float(np.mean(np.abs(iq_ch0 - sdr.tx_captured[0][0]) ** 2))
        mse1 = float(np.mean(np.abs(iq_ch1 - sdr.tx_captured[1][0]) ** 2))
        assert mse0 < 1e-6
        assert mse1 < 1e-6

    def test_rx_stream_id_packets_are_dropped(self, tx_setup):
        sdr, server, sender, port = tx_setup
        # device_id=1 -> high byte 0x01 -> RX side, not TX
        pkt = VRTSignalDataPacket.from_iq_samples(
            _wave(), create_stream_id(channel=0, device_id=1), SR, timestamp=1.0
        ).encode()
        _send_and_wait(server, sender, port, pkt, expected_received=1)
        assert server.packets_dropped_wrong_stream == 1
        assert server.packets_received == 0
        assert len(sdr.tx_captured[0]) == 0


class TestTxServerEpochFilter:
    def test_stale_epoch_dropped(self, tx_setup):
        sdr, server, sender, port = tx_setup
        server.set_min_epoch(5)

        stale = VRTSignalDataPacket.from_iq_samples(
            _wave(), create_stream_id(channel=0, device_id=2), SR,
            timestamp=1.0, config_epoch=3,
        ).encode()
        fresh = VRTSignalDataPacket.from_iq_samples(
            _wave(), create_stream_id(channel=0, device_id=2), SR,
            timestamp=2.0, config_epoch=5,
        ).encode()

        _send_and_wait(server, sender, port, stale, expected_received=1)
        _send_and_wait(server, sender, port, fresh, expected_received=2)

        stats = server.get_statistics()
        assert stats['packets_dropped_stale_epoch'] == 1
        assert stats['packets_transmitted'] == 1
        assert len(sdr.tx_captured[0]) == 1

    def test_unset_min_epoch_passes_untagged_packets(self, tx_setup):
        sdr, server, sender, port = tx_setup
        # Default min_epoch=0; an untagged (epoch=0) packet should pass through.
        pkt = VRTSignalDataPacket.from_iq_samples(
            _wave(), create_stream_id(channel=0, device_id=2), SR, timestamp=1.0,
        ).encode()
        _send_and_wait(server, sender, port, pkt, expected_received=1)
        assert server.packets_transmitted == 1


class TestSimulatorTxCapture:
    def test_disabled_channel_drops(self):
        cfg = SDRConfig(sample_rate_hz=SR, tx_channels=[0])  # only ch0
        sdr = SimulatedSDRInterface(cfg)
        sdr.connect()
        assert sdr.transmit(_wave(), channel=0) is True
        assert sdr.transmit(_wave(), channel=1) is False  # not enabled
        assert len(sdr.tx_captured[0]) == 1
        assert 1 not in sdr.tx_captured
