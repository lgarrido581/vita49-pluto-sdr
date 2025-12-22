When trying to run the `vita49_stream_server.py` and `signal_processing_harness.py` scripts, in simulation mode,
I didn't seem to see the signal processing harness actually printing any meaningful information about the signal processing.


``` bash
(venv) (base) PS C:\git-repos\vita49-pluto> python vita49_stream_server.py --simulate --dest 127.0.0.1 --port 4991
Warning: pyadi-iio not available. Running in simulation mode.
Starting VITA 49 streaming server
  SDR URI: ip:192.168.2.1
  Frequency: 2.400 GHz
  Sample rate: 30.0 MSPS
  Destination: 127.0.0.1:4991
  Channels: [0]
  Simulation: True
2025-12-21 16:34:03,262 - __main__ - INFO - Connected to simulated SDR
2025-12-21 16:34:03,263 - __main__ - INFO - Created socket for channel 0: 127.0.0.1:4991
2025-12-21 16:34:03,323 - __main__ - INFO - Starting stream loop
2025-12-21 16:34:03,323 - __main__ - INFO - VITA 49 streaming server started
Channel 0: 67014 pkts, 153.42 Mbps, 13237.4 pps
Channel 0: 133768 pkts, 154.05 Mbps, 13292.2 pps


(venv) (base) PS C:\git-repos\vita49-pluto> python signal_processing_harness.py --port 4991 --threshold -20
Warning: pyadi-iio not available. Running in simulation mode.
2025-12-21 16:33:43,947 - __main__ - INFO - Added detector: energy_detector
2025-12-21 16:33:43,950 - vita49_stream_server - INFO - VITA 49 client listening on 0.0.0.0:4991
2025-12-21 16:33:43,951 - __main__ - INFO - Processing loop started
2025-12-21 16:33:43,951 - __main__ - INFO - Signal processing harness started on port 4991
Signal processing harness running on port 4991
Press Ctrl+C to stop
Stats: 0 pkts, 0 samples, 0 detections, 0.0 ms/block
Stats: 0 pkts, 0 samples, 0 detections, 0.0 ms/block
Stats: 0 pkts, 0 samples, 0 detections, 0.0 ms/block
Stats: 0 pkts, 0 samples, 0 detections, 0.0 ms/block
``` 
