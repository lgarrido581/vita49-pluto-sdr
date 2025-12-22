"""
VITA49 Python Library for Pluto SDR Streaming

This library provides VITA49 packet encoding/decoding, streaming server,
and configuration client for use with ADALM-Pluto SDR.
"""

__version__ = "1.0.0"

# Import main classes from submodules
from .packets import (
    # Enums
    PacketType,
    TSI,
    TSF,
    RealOrComplex,
    DataItemFormat,
    # Classes
    VRTHeader,
    VRTClassID,
    VRTTimestamp,
    VRTTrailer,
    VRTSignalDataPacket,
    ContextIndicatorField,
    VRTContextPacket,
)

from .stream_server import (
    # Enums
    StreamMode,
    GainMode,
    # Classes
    StreamConfig,
    SDRConfig,
    StreamStatistics,
    PlutoSDRInterface,
    SimulatedSDRInterface,
    VITA49StreamServer,
    VITA49StreamClient,
)

# Note: config_client is typically used as a script, not imported
# But we can expose it if needed

__all__ = [
    # Version
    '__version__',

    # Packet classes
    'PacketType',
    'TSI',
    'TSF',
    'RealOrComplex',
    'DataItemFormat',
    'VRTHeader',
    'VRTClassID',
    'VRTTimestamp',
    'VRTTrailer',
    'VRTSignalDataPacket',
    'ContextIndicatorField',
    'VRTContextPacket',

    # Stream server classes
    'StreamMode',
    'GainMode',
    'StreamConfig',
    'SDRConfig',
    'StreamStatistics',
    'PlutoSDRInterface',
    'SimulatedSDRInterface',
    'VITA49StreamServer',
    'VITA49StreamClient',
]
