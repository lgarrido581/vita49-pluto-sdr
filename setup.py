#!/usr/bin/env python3
"""
VITA 49 IQ Streaming Library for ADALM-Pluto+ SDR

A complete VITA 49 (VRT) implementation for streaming IQ data from
Analog Devices ADALM-Pluto+ SDRs. Built on top of the libiio/pyadi-iio
stack for seamless integration with existing ADI tools.

Installation:
    pip install .

    # With development dependencies:
    pip install -e ".[dev]"

    # For ARM deployment (Pluto+ processor):
    pip install . --target=/path/to/pluto/rootfs

Usage:
    # Start streaming server
    python -m vita49_pluto.server --uri ip:pluto.local --dest 192.168.2.100

    # Run signal processing client
    python -m vita49_pluto.client --port 4991

Author: Pluto+ Radar Emulator Project
License: MIT
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_path = Path(__file__).parent / "README.md"
long_description = ""
if readme_path.exists():
    long_description = readme_path.read_text()

setup(
    name="vita49-pluto",
    version="0.1.0",
    author="Pluto+ Radar Emulator Project",
    author_email="",
    description="VITA 49 IQ streaming library for ADALM-Pluto+ SDR",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/your-repo/vita49-pluto",
    license="MIT",

    packages=find_packages(),
    py_modules=[
        "vita49_packets",
        "vita49_stream_server",
        "vita49_nats_bridge",
        "signal_processing_harness"
    ],

    python_requires=">=3.8",

    install_requires=[
        "numpy>=1.20.0",
    ],

    extras_require={
        "sdr": [
            "pyadi-iio>=0.0.14",
        ],
        "nats": [
            "nats-py>=2.0.0",
        ],
        "processing": [
            "scipy>=1.7.0",
        ],
        "full": [
            "pyadi-iio>=0.0.14",
            "nats-py>=2.0.0",
            "scipy>=1.7.0",
        ],
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.20.0",
            "black>=22.0.0",
            "mypy>=0.990",
        ],
    },

    entry_points={
        "console_scripts": [
            "vita49-server=vita49_stream_server:main",
            "vita49-bridge=vita49_nats_bridge:main",
            "vita49-processor=signal_processing_harness:main",
        ],
    },

    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Scientific/Engineering",
        "Topic :: Communications :: Ham Radio",
    ],

    keywords=[
        "vita49", "vrt", "sdr", "radio", "iq-data", "streaming",
        "adalm-pluto", "analog-devices", "libiio", "radar"
    ],
)
