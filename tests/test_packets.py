#!/usr/bin/env python3
"""
Unit tests for VITA49 packet encoding/decoding

Tests all packet types, headers, timestamps, and utility functions.
Run with: pytest tests/test_packets.py -v
"""

import pytest
import numpy as np
import struct
import time

from vita49.packets import (
    VRTHeader,
    VRTSignalDataPacket,
    VRTContextPacket,
    VRTTimestamp,
    VRTTrailer,
    VRTClassID,
    ContextIndicatorField,
    PacketType,
    TSI,
    TSF,
    RealOrComplex,
    DataItemFormat,
    create_stream_id,
    parse_stream_id,
    calculate_max_samples_per_packet
)


class TestVRTHeader:
    """Test VRT packet header encoding/decoding"""

    def test_default_header(self):
        """Test creating header with default values"""
        header = VRTHeader()
        assert header.packet_type == PacketType.IF_DATA_WITH_STREAM_ID
        assert header.packet_count == 0
        assert header.packet_size == 0

    def test_header_encode_decode(self):
        """Test header round-trip encoding/decoding"""
        original = VRTHeader(
            packet_type=PacketType.CONTEXT,
            class_id_present=True,
            trailer_present=True,
            tsi=TSI.UTC,
            tsf=TSF.REAL_TIME_PS,
            packet_count=7,
            packet_size=100
        )

        encoded = original.encode()
        assert len(encoded) == 4

        decoded = VRTHeader.decode(encoded)
        assert decoded.packet_type == original.packet_type
        assert decoded.class_id_present == original.class_id_present
        assert decoded.trailer_present == original.trailer_present
        assert decoded.tsi == original.tsi
        assert decoded.tsf == original.tsf
        assert decoded.packet_count == original.packet_count
        assert decoded.packet_size == original.packet_size

    def test_packet_count_wrapping(self):
        """Test that packet count wraps at 16"""
        for count in range(20):
            header = VRTHeader(packet_count=count)
            encoded = header.encode()
            decoded = VRTHeader.decode(encoded)
            assert decoded.packet_count == (count & 0xF)

    def test_all_packet_types(self):
        """Test all defined packet types"""
        for ptype in PacketType:
            header = VRTHeader(packet_type=ptype)
            encoded = header.encode()
            decoded = VRTHeader.decode(encoded)
            assert decoded.packet_type == ptype

    def test_tsi_tsf_combinations(self):
        """Test all TSI/TSF combinations"""
        for tsi in TSI:
            for tsf in TSF:
                header = VRTHeader(tsi=tsi, tsf=tsf)
                encoded = header.encode()
                decoded = VRTHeader.decode(encoded)
                assert decoded.tsi == tsi
                assert decoded.tsf == tsf


class TestVRTTimestamp:
    """Test VRT timestamp handling"""

    def test_timestamp_from_time(self):
        """Test creating timestamp from float"""
        t = 1700000000.123456789
        ts = VRTTimestamp.from_time(t)

        assert ts.integer_seconds == 1700000000
        # Picosecond precision
        assert abs(ts.fractional_seconds - 123456789000) < 1000

    def test_timestamp_to_time(self):
        """Test converting timestamp to float"""
        original = 1700000000.987654321
        ts = VRTTimestamp.from_time(original)
        recovered = ts.to_time()

        # Should be within 1 nanosecond
        assert abs(recovered - original) < 1e-9

    def test_timestamp_now(self):
        """Test creating current timestamp"""
        before = time.time()
        ts = VRTTimestamp.now()
        after = time.time()

        recovered = ts.to_time()
        assert before <= recovered <= after

    def test_timestamp_encode_decode(self):
        """Test timestamp encode/decode round-trip"""
        original = VRTTimestamp(
            integer_seconds=1700000000,
            fractional_seconds=123456789012
        )

        encoded = original.encode(TSI.UTC, TSF.REAL_TIME_PS)
        decoded, size = VRTTimestamp.decode(encoded, TSI.UTC, TSF.REAL_TIME_PS)

        assert size == 12  # 4 bytes int + 8 bytes frac
        assert decoded.integer_seconds == original.integer_seconds
        assert decoded.fractional_seconds == original.fractional_seconds

    def test_timestamp_no_fields(self):
        """Test timestamp with no TSI/TSF"""
        ts = VRTTimestamp()
        encoded = ts.encode(TSI.NONE, TSF.NONE)
        assert len(encoded) == 0

        decoded, size = VRTTimestamp.decode(b'', TSI.NONE, TSF.NONE)
        assert size == 0
        assert decoded.integer_seconds == 0
        assert decoded.fractional_seconds == 0


class TestVRTTrailer:
    """Test VRT packet trailer"""

    def test_trailer_default(self):
        """Test default trailer creation"""
        trailer = VRTTrailer()
        assert trailer.valid_data == True
        assert trailer.reference_lock == False
        assert trailer.agc_mgc == False

    def test_trailer_encode_decode(self):
        """Test trailer round-trip"""
        original = VRTTrailer(
            valid_data=True,
            reference_lock=True,
            agc_mgc=True,
            detected_signal=True,
            spectral_inversion=False,
            over_range=False,
            sample_loss=False
        )

        encoded = original.encode()
        assert len(encoded) == 4

        decoded = VRTTrailer.decode(encoded)
        assert decoded.valid_data == original.valid_data
        assert decoded.reference_lock == original.reference_lock
        assert decoded.agc_mgc == original.agc_mgc
        assert decoded.detected_signal == original.detected_signal


class TestVRTClassID:
    """Test VRT Class ID"""

    def test_class_id_creation(self):
        """Test creating class ID"""
        class_id = VRTClassID(
            oui=0x123456,
            information_class_code=0xABCD,
            packet_class_code=0xEF12
        )

        assert class_id.oui == 0x123456
        assert class_id.information_class_code == 0xABCD
        assert class_id.packet_class_code == 0xEF12

    def test_class_id_encode_decode(self):
        """Test class ID round-trip"""
        original = VRTClassID(
            oui=0xAABBCC,
            information_class_code=0x1234,
            packet_class_code=0x5678
        )

        encoded = original.encode()
        assert len(encoded) == 8

        decoded = VRTClassID.decode(encoded)
        assert decoded.oui == original.oui
        assert decoded.information_class_code == original.information_class_code
        assert decoded.packet_class_code == original.packet_class_code


class TestVRTSignalDataPacket:
    """Test VRT signal data packets"""

    def test_create_from_iq_samples(self):
        """Test creating packet from IQ samples"""
        fs = 30e6
        n_samples = 100
        t = np.arange(n_samples) / fs
        iq = 0.5 * np.exp(1j * 2 * np.pi * 1e6 * t)

        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=iq,
            stream_id=0x12345678,
            sample_rate=fs,
            timestamp=1700000000.0,
            packet_count=5
        )

        assert packet.stream_id == 0x12345678
        assert packet.header.packet_count == 5
        assert len(packet.payload) == n_samples * 2  # I and Q

    def test_packet_encode_decode_roundtrip(self):
        """Test full packet encode/decode"""
        n_samples = 50
        phases = 2 * np.pi * np.random.rand(n_samples)
        iq = 0.5 * np.exp(1j * phases)

        original = VRTSignalDataPacket.from_iq_samples(
            iq_samples=iq,
            stream_id=0xDEADBEEF,
            sample_rate=30e6,
            timestamp=1700000000.5,
            packet_count=3
        )

        encoded = original.encode()
        decoded = VRTSignalDataPacket.decode(encoded)

        assert decoded.stream_id == original.stream_id
        assert decoded.header.packet_count == original.header.packet_count

        # Check timestamp
        assert abs(decoded.timestamp.to_time() - original.timestamp.to_time()) < 1e-9

        # Check samples recovered correctly
        recovered_iq = decoded.to_iq_samples()
        assert len(recovered_iq) == len(iq)
        mse = np.mean(np.abs(iq - recovered_iq)**2)
        assert mse < 1e-6

    def test_packet_with_trailer(self):
        """Test packet with trailer"""
        iq = 0.5 * np.ones(10, dtype=np.complex64)

        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=iq,
            stream_id=0x1234,
            sample_rate=30e6,
            include_trailer=True
        )

        assert packet.trailer is not None
        encoded = packet.encode()
        decoded = VRTSignalDataPacket.decode(encoded)

        assert decoded.header.trailer_present
        assert decoded.trailer is not None

    def test_packet_without_trailer(self):
        """Test packet without trailer"""
        iq = 0.5 * np.ones(10, dtype=np.complex64)

        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=iq,
            stream_id=0x1234,
            sample_rate=30e6,
            include_trailer=False
        )

        assert packet.trailer is None
        encoded = packet.encode()
        decoded = VRTSignalDataPacket.decode(encoded)

        assert not decoded.header.trailer_present
        assert decoded.trailer is None

    def test_different_sample_counts(self):
        """Test packets with various sample counts"""
        for n_samples in [1, 10, 100, 360, 1000]:
            iq = 0.5 * np.random.randn(n_samples) + 1j * 0.5 * np.random.randn(n_samples)
            iq = iq.astype(np.complex64)

            packet = VRTSignalDataPacket.from_iq_samples(
                iq_samples=iq,
                stream_id=0x1234,
                sample_rate=30e6
            )

            encoded = packet.encode()
            decoded = VRTSignalDataPacket.decode(encoded)
            recovered = decoded.to_iq_samples()

            assert len(recovered) == n_samples


class TestVRTContextPacket:
    """Test VRT context packets"""

    def test_context_packet_creation(self):
        """Test creating context packet"""
        context = VRTContextPacket(
            stream_id=0xABCD1234,
            timestamp=VRTTimestamp.from_time(1700000000.0),
            bandwidth_hz=20e6,
            rf_reference_frequency_hz=2.4e9,
            sample_rate_hz=30e6,
            gain_db=15.0
        )

        assert context.cif.bandwidth == True
        assert context.cif.rf_reference_frequency == True
        assert context.cif.sample_rate == True
        assert context.cif.gain == True

    def test_context_packet_encode_decode(self):
        """Test context packet round-trip"""
        original = VRTContextPacket(
            stream_id=0x1234,
            bandwidth_hz=20e6,
            rf_reference_frequency_hz=2.4e9,
            sample_rate_hz=30e6,
            gain_db=20.0
        )

        encoded = original.encode()
        assert len(encoded) > 0

        # Verify header
        header = VRTHeader.decode(encoded[:4])
        assert header.packet_type == PacketType.CONTEXT

    def test_context_packet_optional_fields(self):
        """Test context packet with only some fields"""
        context = VRTContextPacket(
            stream_id=0x5678,
            sample_rate_hz=30e6
        )

        assert context.cif.sample_rate == True
        assert context.cif.bandwidth == False
        assert context.cif.rf_reference_frequency == False

        encoded = context.encode()
        assert len(encoded) > 0


class TestStreamIDHelpers:
    """Test stream ID utility functions"""

    def test_create_stream_id(self):
        """Test stream ID creation"""
        stream_id = create_stream_id(channel=2, device_id=5, data_type=1)

        parsed = parse_stream_id(stream_id)
        assert parsed['channel'] == 2
        assert parsed['device_id'] == 5
        assert parsed['data_type'] == 1

    def test_stream_id_roundtrip(self):
        """Test stream ID encode/decode"""
        for channel in range(4):
            for device_id in [0, 1, 127, 255]:
                for data_type in [0, 1]:
                    stream_id = create_stream_id(
                        channel=channel,
                        device_id=device_id,
                        data_type=data_type
                    )
                    parsed = parse_stream_id(stream_id)
                    assert parsed['channel'] == channel
                    assert parsed['device_id'] == device_id
                    assert parsed['data_type'] == data_type

    def test_stream_id_boundaries(self):
        """Test stream ID boundary values"""
        # Max values
        stream_id = create_stream_id(channel=3, device_id=255, data_type=1)
        parsed = parse_stream_id(stream_id)
        assert parsed['channel'] == 3
        assert parsed['device_id'] == 255

        # Min values
        stream_id = create_stream_id(channel=0, device_id=0, data_type=0)
        parsed = parse_stream_id(stream_id)
        assert parsed['channel'] == 0
        assert parsed['device_id'] == 0


class TestMaxSamplesCalculation:
    """Test MTU-based sample calculation"""

    def test_standard_ethernet_mtu(self):
        """Test with standard 1500 MTU"""
        max_samples = calculate_max_samples_per_packet(mtu=1500)
        assert max_samples > 0
        assert max_samples < 500

    def test_jumbo_frames(self):
        """Test with jumbo frames"""
        max_samples_std = calculate_max_samples_per_packet(mtu=1500)
        max_samples_jumbo = calculate_max_samples_per_packet(mtu=9000)
        assert max_samples_jumbo > max_samples_std

    def test_various_mtus(self):
        """Test various MTU sizes"""
        for mtu in [576, 1500, 4096, 9000]:
            max_samples = calculate_max_samples_per_packet(mtu=mtu)
            assert max_samples > 0
            # Verify packet won't exceed MTU
            # Header(4) + StreamID(4) + Timestamp(12) + Trailer(4) + Samples(4*2*N)
            overhead = 24
            assert (overhead + max_samples * 8) <= mtu


class TestEdgeCases:
    """Test edge cases and error conditions"""

    def test_empty_samples(self):
        """Test packet with zero samples"""
        iq = np.array([], dtype=np.complex64)
        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=iq,
            stream_id=0x1234,
            sample_rate=30e6
        )
        assert len(packet.payload) == 0

    def test_large_stream_id(self):
        """Test with maximum stream ID value"""
        stream_id = 0xFFFFFFFF
        iq = 0.5 * np.ones(10, dtype=np.complex64)

        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=iq,
            stream_id=stream_id,
            sample_rate=30e6
        )

        encoded = packet.encode()
        decoded = VRTSignalDataPacket.decode(encoded)
        assert decoded.stream_id == stream_id

    def test_very_high_frequency(self):
        """Test with high frequency values"""
        context = VRTContextPacket(
            stream_id=0x1234,
            rf_reference_frequency_hz=100e9,  # 100 GHz
            sample_rate_hz=1e9  # 1 GSPS
        )

        encoded = context.encode()
        assert len(encoded) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
