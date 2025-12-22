# Quick Start - VITA49 Testing

## 1. Verify Installation

```bash
# Quick import check
python -c "from vita49.packets import VRTHeader; print('OK')"

# Run basic tests
python tests/run_tests.py --basic
```

## 2. Run Test Suite

### Without pytest
```bash
python tests/run_tests.py --basic
```

### With pytest (recommended)
```bash
pip install pytest
pytest tests/ -v
```

## 3. Test Individual Components

### Packet Encoding/Decoding
```bash
pytest tests/test_packets.py -v
```

### Stream Server/Client
```bash
pytest tests/test_stream_server.py -v
```

### Integration Tests
```bash
pytest tests/test_integration.py -v
```

## 4. Hardware Tests (Pluto+ Required)

### Test Pluto+ Configuration
```bash
python tests/test_pluto_config.py --pluto-uri ip:pluto.local --freq 103.7e6
```

### Simple Streaming Test
```bash
python tests/test_streaming_simple.py --pluto-uri ip:pluto.local --freq 103.7e6 --rate 2e6
```

### Full Pipeline Test
```bash
python tests/e2e/full_pipeline.py --pluto-uri ip:pluto.local --freq 2.4e9
```

## 5. Expected Results

All basic tests should pass:
```
Import Tests: 3 passed, 0 failed
Functionality Tests: 5 passed, 0 failed
[PASS] All tests passed!
```

## Common Issues

### Import Error
```bash
# Fix: Install package in development mode
pip install -e .
```

### Port Already in Use
```bash
# Windows: Find and kill process
netstat -ano | findstr :4991
taskkill /PID <pid> /F

# Linux/Mac: Kill process
lsof -i :4991
kill <pid>
```

### Pluto+ Not Found
```bash
# Check connection
ping pluto.local

# Try IP address instead
python tests/test_pluto_config.py --pluto-uri ip:192.168.2.1
```

## Test File Summary

| File | Purpose | Runtime |
|------|---------|---------|
| `test_packets.py` | Packet encoding/decoding | Fast (~1s) |
| `test_stream_server.py` | Streaming components | Medium (~5s) |
| `test_integration.py` | End-to-end workflows | Slow (~10s) |
| `test_vita49.py` | Original comprehensive suite | Medium (~5s) |
| `run_tests.py` | Universal test runner | Fast (~1s) |

## Quick Commands

```bash
# All tests
pytest tests/ -v

# Fast tests only
pytest tests/ -v -m "not slow"

# With coverage
pytest tests/ --cov=vita49 --cov-report=html

# Specific test
pytest tests/test_packets.py::TestVRTHeader::test_header_encode_decode -v

# Run test runner
python tests/run_tests.py
```
