#!/usr/bin/env python3
"""
Pluto+ Configuration Diagnostic Tool

Tests connection to Pluto+ and verifies all SDR parameters are set correctly.
Use this to debug configuration issues.

Usage:
    python test_pluto_config.py --pluto-uri ip:pluto.local --freq 103.7e6 --rate 2e6 --gain 40
"""

import argparse
import sys

try:
    import adi
    HAS_ADI = True
except ImportError:
    HAS_ADI = False
    print("ERROR: pyadi-iio not installed")
    print("Install with: pip install pyadi-iio")
    sys.exit(1)


def test_pluto_connection(
    uri: str,
    freq: float,
    rate: float,
    gain: float,
    bandwidth: float = None
):
    """Test Pluto+ connection and configuration"""

    print("\n" + "="*70)
    print("PLUTO+ CONFIGURATION DIAGNOSTIC")
    print("="*70)

    # Auto-adjust bandwidth to match sample rate if not specified
    if bandwidth is None:
        bandwidth = rate * 0.8  # 80% of sample rate is typical

    print(f"\nRequested Configuration:")
    print(f"  URI:        {uri}")
    print(f"  Frequency:  {freq/1e6:.3f} MHz")
    print(f"  Sample Rate: {rate/1e6:.3f} MSPS")
    print(f"  Bandwidth:  {bandwidth/1e6:.3f} MHz")
    print(f"  Gain:       {gain} dB")

    # Connect
    print(f"\n{'─'*70}")
    print("Step 1: Connecting to Pluto+...")
    print(f"{'─'*70}")

    try:
        sdr = adi.ad9361(uri)
        print("✓ Connection successful!")
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False

    # Configure
    print(f"\n{'─'*70}")
    print("Step 2: Configuring SDR parameters...")
    print(f"{'─'*70}")

    try:
        # Set sample rate first (affects other parameters)
        print(f"Setting sample rate to {rate/1e6:.3f} MSPS...")
        sdr.sample_rate = int(rate)
        actual_rate = sdr.sample_rate
        print(f"  Requested: {rate/1e6:.3f} MSPS")
        print(f"  Actual:    {actual_rate/1e6:.3f} MSPS")
        if abs(actual_rate - rate) > 1e3:
            print(f"  ⚠ WARNING: Sample rate mismatch!")
        else:
            print(f"  ✓ Sample rate set correctly")

        # Set frequency
        print(f"\nSetting RX LO to {freq/1e6:.3f} MHz...")
        sdr.rx_lo = int(freq)
        actual_freq = sdr.rx_lo
        print(f"  Requested: {freq/1e6:.3f} MHz")
        print(f"  Actual:    {actual_freq/1e6:.3f} MHz")
        if abs(actual_freq - freq) > 1e6:
            print(f"  ⚠ WARNING: Frequency mismatch!")
        else:
            print(f"  ✓ Frequency set correctly")

        # Set bandwidth
        print(f"\nSetting RX bandwidth to {bandwidth/1e6:.3f} MHz...")
        sdr.rx_rf_bandwidth = int(bandwidth)
        actual_bw = sdr.rx_rf_bandwidth
        print(f"  Requested: {bandwidth/1e6:.3f} MHz")
        print(f"  Actual:    {actual_bw/1e6:.3f} MHz")
        if abs(actual_bw - bandwidth) > 1e6:
            print(f"  ⚠ WARNING: Bandwidth mismatch!")
        else:
            print(f"  ✓ Bandwidth set correctly")

        # Set buffer size
        print(f"\nSetting RX buffer size...")
        sdr.rx_buffer_size = 16384
        print(f"  Buffer size: {sdr.rx_buffer_size} samples")
        print(f"  ✓ Buffer size set")

        # Enable channel 0
        print(f"\nEnabling RX channel 0...")
        sdr.rx_enabled_channels = [0]
        print(f"  Enabled channels: {sdr.rx_enabled_channels}")
        print(f"  ✓ Channel enabled")

        # Set gain mode and gain
        print(f"\nSetting gain to {gain} dB (manual mode)...")
        sdr.gain_control_mode_chan0 = "manual"
        sdr.rx_hardwaregain_chan0 = gain
        actual_gain = sdr.rx_hardwaregain_chan0
        print(f"  Gain mode:   {sdr.gain_control_mode_chan0}")
        print(f"  Requested:   {gain} dB")
        print(f"  Actual:      {actual_gain} dB")
        if abs(actual_gain - gain) > 0.5:
            print(f"  ⚠ WARNING: Gain mismatch!")
        else:
            print(f"  ✓ Gain set correctly")

        print(f"\n✓ All parameters configured")

    except Exception as e:
        print(f"\n✗ Configuration failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test reception
    print(f"\n{'─'*70}")
    print("Step 3: Testing sample reception...")
    print(f"{'─'*70}")

    try:
        import numpy as np

        print("Receiving test buffer...")
        samples = sdr.rx()

        print(f"✓ Received {len(samples)} samples")
        print(f"\nSample Statistics:")
        print(f"  Type:           {type(samples)}")
        print(f"  Dtype:          {samples.dtype}")
        print(f"  Length:         {len(samples)}")
        print(f"  I mean:         {samples.real.mean():.6f}")
        print(f"  Q mean:         {samples.imag.mean():.6f}")
        print(f"  Magnitude mean: {np.abs(samples).mean():.6f}")
        print(f"  Max magnitude:  {np.abs(samples).max():.6f}")

        # Calculate power
        power_linear = np.mean(np.abs(samples)**2)
        power_dbfs = 10 * np.log10(power_linear + 1e-10)
        print(f"  Power:          {power_dbfs:.2f} dBFS")

        # Check if we're getting real data or just noise
        if np.abs(samples).max() < 100:
            print(f"\n  ⚠ WARNING: Very low signal levels!")
            print(f"    - Check antenna connection")
            print(f"    - Try increasing gain")
            print(f"    - Verify correct frequency")

        if np.abs(samples.real.mean()) < 1e-6 and np.abs(samples.imag.mean()) < 1e-6:
            print(f"\n  ℹ I/Q means near zero (expected for AC-coupled RF)")

        # Show first few samples
        print(f"\n  First 5 samples:")
        for i in range(min(5, len(samples))):
            print(f"    [{i}] {samples[i].real:+8.1f} {samples[i].imag:+8.1f}j  (mag: {np.abs(samples[i]):.1f})")

        print(f"\n✓ Sample reception working")

    except Exception as e:
        print(f"✗ Reception test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"✓ Pluto+ is configured and receiving samples")
    print(f"\nActual Configuration:")
    print(f"  Sample Rate:    {sdr.sample_rate/1e6:.3f} MSPS")
    print(f"  RX LO:          {sdr.rx_lo/1e6:.3f} MHz")
    print(f"  RX Bandwidth:   {sdr.rx_rf_bandwidth/1e6:.3f} MHz")
    print(f"  RX Gain:        {sdr.rx_hardwaregain_chan0} dB")
    print(f"  Signal Power:   {power_dbfs:.2f} dBFS")
    print(f"{'='*70}\n")

    # Cleanup
    try:
        sdr.rx_destroy_buffer()
    except:
        pass

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Pluto+ Configuration Diagnostic Tool"
    )
    parser.add_argument(
        '--pluto-uri',
        default="ip:pluto.local",
        help="Pluto+ URI (default: ip:pluto.local)"
    )
    parser.add_argument(
        '--freq', '-f',
        type=float,
        default=103.7e6,
        help="Center frequency in Hz (default: 103.7e6)"
    )
    parser.add_argument(
        '--rate', '-r',
        type=float,
        default=2e6,
        help="Sample rate in Hz (default: 2e6)"
    )
    parser.add_argument(
        '--gain', '-g',
        type=float,
        default=40.0,
        help="RX gain in dB (default: 40)"
    )
    parser.add_argument(
        '--bandwidth', '-b',
        type=float,
        default=None,
        help="RX bandwidth in Hz (default: auto = 0.8 * sample_rate)"
    )

    args = parser.parse_args()

    success = test_pluto_connection(
        uri=args.pluto_uri,
        freq=args.freq,
        rate=args.rate,
        gain=args.gain,
        bandwidth=args.bandwidth
    )

    return 0 if success else 1


if __name__ == '__main__':
    exit(main())
