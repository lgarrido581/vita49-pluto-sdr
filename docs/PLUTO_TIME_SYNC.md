# Pluto SDR Time Synchronization

The ADALM-Pluto SDR does not have a real-time clock (RTC), so its system time resets to the Unix epoch (January 1, 1970) on every boot. This causes VITA49 timestamp fields to be incorrect.

## Problem

VITA49 packets include timestamps with:
- **Integer Seconds**: Seconds since POSIX epoch (Jan 1, 1970 00:00:00 UTC) when TSI=UTC
- **Fractional Seconds**: Picoseconds

Without time synchronization, the Pluto's timestamps will show times from 1970 or the time since boot.

## Solution Options

### Option 1: Manual Time Sync (Quick Fix)

SSH into the Pluto and manually set the time:

```bash
ssh root@192.168.2.1
# Password: analog

# Set time manually (replace with current time)
date -s "2024-12-22 15:30:00"

# Or sync from a PC timestamp
date -s @$(date +%s)
```

**Note**: This only lasts until the next reboot.

### Option 2: NTP Time Sync (Recommended)

Configure the Pluto to sync time via NTP on boot:

```bash
ssh root@192.168.2.1

# Install ntpdate if not present
# (Most Pluto images have it)

# Add to startup script
echo "ntpdate pool.ntp.org" >> /etc/init.d/S99custom

# Make executable
chmod +x /etc/init.d/S99custom

# Sync now
ntpdate pool.ntp.org
```

### Option 3: Sync from Host Computer

Create a script to sync Pluto time from your PC every time you connect:

```bash
#!/bin/bash
# sync_pluto_time.sh
ssh root@192.168.2.1 "date -s @$(date +%s)"
```

Or use the Python utility:

```python
import paramiko
import time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.2.1', username='root', password='analog')

current_time = int(time.time())
client.exec_command(f'date -s @{current_time}')
client.close()
```

### Option 4: Use Pluto as Network Client

If your Pluto is connected to a network with internet access:

```bash
# On Pluto
vi /etc/network/interfaces

# Add DNS servers
nameserver 8.8.8.8
nameserver 8.8.4.4

# Install and configure NTP
opkg update
opkg install ntp
```

## Checking Current Pluto Time

Use the provided utility script:

```bash
python scripts/check_pluto_time.py --pluto-ip 192.168.2.1
```

Or manually via SSH:

```bash
ssh root@192.168.2.1 "date; date +%s"
```

## Impact on VITA49 Timestamps

Without proper time sync:
- Timestamps will show incorrect dates/times
- Time differences between packets will still be accurate (relative timing)
- Correlation with external events will be impossible

With proper time sync:
- Absolute timestamps are accurate
- Can correlate SDR data with real-world events
- Proper logging and replay capabilities

## Verifying Time in Web UI

After syncing time, check the Packet Inspector in the Web UI:
1. Click on a packet to view details
2. Check the "Timestamp" field shows current time
3. Verify "Integer Seconds" is around 1734900000 (late 2024)
   - If it's less than 100000000, time is not synced

## Troubleshooting

**Timestamps still wrong after sync?**
- Restart the VITA49 stream server on Pluto
- Check timezone settings (Pluto uses UTC by default)
- Verify network connectivity for NTP

**NTP not working?**
- Check internet connectivity from Pluto
- Try different NTP servers (time.google.com, time.nist.gov)
- Check firewall settings (NTP uses UDP port 123)

**Time drifts over time?**
- Add NTP to cron for periodic sync
- Consider using PTP (Precision Time Protocol) for sub-microsecond accuracy
