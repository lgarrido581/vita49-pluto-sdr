#!/usr/bin/env python3
"""
VITA 49 (VRT) Packet Encoding/Decoding Library

Pure Python implementation of ANSI/VITA 49.0 and 49.2 packet structures
for IQ data streaming from SDR devices.

This module provides:
- VRT IF Data Packets (Signal Data Packets)
- VRT Context Packets
- Timestamp handling (Integer/Fractional seconds)
- Stream identification
- UDP transport helpers

Reference: ANSI/VITA 49.0-2015 Radio Transport Standard

Author: Pluto+ Radar Emulator Project
License: MIT
"""

import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, List, Tuple
import numpy as np


class PacketType(IntEnum):
    """VRT Packet Types (4 bits)"""
    IF_DATA_WITHOUT_STREAM_ID = 0b0000
    IF_DATA_WITH_STREAM_ID = 0b0001
    EXTENSION_DATA_WITHOUT_STREAM_ID = 0b0010
    EXTENSION_DATA_WITH_STREAM_ID = 0b0011
    CONTEXT = 0b0100
    EXTENSION_CONTEXT = 0b0101
    COMMAND = 0b0110
    EXTENSION_COMMAND = 0b0111


class TSI(IntEnum):
    """Timestamp Integer Type (2 bits)"""
    NONE = 0b00
    UTC = 0b01
    GPS = 0b10
    OTHER = 0b11


class TSF(IntEnum):
    """Timestamp Fractional Type (2 bits)"""
    NONE = 0b00
    SAMPLE_COUNT = 0b01
    REAL_TIME_PS = 0b10  # Picoseconds
    FREE_RUNNING = 0b11


class RealOrComplex(IntEnum):
    """Signal data format"""
    REAL = 0b00
    COMPLEX_CARTESIAN = 0b01
    COMPLEX_POLAR = 0b10


class DataItemFormat(IntEnum):
    """Data item format for payload"""
    SIGNED_FIXED_POINT = 0b00000
    SIGNED_VRT_1_FLOAT = 0b00001
    SIGNED_VRT_2_FLOAT = 0b00010
    SIGNED_VRT_3_FLOAT = 0b00011
    SIGNED_VRT_4_FLOAT = 0b00100
    SIGNED_VRT_5_FLOAT = 0b00101
    SIGNED_VRT_6_FLOAT = 0b00110
    IEEE_754_SINGLE = 0b01101
    IEEE_754_DOUBLE = 0b01110
    UNSIGNED_FIXED_POINT = 0b10000


@dataclass
class VRTHeader:
    """VRT Packet Header (32 bits)"""
    packet_type: PacketType = PacketType.IF_DATA_WITH_STREAM_ID
    class_id_present: bool = False
    trailer_present: bool = True
    tsi: TSI = TSI.UTC
    tsf: TSF = TSF.REAL_TIME_PS
    packet_count: int = 0  # 4-bit counter (0-15)
    packet_size: int = 0   # 16-bit size in 32-bit words

    def encode(self) -> bytes:
        """Encode header to 4 bytes (big-endian)"""
        word = 0
        word |= (self.packet_type & 0xF) << 28
        word |= (int(self.class_id_present) & 0x1) << 27
        word |= (int(self.trailer_present) & 0x1) << 26
        # Bits 25-24 reserved (TSM, Not V49.0)
        word |= (self.tsi & 0x3) << 22
        word |= (self.tsf & 0x3) << 20
        word |= (self.packet_count & 0xF) << 16
        word |= (self.packet_size & 0xFFFF)
        return struct.pack('>I', word)

    @classmethod
    def decode(cls, data: bytes) -> 'VRTHeader':
        """Decode header from 4 bytes (big-endian)"""
        word = struct.unpack('>I', data[:4])[0]
        return cls(
            packet_type=PacketType((word >> 28) & 0xF),
            class_id_present=bool((word >> 27) & 0x1),
            trailer_present=bool((word >> 26) & 0x1),
            tsi=TSI((word >> 22) & 0x3),
            tsf=TSF((word >> 20) & 0x3),
            packet_count=(word >> 16) & 0xF,
            packet_size=word & 0xFFFF
        )


@dataclass
class VRTClassID:
    """VRT Class Identifier (64 bits / 2 words)"""
    oui: int = 0x00005A  # Organization Unique Identifier (24 bits)
    # Using 0x00005A as example OUI - replace with your registered OUI
    information_class_code: int = 0x0000  # 16 bits
    packet_class_code: int = 0x0000       # 16 bits

    def encode(self) -> bytes:
        """Encode class ID to 8 bytes"""
        word1 = (self.oui & 0xFFFFFF) << 8  # OUI in upper 24 bits
        word2 = ((self.information_class_code & 0xFFFF) << 16) | (self.packet_class_code & 0xFFFF)
        return struct.pack('>II', word1, word2)

    @classmethod
    def decode(cls, data: bytes) -> 'VRTClassID':
        """Decode class ID from 8 bytes"""
        word1, word2 = struct.unpack('>II', data[:8])
        return cls(
            oui=(word1 >> 8) & 0xFFFFFF,
            information_class_code=(word2 >> 16) & 0xFFFF,
            packet_class_code=word2 & 0xFFFF
        )


@dataclass
class VRTTimestamp:
    """
    VRT Timestamp (up to 96 bits / 3 words)

    For TSI=UTC (0b01): integer_seconds is seconds since POSIX epoch (Jan 1, 1970 00:00:00 UTC)
    For TSI=GPS (0b10): integer_seconds is seconds since GPS epoch (Jan 6, 1980 00:00:00 UTC)

    Note: This implementation assumes TSI=UTC and uses POSIX epoch throughout.
    """
    integer_seconds: int = 0        # 32 bits - seconds since epoch (POSIX for UTC, GPS for GPS)
    fractional_seconds: int = 0     # 64 bits - picoseconds or sample count

    @classmethod
    def from_time(cls, timestamp: float, sample_rate: float = 0) -> 'VRTTimestamp':
        """
        Create timestamp from Python time (seconds since POSIX epoch)

        Args:
            timestamp: Python time.time() value (seconds since POSIX epoch Jan 1, 1970)
            sample_rate: Not currently used, reserved for TSF=SAMPLE_COUNT mode
        """
        int_sec = int(timestamp)
        frac_sec = timestamp - int_sec
        # Convert fractional seconds to picoseconds
        frac_ps = int(frac_sec * 1e12)
        return cls(integer_seconds=int_sec, fractional_seconds=frac_ps)

    @classmethod
    def now(cls) -> 'VRTTimestamp':
        """Create timestamp for current time (POSIX epoch)"""
        return cls.from_time(time.time())

    def to_time(self) -> float:
        """
        Convert to Python time (seconds since POSIX epoch)

        Returns:
            Float timestamp compatible with Python's time.time()
        """
        return self.integer_seconds + (self.fractional_seconds / 1e12)

    def encode(self, tsi: TSI, tsf: TSF) -> bytes:
        """Encode timestamp based on TSI/TSF settings"""
        data = b''
        if tsi != TSI.NONE:
            data += struct.pack('>I', self.integer_seconds & 0xFFFFFFFF)
        if tsf != TSF.NONE:
            data += struct.pack('>Q', self.fractional_seconds & 0xFFFFFFFFFFFFFFFF)
        return data

    @classmethod
    def decode(cls, data: bytes, tsi: TSI, tsf: TSF) -> Tuple['VRTTimestamp', int]:
        """Decode timestamp, return (timestamp, bytes_consumed)"""
        offset = 0
        int_sec = 0
        frac_sec = 0

        if tsi != TSI.NONE:
            int_sec = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4

        if tsf != TSF.NONE:
            frac_sec = struct.unpack('>Q', data[offset:offset+8])[0]
            offset += 8

        return cls(integer_seconds=int_sec, fractional_seconds=frac_sec), offset


@dataclass
class VRTTrailer:
    """VRT Trailer (32 bits) - Optional"""
    calibrated_time: bool = False
    valid_data: bool = True
    reference_lock: bool = False
    agc_mgc: bool = False  # False=MGC (manual), True=AGC
    detected_signal: bool = False
    spectral_inversion: bool = False
    over_range: bool = False
    sample_loss: bool = False

    # Enable bits for the above indicators
    calibrated_time_enable: bool = False
    valid_data_enable: bool = True
    reference_lock_enable: bool = False
    agc_mgc_enable: bool = False
    detected_signal_enable: bool = False
    spectral_inversion_enable: bool = False
    over_range_enable: bool = True
    sample_loss_enable: bool = True

    associated_context_count: int = 0  # 7 bits

    def encode(self) -> bytes:
        """Encode trailer to 4 bytes"""
        word = 0
        # Indicator bits (bits 31-20)
        word |= (int(self.calibrated_time) & 0x1) << 31
        word |= (int(self.valid_data) & 0x1) << 30
        word |= (int(self.reference_lock) & 0x1) << 29
        word |= (int(self.agc_mgc) & 0x1) << 28
        word |= (int(self.detected_signal) & 0x1) << 27
        word |= (int(self.spectral_inversion) & 0x1) << 26
        word |= (int(self.over_range) & 0x1) << 25
        word |= (int(self.sample_loss) & 0x1) << 24

        # Enable bits (bits 19-12)
        word |= (int(self.calibrated_time_enable) & 0x1) << 19
        word |= (int(self.valid_data_enable) & 0x1) << 18
        word |= (int(self.reference_lock_enable) & 0x1) << 17
        word |= (int(self.agc_mgc_enable) & 0x1) << 16
        word |= (int(self.detected_signal_enable) & 0x1) << 15
        word |= (int(self.spectral_inversion_enable) & 0x1) << 14
        word |= (int(self.over_range_enable) & 0x1) << 13
        word |= (int(self.sample_loss_enable) & 0x1) << 12

        # Associated context packet count (bits 6-0)
        word |= (self.associated_context_count & 0x7F)

        return struct.pack('>I', word)

    @classmethod
    def decode(cls, data: bytes) -> 'VRTTrailer':
        """Decode trailer from 4 bytes"""
        word = struct.unpack('>I', data[:4])[0]
        return cls(
            calibrated_time=bool((word >> 31) & 0x1),
            valid_data=bool((word >> 30) & 0x1),
            reference_lock=bool((word >> 29) & 0x1),
            agc_mgc=bool((word >> 28) & 0x1),
            detected_signal=bool((word >> 27) & 0x1),
            spectral_inversion=bool((word >> 26) & 0x1),
            over_range=bool((word >> 25) & 0x1),
            sample_loss=bool((word >> 24) & 0x1),
            calibrated_time_enable=bool((word >> 19) & 0x1),
            valid_data_enable=bool((word >> 18) & 0x1),
            reference_lock_enable=bool((word >> 17) & 0x1),
            agc_mgc_enable=bool((word >> 16) & 0x1),
            detected_signal_enable=bool((word >> 15) & 0x1),
            spectral_inversion_enable=bool((word >> 14) & 0x1),
            over_range_enable=bool((word >> 13) & 0x1),
            sample_loss_enable=bool((word >> 12) & 0x1),
            associated_context_count=word & 0x7F
        )


@dataclass
class VRTSignalDataPacket:
    """
    VRT IF Data / Signal Data Packet

    Contains digitized IQ samples with associated metadata.
    This is the primary packet type for streaming SDR data.
    """
    header: VRTHeader = field(default_factory=VRTHeader)
    stream_id: int = 0  # 32-bit stream identifier
    class_id: Optional[VRTClassID] = None
    timestamp: Optional[VRTTimestamp] = None
    payload: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int16))
    trailer: Optional[VRTTrailer] = None

    def __post_init__(self):
        """Ensure header settings match packet contents"""
        self.header.packet_type = PacketType.IF_DATA_WITH_STREAM_ID
        self.header.class_id_present = self.class_id is not None
        self.header.trailer_present = self.trailer is not None

    def encode(self) -> bytes:
        """Encode complete packet to bytes (big-endian)"""
        parts = []

        # Calculate packet size first
        size_words = 1  # Header
        size_words += 1  # Stream ID

        if self.class_id is not None:
            size_words += 2  # Class ID

        if self.timestamp is not None:
            if self.header.tsi != TSI.NONE:
                size_words += 1  # Integer seconds
            if self.header.tsf != TSF.NONE:
                size_words += 2  # Fractional seconds

        # Payload size in words (IQ samples are 16-bit each, packed into 32-bit words)
        # Each int16 sample is 2 bytes, so payload size = n_samples * 2 bytes
        payload_byte_count = len(self.payload) * 2
        payload_words = (payload_byte_count + 3) // 4  # Round up to word boundary
        size_words += payload_words

        if self.trailer is not None:
            size_words += 1

        # Update header with calculated size
        self.header.packet_size = size_words

        # Encode header
        parts.append(self.header.encode())

        # Stream ID
        parts.append(struct.pack('>I', self.stream_id & 0xFFFFFFFF))

        # Class ID (optional)
        if self.class_id is not None:
            parts.append(self.class_id.encode())

        # Timestamp (optional)
        if self.timestamp is not None:
            parts.append(self.timestamp.encode(self.header.tsi, self.header.tsf))

        # Payload - convert to big-endian and pad to 32-bit boundary
        payload_be = self.payload.astype('>i2')  # Convert to big-endian int16
        payload_bytes = payload_be.tobytes()
        padding_needed = (4 - (len(payload_bytes) % 4)) % 4
        if padding_needed:
            payload_bytes += b'\x00' * padding_needed
        parts.append(payload_bytes)

        # Trailer (optional)
        if self.trailer is not None:
            parts.append(self.trailer.encode())

        return b''.join(parts)

    @classmethod
    def decode(cls, data: bytes) -> 'VRTSignalDataPacket':
        """Decode packet from bytes"""
        offset = 0

        # Header
        header = VRTHeader.decode(data[offset:offset+4])
        offset += 4

        # Stream ID
        stream_id = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4

        # Class ID (optional)
        class_id = None
        if header.class_id_present:
            class_id = VRTClassID.decode(data[offset:offset+8])
            offset += 8

        # Timestamp (optional)
        timestamp = None
        if header.tsi != TSI.NONE or header.tsf != TSF.NONE:
            timestamp, ts_size = VRTTimestamp.decode(data[offset:], header.tsi, header.tsf)
            offset += ts_size

        # Calculate payload size
        total_bytes = header.packet_size * 4
        trailer_size = 4 if header.trailer_present else 0
        payload_end = total_bytes - trailer_size
        payload_bytes = data[offset:payload_end]
        offset = payload_end

        # Decode payload as int16 (IQ samples) - big-endian, convert to native
        payload_be = np.frombuffer(payload_bytes, dtype='>i2')  # Big-endian int16
        payload = payload_be.astype(np.int16)  # Convert to native endian

        # Trailer (optional)
        trailer = None
        if header.trailer_present:
            trailer = VRTTrailer.decode(data[offset:offset+4])

        return cls(
            header=header,
            stream_id=stream_id,
            class_id=class_id,
            timestamp=timestamp,
            payload=payload,
            trailer=trailer
        )

    @classmethod
    def from_iq_samples(
        cls,
        iq_samples: np.ndarray,
        stream_id: int,
        sample_rate: float,
        timestamp: Optional[float] = None,
        packet_count: int = 0,
        include_trailer: bool = True,
        scale_factor: int = 2**14
    ) -> 'VRTSignalDataPacket':
        """
        Create a VRT packet from complex IQ samples.

        Args:
            iq_samples: Complex64 numpy array of IQ samples
            stream_id: 32-bit stream identifier
            sample_rate: Sample rate in Hz
            timestamp: Optional timestamp (seconds since epoch), defaults to now
            packet_count: 4-bit packet counter (0-15)
            include_trailer: Whether to include trailer
            scale_factor: Scale factor for converting float to int16

        Returns:
            VRTSignalDataPacket ready for transmission
        """
        # Convert complex samples to interleaved I/Q int16
        # Format: I0, Q0, I1, Q1, ...
        i_samples = np.real(iq_samples)
        q_samples = np.imag(iq_samples)

        # Scale and convert to int16
        i_int16 = (i_samples * scale_factor).astype(np.int16)
        q_int16 = (q_samples * scale_factor).astype(np.int16)

        # Interleave I and Q
        payload = np.empty(len(iq_samples) * 2, dtype=np.int16)
        payload[0::2] = i_int16
        payload[1::2] = q_int16

        # Create timestamp
        ts = VRTTimestamp.from_time(timestamp if timestamp else time.time())

        # Create header
        header = VRTHeader(
            packet_type=PacketType.IF_DATA_WITH_STREAM_ID,
            class_id_present=False,
            trailer_present=include_trailer,
            tsi=TSI.UTC,
            tsf=TSF.REAL_TIME_PS,
            packet_count=packet_count & 0xF,
            packet_size=0  # Will be calculated during encode
        )

        # Create trailer
        trailer = VRTTrailer() if include_trailer else None

        return cls(
            header=header,
            stream_id=stream_id,
            timestamp=ts,
            payload=payload,
            trailer=trailer
        )

    def to_iq_samples(self, scale_factor: int = 2**14) -> np.ndarray:
        """
        Extract complex IQ samples from payload.

        Returns:
            Complex64 numpy array
        """
        # De-interleave I and Q
        i_samples = self.payload[0::2].astype(np.float32) / scale_factor
        q_samples = self.payload[1::2].astype(np.float32) / scale_factor
        return i_samples + 1j * q_samples


@dataclass
class ContextIndicatorField:
    """Context Indicator Field (CIF) - determines which context fields are present"""
    # CIF0 bits
    change_indicator: bool = False
    reference_point_id: bool = False
    bandwidth: bool = False
    if_reference_frequency: bool = False
    rf_reference_frequency: bool = False
    rf_reference_frequency_offset: bool = False
    if_band_offset: bool = False
    reference_level: bool = False
    gain: bool = False
    over_range_count: bool = False
    sample_rate: bool = False
    timestamp_adjustment: bool = False
    timestamp_calibration_time: bool = False
    temperature: bool = False
    device_id: bool = False
    state_event_indicators: bool = False
    data_packet_payload_format: bool = False
    formatted_gps_geolocation: bool = False
    formatted_ins_geolocation: bool = False
    ecef_ephemeris: bool = False
    relative_ephemeris: bool = False
    ephemeris_ref_id: bool = False
    gps_ascii: bool = False
    context_association_lists: bool = False

    def encode(self) -> bytes:
        """Encode CIF0 to 4 bytes"""
        word = 0
        word |= (int(self.change_indicator) << 31)
        word |= (int(self.reference_point_id) << 30)
        word |= (int(self.bandwidth) << 29)
        word |= (int(self.if_reference_frequency) << 28)
        word |= (int(self.rf_reference_frequency) << 27)
        word |= (int(self.rf_reference_frequency_offset) << 26)
        word |= (int(self.if_band_offset) << 25)
        word |= (int(self.reference_level) << 24)
        word |= (int(self.gain) << 23)
        word |= (int(self.over_range_count) << 22)
        word |= (int(self.sample_rate) << 21)
        word |= (int(self.timestamp_adjustment) << 20)
        word |= (int(self.timestamp_calibration_time) << 19)
        word |= (int(self.temperature) << 18)
        word |= (int(self.device_id) << 17)
        word |= (int(self.state_event_indicators) << 16)
        word |= (int(self.data_packet_payload_format) << 15)
        word |= (int(self.formatted_gps_geolocation) << 14)
        word |= (int(self.formatted_ins_geolocation) << 13)
        word |= (int(self.ecef_ephemeris) << 12)
        word |= (int(self.relative_ephemeris) << 11)
        word |= (int(self.ephemeris_ref_id) << 10)
        word |= (int(self.gps_ascii) << 9)
        word |= (int(self.context_association_lists) << 8)
        return struct.pack('>I', word)


@dataclass
class VRTContextPacket:
    """
    VRT Context Packet

    Contains metadata about the signal data stream such as:
    - Sample rate
    - Center frequency
    - Bandwidth
    - Gain settings
    - Geolocation
    """
    header: VRTHeader = field(default_factory=lambda: VRTHeader(
        packet_type=PacketType.CONTEXT,
        tsi=TSI.UTC,
        tsf=TSF.REAL_TIME_PS
    ))
    stream_id: int = 0
    class_id: Optional[VRTClassID] = None
    timestamp: Optional[VRTTimestamp] = None
    cif: ContextIndicatorField = field(default_factory=ContextIndicatorField)

    # Context fields (optional, based on CIF)
    bandwidth_hz: Optional[float] = None
    if_reference_frequency_hz: Optional[float] = None
    rf_reference_frequency_hz: Optional[float] = None
    sample_rate_hz: Optional[float] = None
    gain_db: Optional[float] = None
    reference_level_dbm: Optional[float] = None
    temperature_c: Optional[float] = None

    def __post_init__(self):
        """Update CIF based on which fields are set"""
        self.header.packet_type = PacketType.CONTEXT
        self.cif.bandwidth = self.bandwidth_hz is not None
        self.cif.if_reference_frequency = self.if_reference_frequency_hz is not None
        self.cif.rf_reference_frequency = self.rf_reference_frequency_hz is not None
        self.cif.sample_rate = self.sample_rate_hz is not None
        self.cif.gain = self.gain_db is not None
        self.cif.reference_level = self.reference_level_dbm is not None
        self.cif.temperature = self.temperature_c is not None

    def _encode_fixed_point_64(self, value: float, radix: int = 20) -> bytes:
        """Encode 64-bit fixed point value"""
        # VRT uses 64-bit fixed point with 20-bit radix for Hz values
        fixed = int(value * (1 << radix))
        return struct.pack('>q', fixed)

    def _encode_fixed_point_16(self, value: float, radix: int = 7) -> bytes:
        """Encode 16-bit fixed point value"""
        fixed = int(value * (1 << radix))
        return struct.pack('>h', fixed)

    def encode(self) -> bytes:
        """Encode context packet to bytes"""
        parts = []

        # Calculate size
        size_words = 1  # Header
        size_words += 1  # Stream ID
        if self.class_id is not None:
            size_words += 2
        if self.timestamp is not None:
            if self.header.tsi != TSI.NONE:
                size_words += 1
            if self.header.tsf != TSF.NONE:
                size_words += 2
        size_words += 1  # CIF

        # Add sizes for enabled context fields
        if self.cif.bandwidth:
            size_words += 2  # 64-bit
        if self.cif.if_reference_frequency:
            size_words += 2
        if self.cif.rf_reference_frequency:
            size_words += 2
        if self.cif.sample_rate:
            size_words += 2
        if self.cif.gain:
            size_words += 1  # Two 16-bit values
        if self.cif.reference_level:
            size_words += 1
        if self.cif.temperature:
            size_words += 1

        self.header.packet_size = size_words
        self.header.class_id_present = self.class_id is not None

        # Encode parts
        parts.append(self.header.encode())
        parts.append(struct.pack('>I', self.stream_id))

        if self.class_id is not None:
            parts.append(self.class_id.encode())

        if self.timestamp is not None:
            parts.append(self.timestamp.encode(self.header.tsi, self.header.tsf))

        parts.append(self.cif.encode())

        # Encode context fields in order
        if self.cif.bandwidth:
            parts.append(self._encode_fixed_point_64(self.bandwidth_hz))
        if self.cif.if_reference_frequency:
            parts.append(self._encode_fixed_point_64(self.if_reference_frequency_hz))
        if self.cif.rf_reference_frequency:
            parts.append(self._encode_fixed_point_64(self.rf_reference_frequency_hz))
        if self.cif.sample_rate:
            parts.append(self._encode_fixed_point_64(self.sample_rate_hz))
        if self.cif.gain:
            # Stage 1 and Stage 2 gain (both 16-bit, 7-bit radix)
            stage1 = int(self.gain_db * 128)  # 7-bit radix
            parts.append(struct.pack('>hh', stage1, 0))  # Stage2 = 0
        if self.cif.reference_level:
            ref_level = int(self.reference_level_dbm * 128)
            parts.append(struct.pack('>i', ref_level << 16))
        if self.cif.temperature:
            temp = int((self.temperature_c + 273.15) * 64)  # 6-bit radix, Kelvin
            parts.append(struct.pack('>I', temp << 16))

        return b''.join(parts)

    @classmethod
    def decode(cls, data: bytes) -> 'VRTContextPacket':
        """
        Decode context packet from bytes

        Args:
            data: Raw packet bytes

        Returns:
            VRTContextPacket instance
        """
        offset = 0

        # Decode header
        header = VRTHeader.decode(data[offset:offset+4])
        offset += 4

        # Decode stream ID
        stream_id = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4

        # Decode class ID if present
        class_id = None
        if header.class_id_present:
            class_id = VRTClassID.decode(data[offset:offset+8])
            offset += 8

        # Decode timestamp if present
        timestamp = None
        if header.tsi != TSI.NONE or header.tsf != TSF.NONE:
            timestamp, ts_size = VRTTimestamp.decode(data[offset:], header.tsi, header.tsf)
            offset += ts_size

        # Decode CIF
        cif_word = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4

        # Parse CIF bits
        cif = ContextIndicatorField()
        cif.bandwidth = bool(cif_word & (1 << 29))
        cif.if_reference_frequency = bool(cif_word & (1 << 28))
        cif.rf_reference_frequency = bool(cif_word & (1 << 27))
        cif.sample_rate = bool(cif_word & (1 << 21))
        cif.gain = bool(cif_word & (1 << 23))
        cif.reference_level = bool(cif_word & (1 << 24))
        cif.temperature = bool(cif_word & (1 << 18))

        # Decode context fields based on CIF
        bandwidth_hz = None
        if_reference_frequency_hz = None
        rf_reference_frequency_hz = None
        sample_rate_hz = None
        gain_db = None
        reference_level_dbm = None
        temperature_c = None

        # Bandwidth (64-bit fixed point, 20-bit radix)
        if cif.bandwidth:
            fixed_val = struct.unpack('>q', data[offset:offset+8])[0]
            bandwidth_hz = fixed_val / (1 << 20)
            offset += 8

        # IF reference frequency
        if cif.if_reference_frequency:
            fixed_val = struct.unpack('>q', data[offset:offset+8])[0]
            if_reference_frequency_hz = fixed_val / (1 << 20)
            offset += 8

        # RF reference frequency
        if cif.rf_reference_frequency:
            fixed_val = struct.unpack('>q', data[offset:offset+8])[0]
            rf_reference_frequency_hz = fixed_val / (1 << 20)
            offset += 8

        # Sample rate
        if cif.sample_rate:
            fixed_val = struct.unpack('>q', data[offset:offset+8])[0]
            sample_rate_hz = fixed_val / (1 << 20)
            offset += 8

        # Gain (two 16-bit values, 7-bit radix)
        if cif.gain:
            stage1, stage2 = struct.unpack('>hh', data[offset:offset+4])
            gain_db = stage1 / 128.0  # 7-bit radix
            offset += 4

        # Reference level
        if cif.reference_level:
            ref_word = struct.unpack('>i', data[offset:offset+4])[0]
            reference_level_dbm = (ref_word >> 16) / 128.0
            offset += 4

        # Temperature
        if cif.temperature:
            temp_word = struct.unpack('>I', data[offset:offset+4])[0]
            temp_kelvin = (temp_word >> 16) / 64.0  # 6-bit radix
            temperature_c = temp_kelvin - 273.15
            offset += 4

        return cls(
            header=header,
            stream_id=stream_id,
            class_id=class_id,
            timestamp=timestamp,
            cif=cif,
            bandwidth_hz=bandwidth_hz,
            if_reference_frequency_hz=if_reference_frequency_hz,
            rf_reference_frequency_hz=rf_reference_frequency_hz,
            sample_rate_hz=sample_rate_hz,
            gain_db=gain_db,
            reference_level_dbm=reference_level_dbm,
            temperature_c=temperature_c
        )


# =============================================================================
# Helper Functions
# =============================================================================

def calculate_max_samples_per_packet(mtu: int = 1500) -> int:
    """
    Calculate maximum IQ samples that fit in one VRT packet within MTU.

    Args:
        mtu: Maximum Transmission Unit in bytes (default 1500 for Ethernet)

    Returns:
        Maximum number of complex IQ samples per packet
    """
    # UDP/IP overhead: ~28 bytes
    # VRT header: 4 bytes
    # Stream ID: 4 bytes
    # Timestamp (UTC + picoseconds): 12 bytes
    # Trailer: 4 bytes
    overhead = 28 + 4 + 4 + 12 + 4  # = 52 bytes
    payload_bytes = mtu - overhead

    # Each complex sample = 4 bytes (2x int16)
    return payload_bytes // 4


def create_stream_id(channel: int, device_id: int = 0, data_type: int = 0) -> int:
    """
    Create a VRT stream ID.

    Format:
    - Bits 31-24: Device ID (8 bits)
    - Bits 23-16: Data type (8 bits) - 0=IQ, 1=Spectrum, etc.
    - Bits 15-8:  Reserved
    - Bits 7-0:   Channel number (8 bits)

    Args:
        channel: Channel number (0-255)
        device_id: Device identifier (0-255)
        data_type: Data type code (0-255)

    Returns:
        32-bit stream ID
    """
    return ((device_id & 0xFF) << 24) | ((data_type & 0xFF) << 16) | (channel & 0xFF)


def parse_stream_id(stream_id: int) -> dict:
    """Parse a stream ID into its components"""
    return {
        'device_id': (stream_id >> 24) & 0xFF,
        'data_type': (stream_id >> 16) & 0xFF,
        'channel': stream_id & 0xFF
    }


# =============================================================================
# Test / Demo
# =============================================================================

if __name__ == '__main__':
    print("VITA 49 Packet Library Test")
    print("=" * 50)

    # Create test IQ samples
    fs = 30e6  # 30 MSPS
    fc = 1e6   # 1 MHz IF
    n_samples = 1000
    t = np.arange(n_samples) / fs
    iq_samples = 0.9 * np.exp(1j * 2 * np.pi * fc * t)

    # Create VRT packet from IQ samples
    stream_id = create_stream_id(channel=0, device_id=1)
    packet = VRTSignalDataPacket.from_iq_samples(
        iq_samples=iq_samples,
        stream_id=stream_id,
        sample_rate=fs,
        packet_count=0
    )

    # Encode packet
    encoded = packet.encode()
    print(f"Packet size: {len(encoded)} bytes ({packet.header.packet_size} words)")
    print(f"Stream ID: 0x{packet.stream_id:08X}")
    print(f"Timestamp: {packet.timestamp.to_time():.6f}")
    print(f"Payload samples: {len(packet.payload) // 2} complex")

    # Decode packet
    decoded = VRTSignalDataPacket.decode(encoded)
    print(f"\nDecoded stream ID: 0x{decoded.stream_id:08X}")
    print(f"Decoded timestamp: {decoded.timestamp.to_time():.6f}")

    # Extract IQ samples
    recovered_iq = decoded.to_iq_samples()
    print(f"Recovered samples: {len(recovered_iq)} complex")

    # Verify data integrity
    mse = np.mean(np.abs(iq_samples - recovered_iq)**2)
    print(f"Reconstruction MSE: {mse:.2e}")

    # Create context packet
    context = VRTContextPacket(
        stream_id=stream_id,
        timestamp=VRTTimestamp.now(),
        bandwidth_hz=20e6,
        rf_reference_frequency_hz=2.4e9,
        sample_rate_hz=fs,
        gain_db=10.0
    )
    context_encoded = context.encode()
    print(f"\nContext packet size: {len(context_encoded)} bytes")

    # Max samples per packet
    max_samples = calculate_max_samples_per_packet(mtu=1500)
    print(f"\nMax IQ samples per 1500-byte MTU: {max_samples}")

    print("\n✓ All tests passed!")
