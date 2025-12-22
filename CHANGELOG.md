# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-12-21

### Initial Release

First public release of VITA49 Pluto Streamer.

### Added

**Core Implementation:**
- C streamer for ADALM-Pluto ARM processor (50 KB, minimal footprint)
- Python VITA49 library for host-side processing
- Network-based configuration via VITA49 Context packets
- Multi-threaded architecture (control + streaming threads)
- Full VITA 49.0 compliance (Signal Data + Context packets)

**Python Library (src/vita49/):**
- Complete VITA49 packet encoder/decoder
- VITA49StreamServer and VITA49StreamClient
- Configuration client for remote SDR control
- Support for multiple simultaneous receivers

**Examples and Tools:**
- Signal processing harness with energy detection
- Real-time spectrum plotting receiver
- Parallel receiver framework
- Packet inspector utility
- NATS bridge for message bus integration

**Testing:**
- Comprehensive unit test suite
- End-to-end test pipeline
- Simulation mode for testing without hardware
- Pytest configuration with fixtures

**Build System:**
- Cross-platform Makefile (Linux/macOS/Windows)
- Docker-based build environment
- Automated deployment scripts
- Support for WSL, native toolchains, and Docker

**Documentation:**
- Comprehensive BUILD.md guide
- Complete USAGE.md with examples
- DEVELOPMENT.md with architecture details
- Professional README.md
- CONTRIBUTING.md guidelines

**Infrastructure:**
- Modern Python packaging (pyproject.toml)
- MIT License
- Requirements files for host and device
- EditorConfig for consistent formatting
- Comprehensive .gitignore

### Performance

- Binary size: 50 KB (300x smaller than Python)
- RAM usage: ~2 MB (vs 15 MB for Python)
- CPU usage: 20-30% at 30 MSPS on Pluto ARM
- Latency: 1-2 ms (UDP + buffering)
- Sample rates: 2-61 MSPS (AD9361 limits)

### Migration

This release represents a major cleanup from the development workspace:
- Reorganized file structure for clarity
- Consolidated 8 documentation files into 4 comprehensive guides
- Created proper Python package structure
- Added professional project files
- See MIGRATION_PLAN.md for detailed reorganization history

## [Unreleased]

### Planned

- Additional signal processing algorithms
- GUI configuration tool
- Windows native build support
- Performance optimizations (SIMD)
- Additional SDR platform support

---

For upgrade instructions and breaking changes, see [docs/USAGE.md](docs/USAGE.md).
