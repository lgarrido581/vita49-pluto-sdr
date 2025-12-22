# Bug Fix: VITA49 Context Packet Parsing

## Issue

The plotting receiver was displaying incorrect stream parameters (hardcoded defaults of 2.4 GHz and 30 MSPS) instead of the actual parameters from the VITA49 stream.

**Example:**
```bash
python test_e2e_full_pipeline.py --freq 103.7e6 --rate 2e6 --gain 40
```

The plot showed:
- Frequency: 2.4 GHz (should be 0.1037 GHz / 103.7 MHz)
- Sample Rate: 30 MSPS (should be 2.0 MSPS)

## Root Cause

Two issues were identified:

1. **Missing Context Packet Decoder**: The `VRTContextPacket` class in `vita49_packets.py` had an `encode()` method but no `decode()` method, so received context packets couldn't be parsed.

2. **Plotting Receiver Not Using Context Data**: The `test_e2e_step3_plotting_receiver.py` had hardcoded default values and wasn't registering a callback to receive and process context packets.

## Files Modified

### 1. `vita49_packets.py`

**Added:** `VRTContextPacket.decode()` class method (lines 657-769)

This method:
- Decodes the VITA49 context packet header
- Parses the Context Indicator Field (CIF) to determine which fields are present
- Extracts stream parameters using proper fixed-point decoding:
  - Bandwidth (64-bit, 20-bit radix)
  - RF reference frequency (64-bit, 20-bit radix)
  - Sample rate (64-bit, 20-bit radix)
  - Gain (16-bit, 7-bit radix)
  - Temperature (16-bit, 6-bit radix)
- Returns a fully populated `VRTContextPacket` instance

### 2. `test_e2e_step3_plotting_receiver.py`

**Added:** Context packet handling functionality

1. **New callback method** `_on_context_received()` (lines 138-174):
   - Decodes incoming context packets using the new `VRTContextPacket.decode()`
   - Updates stream parameters (sample rate, center frequency, bandwidth, gain)
   - Dynamically adjusts plot axes when parameters change
   - Logs parameter updates to console

2. **Updated statistics display** `_format_statistics()` (lines 237-269):
   - Now shows actual bandwidth and gain values from context packets
   - Added "Context Packet" indicator (✓/✗) to show if context has been received

3. **Registered context callback** in `start()` method (line 275):
   - Added `self.client.on_context(self._on_context_received)`

## Testing

After the fix, the plotting receiver now correctly displays stream parameters:

```bash
python test_e2e_full_pipeline.py --freq 103.7e6 --rate 2e6 --gain 40
```

**Plot now shows:**
- Center Frequency: 0.104 GHz (103.7 MHz) ✓
- Sample Rate: 2.0 MSPS ✓
- Gain: 40.0 dB ✓
- Context Packet: ✓

## Technical Details

### VITA49 Context Packet Format

Context packets are sent periodically (default: every 100 data packets) and contain:

```
Offset  Size    Field
------  ----    -----
0       4       Header (packet type = CONTEXT)
4       4       Stream ID
8       12      Timestamp (optional)
20      4       Context Indicator Field (CIF)
24+     N       Context fields (variable, based on CIF bits)
```

### Fixed-Point Encoding

VITA49 uses fixed-point representation for frequency and gain values:

- **Frequency values**: 64-bit signed integer with 20-bit radix
  - Formula: `Hz = fixed_value / 2^20`
  - Example: `103.7e6 Hz` → `108697804800` (fixed) → `103700000.0 Hz` (decoded)

- **Gain values**: 16-bit signed integer with 7-bit radix
  - Formula: `dB = fixed_value / 2^7`
  - Example: `40.0 dB` → `5120` (fixed) → `40.0 dB` (decoded)

## Verification

To verify the fix works correctly:

1. **Test with different frequencies:**
   ```bash
   python test_e2e_full_pipeline.py --freq 915e6  # Should show 0.915 GHz
   python test_e2e_full_pipeline.py --freq 2.4e9  # Should show 2.4 GHz
   ```

2. **Test with different sample rates:**
   ```bash
   python test_e2e_full_pipeline.py --rate 5e6   # Should show 5.0 MSPS
   python test_e2e_full_pipeline.py --rate 20e6  # Should show 20.0 MSPS
   ```

3. **Test with different gains:**
   ```bash
   python test_e2e_full_pipeline.py --gain 10    # Should show 10.0 dB
   python test_e2e_full_pipeline.py --gain 60    # Should show 60.0 dB
   ```

4. **Check console logs:**
   ```
   INFO - Sample rate updated: 2.0 MSPS
   INFO - Center frequency updated: 0.104 GHz
   ```

## Related Code

- Context packet encoding: `vita49_packets.py:588-655` (`VRTContextPacket.encode()`)
- Context packet decoding: `vita49_packets.py:657-769` (`VRTContextPacket.decode()`)
- Context packet sending: `test_e2e_step2_vita49_restreamer.py:48-66` (`send_context_packet()`)
- Context packet receiving: `test_e2e_step3_plotting_receiver.py:138-174` (`_on_context_received()`)

## Notes

- Context packets are sent every 100 data packets by default (configurable via `context_interval` parameter)
- The spectrum plot and waterfall axes are automatically adjusted when the sample rate changes
- Invalid or corrupted context packets are logged as errors but don't crash the receiver
