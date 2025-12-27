# Building and Deploying linuxptp to Pluto+

This directory contains Docker and deployment scripts for cross-compiling linuxptp (PTP daemon) for ARM and deploying it to ADALM-Pluto SDR.

## Quick Start

### Step 1: Build linuxptp for ARM

**Windows:**
```bash
.\scripts\build-linuxptp-docker.bat
```

**Linux/macOS/WSL:**
```bash
./scripts/build-linuxptp-docker.sh
```

This will:
1. Build Docker image with ARM cross-compiler
2. Clone and compile linuxptp from GitHub
3. Create `linuxptp-arm/ptp4l` and `linuxptp-arm/phc2sys` binaries
4. Verify binaries are ARM architecture

**Output:**
```
linuxptp-arm/
├── ptp4l      (~100 KB ARM binary)
└── phc2sys    (~50 KB ARM binary)
```

### Step 2: Deploy to Pluto

**Windows:**
```bash
.\scripts\deploy-linuxptp-to-pluto.bat pluto.local
```

**Linux/macOS/WSL:**
```bash
./scripts/deploy-linuxptp-to-pluto.sh pluto.local
```

This will:
1. Copy `ptp4l` to `/usr/sbin/ptp4l` on Pluto
2. Copy `phc2sys` to `/usr/sbin/phc2sys` on Pluto
3. Make binaries executable
4. Verify deployment

**Default Pluto credentials:**
- Username: `root`
- Password: `analog`

### Step 3: Test on Pluto

```bash
# SSH to Pluto
ssh root@pluto.local

# Test binaries
ptp4l --version
phc2sys --version

# Expected output:
# ptp4l 3.x.x
# phc2sys 3.x.x
```

## What is linuxptp?

**linuxptp** is the Linux implementation of IEEE 1588 Precision Time Protocol (PTP).

It provides:
- **ptp4l**: PTP daemon for clock synchronization
- **phc2sys**: Utility to sync system clock to PTP hardware clock

## Why Use PTP Instead of NTP?

| Feature | NTP | PTP (Software) | PTP (Hardware) |
|---------|-----|----------------|----------------|
| **Accuracy** | 1-10ms | 10-100μs | 100ns-1μs |
| **Suitable for TDOA?** | ❌ No | ✅ Yes | ✅ Yes (best) |
| **Suitable for PCR?** | ❌ No | ⚠️ Marginal | ✅ Yes |

For distributed RF intelligence, TDOA, and passive coherent radar, PTP is **essential**.

## How to Configure PTP

See the comprehensive guide: **`PTP_SYNC_IMPLEMENTATION_GUIDE.md`**

It covers:
- Setting up PTP master (Windows PC or Jetson)
- Configuring Pluto as PTP slave
- Integration with VITA49 C streamer
- Testing and validation
- Accuracy expectations

## Files Created

```
scripts/
├── build-linuxptp-docker.bat      # Build on Windows
├── build-linuxptp-docker.sh       # Build on Linux/macOS
├── deploy-linuxptp-to-pluto.bat   # Deploy from Windows
└── deploy-linuxptp-to-pluto.sh    # Deploy from Linux/macOS

docker/
└── Dockerfile.linuxptp            # Docker build configuration

linuxptp-arm/                      # Created after build
├── ptp4l                          # PTP daemon (ARM binary)
└── phc2sys                        # System clock sync (ARM binary)
```

## Troubleshooting

### Docker not found

**Windows:**
```bash
# Install Docker Desktop
https://www.docker.com/products/docker-desktop
```

**Linux:**
```bash
# Install Docker
sudo apt-get install docker.io
sudo systemctl start docker
sudo usermod -aG docker $USER
# Log out and back in
```

### Can't connect to Pluto

```bash
# Try direct IP
ping 192.168.2.1

# Update scripts to use IP
.\scripts\deploy-linuxptp-to-pluto.bat 192.168.2.1
```

### Binaries won't run on Pluto

```bash
# Verify architecture
file linuxptp-arm/ptp4l

# Expected: "ELF 32-bit LSB executable, ARM"
# If you see "x86-64", rebuild with Docker
```

### Permission denied when running

```bash
# SSH to Pluto and fix permissions
ssh root@pluto.local
chmod +x /usr/sbin/ptp4l /usr/sbin/phc2sys
```

### sshpass not found (optional)

**Without sshpass:** You'll need to enter the password manually (3 times during deployment)

**To install sshpass:**

```bash
# Ubuntu/Debian
sudo apt-get install sshpass

# macOS
brew install hudochenkov/sshpass/sshpass

# Windows
choco install sshpass
# Or use WSL: wsl ./scripts/deploy-linuxptp-to-pluto.sh
```

## Manual Build (Without Docker)

If you prefer to build without Docker:

```bash
# Install ARM cross-compiler
sudo apt-get install gcc-arm-linux-gnueabihf

# Clone linuxptp
git clone https://github.com/richardcochran/linuxptp.git
cd linuxptp

# Cross-compile
export CROSS_COMPILE=arm-linux-gnueabihf-
export CC=${CROSS_COMPILE}gcc
make clean
make

# Deploy
scp ptp4l phc2sys root@pluto.local:/usr/sbin/
```

## Next Steps

After deploying linuxptp to Pluto:

1. **Configure PTP master** on your PC or Jetson
   - See `PTP_SYNC_IMPLEMENTATION_GUIDE.md`

2. **Configure Pluto as PTP slave**
   - Create `/etc/ptp4l_slave.conf`
   - Run `ptp4l -i eth0 -s -f /etc/ptp4l_slave.conf`

3. **Integrate with VITA49 streamer**
   - Modify C streamer to use PTP timestamps
   - Report sync status in Context packets

4. **Test synchronization accuracy**
   - Run `tests/test_ptp_accuracy.py`
   - Target: <100μs offset between Plutos

5. **Validate with TDOA**
   - Place RF emitter at known location
   - Measure arrival times at multiple Plutos
   - Calculate position and compare to ground truth

## Resources

- **linuxptp GitHub**: https://github.com/richardcochran/linuxptp
- **IEEE 1588 Standard**: https://standards.ieee.org/standard/1588-2019.html
- **PTP Configuration Guide**: `PTP_SYNC_IMPLEMENTATION_GUIDE.md`
- **VITA49 Integration**: See C streamer modifications in PTP guide

## Support

For issues:
1. Check `PTP_SYNC_IMPLEMENTATION_GUIDE.md` troubleshooting section
2. Verify binaries with `file linuxptp-arm/ptp4l`
3. Test on Pluto with `ptp4l --version`
4. Open GitHub issue with error details
