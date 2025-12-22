# Windows Quick Start Guide

## TL;DR - Easiest Way (2 Steps)

```powershell
# Step 1: Build with Docker
.\build-with-docker.bat

# Step 2: Deploy to Pluto
# Use WinSCP or the command below if you have SSH
scp vita49_streamer root@pluto.local:/root/
```

Done! Now skip to "Usage" section below.

---

## Option 1: Docker (Recommended for Windows)

### Prerequisites
- Docker Desktop: https://www.docker.com/products/docker-desktop

### Build and Deploy

```powershell
# 1. Build using Docker (creates ARM binary)
.\build-with-docker.bat

# 2. Copy to Pluto using WinSCP
# Download WinSCP from: https://winscp.net/
# Connect to pluto.local, user: root, password: analog
# Copy vita49_streamer to /root/
# Set permissions to 755 (rwxr-xr-x)
```

**OR** if you have SSH tools installed:

```powershell
# 2. Deploy with SCP
scp vita49_streamer root@pluto.local:/root/
ssh root@pluto.local
chmod +x vita49_streamer
```

---

## Option 2: WSL (Best for Development)

If you plan to develop/modify the code, use WSL.

### Install WSL

```powershell
# In PowerShell as Administrator
wsl --install
```

Reboot, then open "Ubuntu" from Start Menu:

```bash
# In WSL terminal
sudo apt-get update
sudo apt-get install -y gcc-arm-linux-gnueabihf libiio-dev make

# Navigate to your Windows files
cd /mnt/c/git-repos/vita49-pluto

# Build and deploy
make deploy
```

---

## Usage (After Deploy)

### 1. Start Streamer on Pluto

```powershell
# SSH to Pluto
ssh root@pluto.local
# Password: analog

# Run streamer
./vita49_streamer
```

You should see:
```
==========================================
VITA49 Standalone Streamer for Pluto
==========================================

IIO context created
Control port: 4990
Data port: 4991

[Config] Configured: 2400.0 MHz, 30.0 MSPS, 20.0 dB
[Control] Listening on port 4990
[Streaming] Started
```

### 2. Configure from Your PC

```powershell
# In a new PowerShell window
python vita49_config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6 --gain 40
```

### 3. Receive Data

```powershell
python test_e2e_step3_plotting_receiver.py --port 4991
```

You'll see real-time plots of the IQ data!

---

## Troubleshooting

### Docker not found

Install Docker Desktop:
https://www.docker.com/products/docker-desktop

Make sure it's running (icon in system tray).

### SSH/SCP not found

**Option A:** Use WinSCP (GUI, easier)
- Download: https://winscp.net/
- Drag and drop files to Pluto

**Option B:** Install OpenSSH
```powershell
# In PowerShell as Administrator
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

**Option C:** Use PuTTY/PSCP
- Download: https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html
- Use `pscp` instead of `scp`

### Can't connect to Pluto

```powershell
# Try ping
ping pluto.local

# If that fails, try IP directly
ping 192.168.2.1

# Use IP for all commands
python vita49_config_client.py --pluto 192.168.2.1 ...
```

### Python not found

You need Python for the PC-side tools (not for Pluto!).

Install Python:
https://www.python.org/downloads/

Then install dependencies:
```powershell
pip install numpy matplotlib pyadi-iio
```

---

## File Transfer Methods

### Method 1: WinSCP (Easiest)

1. Download WinSCP: https://winscp.net/
2. Install and run
3. Create new connection:
   - Protocol: SCP
   - Host: `pluto.local` (or `192.168.2.1`)
   - Port: 22
   - Username: `root`
   - Password: `analog`
4. Click "Login"
5. Drag `vita49_streamer` from left (Windows) to right (Pluto)
6. Right-click file on Pluto → Properties → Permissions → Check "Execute" boxes
7. Done!

### Method 2: Command Line (SCP)

```powershell
scp vita49_streamer root@pluto.local:/root/
```

### Method 3: PuTTY PSCP

```powershell
pscp vita49_streamer root@pluto.local:/root/
```

---

## Quick Reference

### Build Commands

```powershell
# Build with Docker (Windows batch script)
.\build-with-docker.bat

# OR with Docker manually
docker build -t pluto-builder .
docker run --rm -v ${PWD}:/build pluto-builder

# OR with WSL
wsl
make cross
```

### Deploy Commands

```powershell
# WinSCP (GUI)
# Just drag and drop!

# SCP (command line)
scp vita49_streamer root@pluto.local:/root/

# PuTTY PSCP
pscp vita49_streamer root@pluto.local:/root/

# WSL with Make
wsl
make deploy-binary
```

### Usage Commands

```powershell
# Configure Pluto
python vita49_config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6

# Receive and plot
python test_e2e_step3_plotting_receiver.py --port 4991

# Run parallel receivers
python example_parallel_receivers.py --port 4991
```

---

## Complete Workflow (Copy/Paste)

```powershell
# 1. Build
.\build-with-docker.bat

# 2. Deploy (using WinSCP or this command)
scp vita49_streamer root@pluto.local:/root/

# 3. Run on Pluto (in separate window)
ssh root@pluto.local
./vita49_streamer

# 4. Configure from PC
python vita49_config_client.py --pluto pluto.local --freq 2.4e9 --rate 30e6 --gain 40

# 5. Receive data
python test_e2e_step3_plotting_receiver.py --port 4991
```

---

## Next Steps

- See `QUICKSTART.md` for usage examples
- See `WINDOWS_BUILD_GUIDE.md` for detailed build options
- See `BUILD_AND_DEPLOY.md` for advanced topics

Enjoy your VITA49 streaming Pluto!
