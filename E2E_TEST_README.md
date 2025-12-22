# End-to-End VITA49 Streaming Test Suite

This test suite provides a complete end-to-end verification of the VITA49 streaming system using your ADALM-Pluto+ SDR.

## Test Architecture

```
┌─────────────────┐
│  Pluto+ SDR     │  Hardware
│  (AD9361)       │
└────────┬────────┘
         │ pyadi-iio
         ▼
┌─────────────────┐
│  Step 2:        │  Python Script
│  VITA49         │  (test_e2e_step2_vita49_restreamer.py)
│  Re-Streamer    │
└────────┬────────┘
         │ UDP VITA49 Packets
         │ (port 4991)
         ▼
┌─────────────────┐
│  Step 3:        │  Python Script
│  Plotting       │  (test_e2e_step3_plotting_receiver.py)
│  Receiver       │
└─────────────────┘
```

## Prerequisites

### Required Python Packages

```bash
pip install pyadi-iio numpy matplotlib scipy
```

### Hardware Setup

1. Connect your ADALM-Pluto+ to your PC via USB or Ethernet
2. Verify connectivity:
   ```bash
   ping 192.168.2.1
   ```
3. Optionally connect an antenna to RX1A for better signals

## Quick Start - Full Pipeline

The easiest way to run the complete end-to-end test:

```bash
python test_e2e_full_pipeline.py --pluto-uri ip:192.168.2.1 --freq 2.4e9
```

This single command will:
1. Connect to your Pluto+ SDR
2. Start receiving IQ samples via pyadi-iio
3. Re-stream samples as VITA49 UDP packets
4. Open a real-time plotting window showing the received data

**To stop:** Close the plotting window or press Ctrl+C

### Quick Start Options

```bash
# Custom frequency (e.g., 915 MHz ISM band)
python test_e2e_full_pipeline.py --freq 915e6

# Lower sample rate for less CPU usage
python test_e2e_full_pipeline.py --rate 10e6

# Higher gain for weak signals
python test_e2e_full_pipeline.py --gain 40

# Run without plotter (streaming only, for debugging)
python test_e2e_full_pipeline.py --no-plotter
```

## Step-by-Step Testing

For debugging or understanding each component, you can run the steps individually:

### Step 1: Test Pluto+ Reception (Optional)

Verify you can receive samples from the Pluto+ SDR:

```bash
python test_e2e_step1_receive_from_pluto.py --uri ip:192.168.2.1 --freq 2.4e9
```

**Expected output:**
```
INFO - Connecting to Pluto+ at ip:192.168.2.1
INFO - Connected successfully!
INFO -   Sample Rate: 30.0 MSPS
INFO -   RX LO: 2.400 GHz
INFO - Received 100 buffers, 1638400 samples, 30.0 MSPS
```

Press Ctrl+C to stop.

**Save samples to file:**
```bash
python test_e2e_step1_receive_from_pluto.py --save test_samples.npy --duration 10
```

### Step 2: VITA49 Re-Streamer

In one terminal, run the VITA49 re-streamer:

```bash
python test_e2e_step2_vita49_restreamer.py --pluto-uri ip:192.168.2.1 --dest 127.0.0.1 --port 4991
```

**Expected output:**
```
============================================================
VITA49 Re-Streamer Running
============================================================
  Pluto+ URI: ip:192.168.2.1
  Frequency: 2.400 GHz
  Sample Rate: 30.0 MSPS
  Gain: 20.0 dB
  VITA49 Destination: 127.0.0.1:4991
  Samples/Packet: 360
============================================================

[Stats] SDR: 100 buffers, 30.0 MSPS | VITA49: 4551 packets, 23.45 Mbps | Context: 45
```

Keep this running and proceed to Step 3.

### Step 3: Plotting Receiver

In a **second terminal**, run the plotting receiver:

```bash
python test_e2e_step3_plotting_receiver.py --port 4991
```

**Expected behavior:**
- A matplotlib window opens showing 4 plots:
  - **Top:** Time domain I/Q waveforms
  - **Middle:** Real-time spectrum (FFT)
  - **Bottom-Left:** Waterfall (spectrogram)
  - **Bottom-Right:** Stream statistics

**Close the plot window to stop.**

## Testing Different Scenarios

### Test 1: WiFi Signal Detection (2.4 GHz)

```bash
python test_e2e_full_pipeline.py --freq 2.4e9 --rate 20e6 --gain 30
```

You should see WiFi signals in the spectrum around 2.4-2.48 GHz.

### Test 2: FM Radio (88-108 MHz)

```bash
python test_e2e_full_pipeline.py --freq 98e6 --rate 2e6 --gain 40
```

### Test 3: ISM Band 915 MHz

```bash
python test_e2e_full_pipeline.py --freq 915e6 --rate 10e6 --gain 35
```

### Test 4: Low Sample Rate (for slower PCs)

```bash
python test_e2e_full_pipeline.py --freq 2.4e9 --rate 10e6
```

### Test 5: High Gain (weak signals)

```bash
python test_e2e_full_pipeline.py --freq 2.4e9 --gain 60
```

**Note:** Max gain on Pluto+ is ~73 dB in manual mode.

## Troubleshooting

### Problem: "pyadi-iio not installed"

**Solution:**
```bash
pip install pyadi-iio
```

### Problem: "Failed to connect to Pluto+"

**Solutions:**
1. Check USB/Ethernet connection
2. Verify IP address:
   ```bash
   ping 192.168.2.1
   ```
3. Try using hostname:
   ```bash
   python test_e2e_full_pipeline.py --pluto-uri ip:pluto.local
   ```
4. Check if another application is using the SDR

### Problem: "No packets received" in plotter

**Solutions:**
1. Verify re-streamer is running (check Step 2 terminal)
2. Check firewall isn't blocking UDP port 4991
3. Ensure both scripts use the same port number
4. On Windows, allow Python through Windows Defender Firewall

### Problem: Plots are not updating

**Solutions:**
1. Check that samples are being received (check statistics panel)
2. Increase update interval:
   ```bash
   python test_e2e_step3_plotting_receiver.py --update-rate 100
   ```
3. Reduce FFT size:
   ```bash
   python test_e2e_step3_plotting_receiver.py --fft-size 512
   ```

### Problem: High CPU usage

**Solutions:**
1. Reduce sample rate:
   ```bash
   python test_e2e_full_pipeline.py --rate 10e6
   ```
2. Increase plot update interval:
   ```bash
   python test_e2e_step3_plotting_receiver.py --update-rate 100
   ```
3. Close other applications

### Problem: Plotting window freezes on Windows

**Solution:**
This is a known matplotlib issue on Windows with tight update loops. Increase the update interval:
```bash
python test_e2e_step3_plotting_receiver.py --update-rate 100
```

## Verifying VITA49 Packet Format

To verify the VITA49 packets are correctly formatted, you can use the existing test suite:

```bash
pytest test_vita49.py -v -k "test_server_client_communication"
```

This will test the VITA49 encoder/decoder with simulated data.

## Next Steps

Once you've verified the end-to-end pipeline works:

1. **Deploy to Pluto+ embedded:** Use the scripts from the main README to run VITA49 streaming directly on the Pluto+ ARM processor

2. **Use with signal processing harness:**
   ```bash
   python signal_processing_harness.py --port 4991 --threshold -20
   ```

3. **Integrate with your application:** Use the `VITA49StreamClient` class from `vita49_stream_server.py` in your own Python applications

## Performance Metrics

Expected performance on a typical PC:

| Sample Rate | VITA49 Throughput | CPU Usage (Re-streamer) | CPU Usage (Plotter) |
|-------------|-------------------|------------------------|---------------------|
| 10 MSPS     | 7-8 Mbps          | 10-15%                 | 15-20%              |
| 20 MSPS     | 15-16 Mbps        | 15-20%                 | 20-30%              |
| 30 MSPS     | 23-24 Mbps        | 20-25%                 | 30-40%              |

## Script Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `test_e2e_step1_receive_from_pluto.py` | Test basic Pluto+ reception | Verify SDR connectivity |
| `test_e2e_step2_vita49_restreamer.py` | Re-stream as VITA49 packets | Manual step-by-step testing |
| `test_e2e_step3_plotting_receiver.py` | Visualize VITA49 stream | Manual step-by-step testing |
| `test_e2e_full_pipeline.py` | **Complete automated pipeline** | **Recommended for testing** |

## Command-Line Reference

### Full Pipeline (`test_e2e_full_pipeline.py`)

```
--pluto-uri, -u     Pluto+ URI (default: ip:192.168.2.1)
--freq, -f          Center frequency in Hz (default: 2.4e9)
--rate, -r          Sample rate in Hz (default: 30e6)
--gain, -g          RX gain in dB (default: 20)
--port, -p          VITA49 UDP port (default: 4991)
--no-plotter        Run without plotting (streamer only)
```

### Re-Streamer (`test_e2e_step2_vita49_restreamer.py`)

```
--pluto-uri, -u     Pluto+ URI (default: ip:192.168.2.1)
--freq, -f          Center frequency in Hz (default: 2.4e9)
--rate, -r          Sample rate in Hz (default: 30e6)
--gain, -g          RX gain in dB (default: 20)
--dest, -d          VITA49 destination IP (default: 127.0.0.1)
--port, -p          VITA49 destination port (default: 4991)
--pkt-size          Samples per packet (default: 360)
```

### Plotting Receiver (`test_e2e_step3_plotting_receiver.py`)

```
--port, -p             VITA49 UDP port (default: 4991)
--fft-size, -f         FFT size (default: 1024)
--waterfall-lines, -w  Waterfall history (default: 100)
--update-rate, -u      Update interval in ms (default: 50)
```

## Support

If you encounter issues not covered here, check:
1. The main `README.md` for general VITA49 information
2. Run the test suite: `pytest test_vita49.py -v`
3. Check the logs for detailed error messages
