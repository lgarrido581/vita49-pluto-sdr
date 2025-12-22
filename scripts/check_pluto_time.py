#!/usr/bin/env python3
"""
Check Pluto SDR Time

Simple utility to check the system time on Pluto SDR via SSH.
Also compares it to local system time to identify time sync issues.
"""

import paramiko
import time
from datetime import datetime
import argparse


def check_pluto_time(pluto_ip, password='analog'):
    """
    Check the current time on Pluto SDR via SSH.

    Args:
        pluto_ip: IP address of Pluto SDR (usually 192.168.2.1)
        password: SSH password (default: analog)
    """
    print(f"\n=== Checking Pluto SDR Time at {pluto_ip} ===\n")

    # Get local time
    local_time = time.time()
    local_dt = datetime.fromtimestamp(local_time)

    print(f"Local System Time:")
    print(f"  Timestamp: {local_time:.3f}")
    print(f"  DateTime:  {local_dt}")
    print(f"  UTC:       {datetime.utcfromtimestamp(local_time)}")
    print()

    try:
        # Connect to Pluto via SSH
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        print(f"Connecting to Pluto at {pluto_ip}...")
        client.connect(pluto_ip, username='root', password=password, timeout=5)

        # Get Pluto time as Unix timestamp
        stdin, stdout, stderr = client.exec_command('date +%s.%N')
        pluto_time_str = stdout.read().decode().strip()
        pluto_time = float(pluto_time_str)
        pluto_dt = datetime.fromtimestamp(pluto_time)

        print(f"\nPluto SDR Time:")
        print(f"  Timestamp: {pluto_time:.3f}")
        print(f"  DateTime:  {pluto_dt}")
        print(f"  UTC:       {datetime.utcfromtimestamp(pluto_time)}")

        # Calculate time difference
        time_diff = pluto_time - local_time

        print(f"\nTime Difference:")
        print(f"  Pluto - Local: {time_diff:.3f} seconds")

        if abs(time_diff) < 1.0:
            print(f"  ✓ Times are synchronized (within 1 second)")
        elif abs(time_diff) < 60:
            print(f"  ⚠ Times differ by {abs(time_diff):.1f} seconds")
        elif abs(time_diff) < 3600:
            print(f"  ⚠ Times differ by {abs(time_diff)/60:.1f} minutes")
        elif abs(time_diff) < 86400:
            print(f"  ⚠ Times differ by {abs(time_diff)/3600:.1f} hours")
        else:
            print(f"  ⚠ Times differ by {abs(time_diff)/86400:.1f} days")

        # Check uptime (Pluto without RTC starts at epoch on boot)
        stdin, stdout, stderr = client.exec_command('uptime')
        uptime = stdout.read().decode().strip()
        print(f"\nPluto Uptime:")
        print(f"  {uptime}")

        # Check if time is near Unix epoch (indicates no time sync)
        if pluto_time < 1000000000:  # Before Sept 2001
            print(f"\n  ⚠ WARNING: Pluto time is near Unix epoch!")
            print(f"  ⚠ The Pluto has not synchronized its time.")
            print(f"  ⚠ This will cause incorrect VITA49 timestamps.")

        # Check for NTP
        stdin, stdout, stderr = client.exec_command('ps | grep -v grep | grep ntp')
        ntp_output = stdout.read().decode().strip()
        if ntp_output:
            print(f"\n  NTP Process: Running")
            print(f"  {ntp_output}")
        else:
            print(f"\n  NTP Process: Not running")
            print(f"\n  Recommendation: Configure NTP on Pluto to sync time")

        client.close()

    except paramiko.AuthenticationException:
        print(f"❌ Authentication failed. Check password (default: 'analog')")
    except paramiko.SSHException as e:
        print(f"❌ SSH connection error: {e}")
    except TimeoutError:
        print(f"❌ Connection timeout. Is the Pluto reachable at {pluto_ip}?")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Check Pluto SDR system time via SSH'
    )
    parser.add_argument(
        '--pluto-ip',
        default='192.168.2.1',
        help='Pluto SDR IP address (default: 192.168.2.1)'
    )
    parser.add_argument(
        '--password',
        default='analog',
        help='SSH password (default: analog)'
    )

    args = parser.parse_args()
    check_pluto_time(args.pluto_ip, args.password)
