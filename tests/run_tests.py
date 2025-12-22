#!/usr/bin/env python3
"""
Test runner for VITA49 library

Runs all tests and provides a summary report.
Can be run without pytest installed (falls back to basic import tests).

Usage:
    python tests/run_tests.py
    python tests/run_tests.py --basic  # Only import tests
"""

import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


def test_imports():
    """Test that all imports work correctly"""
    print("\n" + "="*70)
    print("Testing Imports")
    print("="*70)

    tests = [
        ("vita49.packets", "VRTHeader, VRTSignalDataPacket, VRTContextPacket"),
        ("vita49.stream_server", "VITA49StreamServer, VITA49StreamClient"),
        ("vita49.packets", "create_stream_id, parse_stream_id"),
    ]

    passed = 0
    failed = 0

    for module, items in tests:
        try:
            exec(f"from {module} import {items}")
            print(f"[PASS] {module}: {items}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {module}: {items} - {e}")
            failed += 1

    print(f"\nImport Tests: {passed} passed, {failed} failed")
    return failed == 0


def test_basic_functionality():
    """Test basic functionality without pytest"""
    print("\n" + "="*70)
    print("Testing Basic Functionality")
    print("="*70)

    passed = 0
    failed = 0

    # Test 1: Create and encode VRT header
    try:
        from vita49.packets import VRTHeader, PacketType
        header = VRTHeader(packet_type=PacketType.CONTEXT)
        encoded = header.encode()
        decoded = VRTHeader.decode(encoded)
        assert decoded.packet_type == PacketType.CONTEXT
        print("[PASS] VRT Header encode/decode")
        passed += 1
    except Exception as e:
        print(f"[FAIL]VRT Header encode/decode - {e}")
        failed += 1

    # Test 2: Create stream ID
    try:
        from vita49.packets import create_stream_id, parse_stream_id
        stream_id = create_stream_id(channel=1, device_id=42)
        parsed = parse_stream_id(stream_id)
        assert parsed['channel'] == 1
        assert parsed['device_id'] == 42
        print("[PASS]Stream ID creation and parsing")
        passed += 1
    except Exception as e:
        print(f"[FAIL]Stream ID creation and parsing - {e}")
        failed += 1

    # Test 3: Create signal data packet
    try:
        import numpy as np
        from vita49.packets import VRTSignalDataPacket
        iq = 0.5 * np.ones(100, dtype=np.complex64)
        packet = VRTSignalDataPacket.from_iq_samples(
            iq_samples=iq,
            stream_id=0x1234,
            sample_rate=30e6
        )
        encoded = packet.encode()
        decoded = VRTSignalDataPacket.decode(encoded)
        recovered = decoded.to_iq_samples()
        assert len(recovered) == 100
        print("[PASS]Signal data packet encode/decode")
        passed += 1
    except Exception as e:
        print(f"[FAIL]Signal data packet encode/decode - {e}")
        failed += 1

    # Test 4: Create simulated SDR
    try:
        from vita49.stream_server import SimulatedSDRInterface, SDRConfig
        config = SDRConfig()
        sdr = SimulatedSDRInterface(config)
        assert sdr.connect() == True
        data = sdr.receive()
        assert data is not None
        sdr.disconnect()
        print("[PASS]Simulated SDR interface")
        passed += 1
    except Exception as e:
        print(f"[FAIL]Simulated SDR interface - {e}")
        failed += 1

    # Test 5: Create stream server and client
    try:
        from vita49.stream_server import VITA49StreamServer, VITA49StreamClient
        server = VITA49StreamServer(
            destination="127.0.0.1",
            port=19999,
            use_simulation=True
        )
        client = VITA49StreamClient(port=19999)
        # Don't actually start them, just test creation
        print("[PASS]Stream server and client creation")
        passed += 1
    except Exception as e:
        print(f"[FAIL]Stream server and client creation - {e}")
        failed += 1

    print(f"\nFunctionality Tests: {passed} passed, {failed} failed")
    return failed == 0


def run_pytest():
    """Run pytest if available"""
    print("\n" + "="*70)
    print("Running pytest")
    print("="*70)

    try:
        import pytest
        test_dir = Path(__file__).parent
        args = [
            str(test_dir),
            '-v',
            '--tb=short',
            '-m', 'not slow'  # Skip slow tests by default
        ]
        return pytest.main(args) == 0
    except ImportError:
        print("pytest not installed - skipping pytest tests")
        print("Install with: pip install pytest")
        return None


def main():
    parser = argparse.ArgumentParser(description="Run VITA49 tests")
    parser.add_argument(
        '--basic',
        action='store_true',
        help="Run only basic import and functionality tests"
    )
    parser.add_argument(
        '--pytest',
        action='store_true',
        help="Run only pytest tests (requires pytest)"
    )

    args = parser.parse_args()

    all_passed = True

    if not args.pytest:
        # Run basic tests
        imports_ok = test_imports()
        functionality_ok = test_basic_functionality()
        all_passed = imports_ok and functionality_ok

    if not args.basic and not args.pytest:
        # Try to run pytest
        pytest_result = run_pytest()
        if pytest_result is not None:
            all_passed = all_passed and pytest_result

    elif args.pytest:
        # Only run pytest
        pytest_result = run_pytest()
        if pytest_result is None:
            print("\nERROR: pytest not available")
            return 1
        all_passed = pytest_result

    # Summary
    print("\n" + "="*70)
    print("Test Summary")
    print("="*70)
    if all_passed:
        print("[PASS] All tests passed!")
        return 0
    else:
        print("[FAIL] Some tests failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
