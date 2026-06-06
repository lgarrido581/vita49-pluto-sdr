"""Tests for the config_epoch tag carried in VITA-49 packets.

The epoch lets consumers associate IQ samples with a specific SDR
configuration generation, so that samples taken under the previous
config (still draining from libiio buffers and UDP queues at reconfig
time) can be filtered out.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vita49.packets import (  # noqa: E402
    EPOCH_CLASS_OUI,
    EPOCH_PACKET_CLASS_CODE,
    VRTContextPacket,
    VRTSignalDataPacket,
    VRTTimestamp,
    class_id_epoch,
    create_stream_id,
    make_epoch_class_id,
)


SR = 30e6
STREAM_ID = create_stream_id(channel=0, device_id=1)
TS_FIXED = 1700000000.123


def _iq(n=64):
    t = np.arange(n) / SR
    return (0.5 * np.exp(1j * 2 * np.pi * 1e6 * t)).astype(np.complex64)


class TestEpochClassIdHelper:
    def test_make_then_extract(self):
        cid = make_epoch_class_id(0x1234)
        assert cid.oui == EPOCH_CLASS_OUI
        assert cid.packet_class_code == EPOCH_PACKET_CLASS_CODE
        assert class_id_epoch(cid) == 0x1234

    def test_extract_returns_zero_for_none(self):
        assert class_id_epoch(None) == 0

    def test_extract_returns_zero_for_foreign_class_id(self):
        # A Class ID set by some other vendor / for some other purpose
        # must not be misread as an epoch.
        from vita49.packets import VRTClassID

        foreign = VRTClassID(oui=0x123456, information_class_code=0xBEEF, packet_class_code=0x0001)
        assert class_id_epoch(foreign) == 0

    def test_epoch_is_masked_to_16_bits(self):
        cid = make_epoch_class_id(0x1_0042)  # 17 bits set
        assert class_id_epoch(cid) == 0x0042


class TestDataPacketEpoch:
    def test_roundtrip_with_epoch(self):
        p = VRTSignalDataPacket.from_iq_samples(
            _iq(), STREAM_ID, SR, timestamp=TS_FIXED, config_epoch=7
        )
        decoded = VRTSignalDataPacket.decode(p.encode())
        assert decoded.config_epoch == 7
        assert decoded.header.class_id_present is True

    def test_roundtrip_without_epoch_preserves_wire_format(self):
        # Default kwarg (no epoch) must not add a Class ID — keeps wire
        # format bit-identical to pre-feature behavior.
        p = VRTSignalDataPacket.from_iq_samples(_iq(), STREAM_ID, SR, timestamp=TS_FIXED)
        assert p.config_epoch == 0
        assert p.class_id is None
        assert p.header.class_id_present is False
        decoded = VRTSignalDataPacket.decode(p.encode())
        assert decoded.config_epoch == 0
        assert decoded.class_id is None

    def test_explicit_zero_epoch_equals_default(self):
        a = VRTSignalDataPacket.from_iq_samples(_iq(), STREAM_ID, SR, timestamp=TS_FIXED).encode()
        b = VRTSignalDataPacket.from_iq_samples(
            _iq(), STREAM_ID, SR, timestamp=TS_FIXED, config_epoch=0
        ).encode()
        assert a == b

    def test_iq_recovery_unaffected_by_epoch(self):
        iq = _iq(32)
        p = VRTSignalDataPacket.from_iq_samples(
            iq, STREAM_ID, SR, timestamp=TS_FIXED, config_epoch=99
        )
        recovered = VRTSignalDataPacket.decode(p.encode()).to_iq_samples()
        # int16 quantization tolerance
        mse = float(np.mean(np.abs(iq - recovered) ** 2))
        assert mse < 1e-6

    def test_packet_size_grows_by_class_id_when_epoch_set(self):
        no_epoch = VRTSignalDataPacket.from_iq_samples(_iq(), STREAM_ID, SR, timestamp=TS_FIXED)
        with_epoch = VRTSignalDataPacket.from_iq_samples(
            _iq(), STREAM_ID, SR, timestamp=TS_FIXED, config_epoch=1
        )
        # Class ID is 2 words = 8 bytes
        assert len(with_epoch.encode()) == len(no_epoch.encode()) + 8


class TestContextPacketEpoch:
    def _ts(self):
        return VRTTimestamp.from_time(TS_FIXED)

    def test_roundtrip_with_epoch(self):
        ctx = VRTContextPacket(
            stream_id=0x01000000,
            timestamp=self._ts(),
            sample_rate_hz=SR,
            gain_db=20.0,
            config_epoch=42,
        )
        decoded = VRTContextPacket.decode(ctx.encode())
        assert decoded.config_epoch == 42

    def test_roundtrip_without_epoch_preserves_wire_format(self):
        ctx = VRTContextPacket(
            stream_id=0x01000000,
            timestamp=self._ts(),
            sample_rate_hz=SR,
            gain_db=20.0,
        )
        assert ctx.config_epoch == 0
        assert ctx.class_id is None
        decoded = VRTContextPacket.decode(ctx.encode())
        assert decoded.config_epoch == 0
        assert decoded.class_id is None

    def test_explicit_zero_epoch_equals_default(self):
        a = VRTContextPacket(
            stream_id=0x01000000, timestamp=self._ts(), sample_rate_hz=SR, gain_db=20.0
        ).encode()
        b = VRTContextPacket(
            stream_id=0x01000000,
            timestamp=self._ts(),
            sample_rate_hz=SR,
            gain_db=20.0,
            config_epoch=0,
        ).encode()
        assert a == b

    def test_context_fields_survive_when_epoch_set(self):
        ctx = VRTContextPacket(
            stream_id=0x01000000,
            timestamp=self._ts(),
            sample_rate_hz=SR,
            gain_db=20.0,
            bandwidth_hz=20e6,
            config_epoch=5,
        )
        decoded = VRTContextPacket.decode(ctx.encode())
        assert decoded.stream_id == 0x01000000
        assert decoded.sample_rate_hz == SR
        assert decoded.gain_db == 20.0
        assert decoded.bandwidth_hz == 20e6
        assert decoded.config_epoch == 5


@pytest.mark.parametrize("epoch", [1, 2, 100, 0x7FFF, 0xFFFF])
def test_data_packet_epoch_values(epoch):
    p = VRTSignalDataPacket.from_iq_samples(
        _iq(), STREAM_ID, SR, timestamp=TS_FIXED, config_epoch=epoch
    )
    assert VRTSignalDataPacket.decode(p.encode()).config_epoch == epoch
