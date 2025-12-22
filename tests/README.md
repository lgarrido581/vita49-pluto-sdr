# VITA49 Test Suite

Comprehensive test suite for the VITA49 Python library for Pluto SDR streaming.

## Test Structure

```
tests/
├── test_packets.py          # Unit tests for VITA49 packet encoding/decoding
├── test_stream_server.py    # Unit tests for streaming server and client
├── test_integration.py      # Integration tests for complete workflows
├── test_vita49.py          # Original comprehensive test suite
├── test_streaming_simple.py # Simple streaming test script
├── test_pluto_config.py    # Pluto+ configuration diagnostic tool
├── run_tests.py            # Test runner (works with or without pytest)
├── e2e/                    # End-to-end test scripts
│   ├── full_pipeline.py    # Complete pipeline orchestration
│   ├── step1_receive_from_pluto.py
│   ├── step2_vita49_restreamer.py
│   └── step3_plotting_receiver.py
└── conftest.py             # Pytest configuration
```

## Running Tests

### Option 1: Using the Test Runner (Recommended)

The test runner works with or without pytest installed:

```bash
# Run all tests
python tests/run_tests.py

# Run only basic import and functionality tests
python tests/run_tests.py --basic

# Run only pytest tests (requires pytest)
python tests/run_tests.py --pytest
```

### Option 2: Using pytest (If Installed)

```bash
# Install pytest
pip install pytest

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_packets.py -v

# Run with coverage
pip install pytest-cov
pytest tests/ --cov=vita49 --cov-report=html

# Run only fast tests (skip slow integration tests)
pytest tests/ -v -m "not slow"

# Run with verbose output
pytest tests/ -v --tb=short
```

### Option 3: Run Individual Test Files

Each test file can be run standalone:

```bash
# Packet tests
python tests/test_packets.py

# Stream server tests
python tests/test_stream_server.py

# Integration tests
python tests/test_integration.py
```

## Test Coverage

### test_packets.py
- **VRTHeader**: Encoding/decoding, packet types, TSI/TSF combinations
- **VRTTimestamp**: Time conversions, encoding/decoding
- **VRTTrailer**: Status flags and encoding
- **VRTClassID**: OUI and packet classification
- **VRTSignalDataPacket**: IQ sample encoding/decoding, round-trip testing
- **VRTContextPacket**: Metadata encoding and parsing
- **Stream ID Helpers**: Stream ID creation and parsing
- **MTU Calculations**: Maximum samples per packet
- **Edge Cases**: Empty packets, large values, boundary conditions

### test_stream_server.py
- **SDRConfig**: Configuration creation and validation
- **StreamConfig**: Stream parameters
- **SimulatedSDR**: Simulated SDR interface testing
- **StreamServer**: Server start/stop, multi-channel streaming
- **StreamClient**: Client operations and callbacks
- **End-to-End**: Server-client communication
- **Statistics**: Throughput and packet tracking
- **Error Conditions**: Error handling and recovery
- **Configuration**: Various sample rates, frequencies, gains

### test_integration.py
- **Packet Integration**: Network packet transmission
- **Server-Client Integration**: Complete streaming pipeline
- **Multi-Channel**: Dual-channel streaming
- **High Throughput**: Performance testing
- **Statistics Accuracy**: Validation of metrics
- **Simulated SDR**: Different signal types
- **Robustness**: Restart scenarios, concurrent clients
- **Performance**: Sustained streaming, packet loss measurement

### test_vita49.py (Original Suite)
- Comprehensive tests for all components
- Signal processing harness integration
- Energy, CFAR, and pulse detection
- Full system integration tests

## End-to-End Tests

### Simple Streaming Test
```bash
# Direct mode - no multiprocessing
python tests/test_streaming_simple.py --pluto-uri ip:pluto.local --freq 103.7e6
```

### Full Pipeline Test
```bash
# Complete pipeline with all components
python tests/e2e/full_pipeline.py --pluto-uri ip:pluto.local --freq 2.4e9
```

### Individual Pipeline Steps
```bash
# Step 1: Receive from Pluto+
python tests/e2e/step1_receive_from_pluto.py --uri ip:pluto.local

# Step 2: VITA49 re-streamer
python tests/e2e/step2_vita49_restreamer.py --pluto-uri ip:pluto.local

# Step 3: Plotting receiver
python tests/e2e/step3_plotting_receiver.py --port 4991
```

### Pluto+ Configuration Test
```bash
# Diagnostic tool for Pluto+ configuration
python tests/test_pluto_config.py --pluto-uri ip:pluto.local --freq 103.7e6 --rate 2e6 --gain 40
```

## Test Markers

Tests can be marked with pytest markers:

- `@pytest.mark.slow` - Long-running tests (>5 seconds)
- `@pytest.mark.integration` - Integration tests requiring multiple components
- `@pytest.mark.hardware` - Tests requiring actual Pluto+ hardware

Skip slow tests:
```bash
pytest tests/ -v -m "not slow"
```

## Writing New Tests

### Unit Test Template

```python
import pytest
from vita49.packets import VRTHeader

class TestMyFeature:
    """Test description"""

    def test_basic_functionality(self):
        """Test basic case"""
        # Arrange
        header = VRTHeader()

        # Act
        result = header.encode()

        # Assert
        assert len(result) == 4

    def test_edge_case(self):
        """Test edge case"""
        # Test implementation
        pass
```

### Integration Test Template

```python
import pytest
import time
from vita49.stream_server import VITA49StreamServer, VITA49StreamClient

class TestIntegration:
    """Integration test description"""

    def test_feature_integration(self):
        """Test complete workflow"""
        # Setup
        client = VITA49StreamClient(port=16000)
        client.start()

        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=16000,
            use_simulation=True
        )
        server.start()

        # Wait for data
        time.sleep(2.0)

        # Verify
        assert client.packets_received > 0

        # Cleanup
        server.stop()
        client.stop()
```

## Continuous Integration

For CI/CD pipelines:

```bash
# Install dependencies
pip install -e .
pip install pytest pytest-cov

# Run tests with coverage
pytest tests/ --cov=vita49 --cov-report=xml --cov-report=term

# Run only fast tests for CI
pytest tests/ -v -m "not slow" --tb=short
```

## Troubleshooting

### Import Errors
If you get import errors, ensure the package is installed:
```bash
pip install -e .
```

### Port Conflicts
If tests fail with "Address already in use", change the test ports or kill processes:
```bash
# Windows
netstat -ano | findstr :4991
taskkill /PID <pid> /F

# Linux/Mac
lsof -i :4991
kill <pid>
```

### Simulated SDR Not Working
The simulated SDR should always work. If it doesn't:
1. Check that numpy is installed: `pip install numpy`
2. Verify imports work: `python -c "from vita49.stream_server import SimulatedSDRInterface"`

### Pluto+ Hardware Tests Failing
For hardware tests with actual Pluto+:
1. Verify Pluto+ is connected and accessible
2. Check URI is correct (e.g., `ip:192.168.2.1` or `ip:pluto.local`)
3. Ensure pyadi-iio is installed: `pip install pyadi-iio`
4. Run diagnostic: `python tests/test_pluto_config.py --pluto-uri ip:pluto.local`

## Test Results

After fixing import paths, all tests should pass:

```
Testing Imports
======================================================================
[PASS] vita49.packets: VRTHeader, VRTSignalDataPacket, VRTContextPacket
[PASS] vita49.stream_server: VITA49StreamServer, VITA49StreamClient
[PASS] vita49.packets: create_stream_id, parse_stream_id

Import Tests: 3 passed, 0 failed

Testing Basic Functionality
======================================================================
[PASS] VRT Header encode/decode
[PASS] Stream ID creation and parsing
[PASS] Signal data packet encode/decode
[PASS] Simulated SDR interface
[PASS] Stream server and client creation

Functionality Tests: 5 passed, 0 failed

Test Summary
======================================================================
[PASS] All tests passed!
```

## Contributing

When adding new tests:
1. Follow the existing test structure
2. Use descriptive test names
3. Add docstrings explaining what is being tested
4. Mark slow tests with `@pytest.mark.slow`
5. Clean up resources (close sockets, stop servers)
6. Use unique ports for network tests to avoid conflicts
