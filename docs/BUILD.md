# Building VITA49 Pluto Streamer

This guide covers building the VITA49 streamer for deployment to ADALM-Pluto SDR across all platforms.

## Quick Start

### Using Pre-Built Binary (Easiest)

If you have a pre-built ARM binary:

```bash
scp vita49_streamer root@pluto.local:/root/
ssh root@pluto.local
chmod +x vita49_streamer
./vita49_streamer
```

Done!

### Build and Deploy in One Command

```bash
make deploy
```

This cross-compiles the C streamer for ARM and deploys it to your Pluto.

---

## Build Methods

Choose the method that best fits your environment:

| Method | Best For | Setup Time | Platforms |
|--------|----------|------------|-----------|
| **Native Toolchain** | Linux developers | 5 min | Linux, macOS |
| **WSL** | Windows developers | 5 min | Windows |
| **Docker** | Any platform, no install | 10 min | All |
| **GitHub Actions** | CI/CD, automatic builds | 15 min | All |

---

## Method 1: Native ARM Cross-Compiler

### Install Prerequisites

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install gcc-arm-linux-gnueabihf libiio-dev make
```

**Fedora/RHEL/CentOS:**
```bash
sudo dnf install gcc-arm-linux-gnu libiio-devel make
```

**macOS:**
```bash
brew install arm-linux-gnueabihf-binutils
# Note: macOS support is limited, Docker recommended
```

### Build Commands

```bash
# Cross-compile for ARM
make cross

# Or simply (cross is default)
make

# Deploy to Pluto
make deploy

# Custom Pluto IP
make deploy PLUTO_IP=192.168.2.1

# Clean build artifacts
make clean
```

---

## Method 2: Windows with WSL (Recommended for Windows)

Windows Subsystem for Linux provides a complete Linux environment on Windows.

### Install WSL

```powershell
# In PowerShell as Administrator
wsl --install
```

Reboot your system, then:

```bash
# Inside WSL (Ubuntu)
sudo apt-get update
sudo apt-get install gcc-arm-linux-gnueabihf libiio-dev make

# Navigate to your repo (Windows drives are at /mnt/)
cd /mnt/c/git-repos/vita49-pluto

# Build and deploy
make deploy
```

### Advantages of WSL
- Native Linux tools and compilers
- Seamless access to Windows files
- Best long-term solution for Windows development
- Works with all Linux-based tools and scripts

---

## Method 3: Docker (No Toolchain Installation)

Use Docker to build without installing the ARM toolchain on your system.

### Prerequisites

- Docker Desktop (Windows/macOS) or Docker Engine (Linux)
- Download from: https://www.docker.com/products/docker-desktop

### Using Provided Scripts

**Linux/macOS:**
```bash
chmod +x scripts/build-with-docker.sh
./scripts/build-with-docker.sh
```

**Windows (PowerShell):**
```powershell
.\scripts\build-with-docker.bat
```

### Manual Docker Build

```bash
# Build the Docker image
docker build -t pluto-builder -f docker/Dockerfile .

# Compile the binary
docker run --rm -v $(pwd):/build pluto-builder

# On Windows PowerShell:
docker run --rm -v ${PWD}:/build pluto-builder

# Deploy to Pluto
scp vita49_streamer root@pluto.local:/root/
```

The Dockerfile is located at `docker/Dockerfile` and contains:
```dockerfile
FROM debian:bullseye

RUN apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf \
    libiio-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
CMD ["make", "cross"]
```

---

## Method 4: Build on Pluto (Advanced)

If you have a development environment directly on your Pluto:

```bash
# On your PC, copy source to Pluto
scp src/pluto_vita49_streamer.c Makefile root@pluto.local:/root/

# SSH to Pluto
ssh root@pluto.local

# Build natively on Pluto
make native

# Run
./vita49_streamer
```

**Note:** This requires GCC and build tools on the Pluto itself, which are not included by default.

---

## Method 5: GitHub Actions (Automated Builds)

Set up automatic builds on every commit.

Create `.github/workflows/build.yml`:

```yaml
name: Build ARM Binary

on: [push, workflow_dispatch]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Install ARM toolchain
        run: |
          sudo apt-get update
          sudo apt-get install -y gcc-arm-linux-gnueabihf libiio-dev

      - name: Build
        run: make cross

      - name: Upload binary
        uses: actions/upload-artifact@v3
        with:
          name: vita49_streamer
          path: vita49_streamer
```

After pushing to GitHub:
1. Go to Actions tab
2. Download the compiled binary from artifacts
3. Deploy to Pluto

---

## Deployment Methods

### Automated Deployment

```bash
# Default (pluto.local)
make deploy

# Custom IP
make deploy PLUTO_IP=192.168.2.1
```

This will:
1. Cross-compile the streamer
2. Copy binary to Pluto via SCP
3. Set execute permissions

### Manual Deployment - Linux/macOS

```bash
# Build first
make cross

# Copy to Pluto
scp vita49_streamer root@pluto.local:/root/

# SSH to Pluto
ssh root@pluto.local
# Password: analog

# Make executable and run
chmod +x vita49_streamer
./vita49_streamer
```

### Manual Deployment - Windows

**Option 1: Using Windows OpenSSH (Windows 10+)**
```powershell
# Copy to Pluto
scp vita49_streamer root@pluto.local:/root/

# Connect to Pluto
ssh root@pluto.local
```

**Option 2: Using WinSCP (GUI)**
1. Download WinSCP from https://winscp.net/
2. Connect to Pluto:
   - Host: `pluto.local` or `192.168.2.1`
   - User: `root`
   - Password: `analog`
3. Copy `vita49_streamer` to `/root/`
4. Right-click → Properties → Permissions: `755` (rwxr-xr-x)

**Option 3: Using PuTTY/PSCP**
```powershell
pscp vita49_streamer root@pluto.local:/root/
putty root@pluto.local
```

---

## Makefile Targets

```bash
make                  # Cross-compile (same as 'make cross')
make cross            # Cross-compile for ARM
make native           # Native compile (on Pluto)
make deploy           # Build and deploy to Pluto
make clean            # Remove build artifacts
make help             # Show all available targets
```

### Makefile Variables

```bash
# Custom cross-compiler prefix
make cross CROSS_COMPILE=arm-buildroot-linux-gnueabihf-

# Custom Pluto IP
make deploy PLUTO_IP=192.168.2.1

# Custom deployment user
make deploy PLUTO_USER=root
```

---

## Build Configuration

### Debug Build

Edit `Makefile` and change:
```makefile
# Release build (default)
CFLAGS = -Wall -Wextra -O2 -std=gnu99

# Debug build
CFLAGS = -Wall -Wextra -g -O0 -std=gnu99 -DDEBUG
```

Then rebuild:
```bash
make clean
make cross
```

### Custom Compiler Flags

```bash
make cross CFLAGS="-O3 -march=armv7-a"
```

---

## Verifying the Build

### Check Binary Architecture

```bash
file vita49_streamer
```

**Expected output:**
```
vita49_streamer: ELF 32-bit LSB executable, ARM, EABI5 version 1 (SYSV), dynamically linked, interpreter /lib/ld-linux-armhf.so.3, for GNU/Linux 3.2.0, not stripped
```

### Check Binary Size

```bash
ls -lh vita49_streamer
```

**Expected:** ~50-100 KB (depending on whether stripped)

### Strip Binary (Optional)

To reduce size:
```bash
arm-linux-gnueabihf-strip vita49_streamer
```

---

## Troubleshooting

### "arm-linux-gnueabihf-gcc: command not found"

**Solution:** Install ARM cross-compiler:
```bash
# Ubuntu/Debian
sudo apt-get install gcc-arm-linux-gnueabihf

# Or use Docker/WSL methods
```

### "fatal error: iio.h: No such file or directory"

**Solution:** Install libiio development files:
```bash
sudo apt-get install libiio-dev
```

### "Can't connect to pluto.local"

**Solutions:**
```bash
# Try IP directly
ping 192.168.2.1
make deploy PLUTO_IP=192.168.2.1

# Check hosts file
echo "192.168.2.1 pluto.local" | sudo tee -a /etc/hosts

# Windows: C:\Windows\System32\drivers\etc\hosts
```

### Binary won't run on Pluto: "./vita49_streamer: not found"

**Cause:** Wrong architecture (compiled for x86 instead of ARM)

**Solution:**
```bash
# Verify you used cross-compile
file vita49_streamer  # Should show "ARM"

# Rebuild with cross-compiler
make clean
make cross
```

### "error while loading shared libraries: libiio.so.0"

**Solution:** Install libiio on Pluto:
```bash
ssh root@pluto.local
opkg update
opkg install libiio0
```

### Docker build fails on Windows

**Solution:** Ensure line endings are correct:
```bash
# In Git Bash or WSL
git config core.autocrlf input
git checkout .
```

### Permission denied when deploying

**Solutions:**
```bash
# Verify SSH access
ssh root@pluto.local  # Password: analog

# Check SSH keys
ssh-copy-id root@pluto.local

# Use explicit password
make deploy PLUTO_IP=pluto.local
# Enter password when prompted
```

---

## Binary Size Comparison

| Version | Size | Memory Usage |
|---------|------|--------------|
| Python + deps | ~15 MB | ~15 MB RAM |
| C binary (unstripped) | ~100 KB | ~2 MB RAM |
| C binary (stripped) | ~50 KB | ~2 MB RAM |

**Result: 300x smaller!**

---

## Dependencies

### On Your PC (Build System)
- ARM cross-compiler (`gcc-arm-linux-gnueabihf`)
- libiio development files (`libiio-dev`)
- make
- SSH/SCP client

### On Pluto (Runtime)
- libiio (pre-installed on Pluto+ firmware)
- Nothing else!

---

## Next Steps

After building successfully:

1. **Deploy to Pluto:** `make deploy`
2. **Configure from PC:** See [docs/USAGE.md](USAGE.md)
3. **Auto-start on boot:** See [docs/USAGE.md](USAGE.md#auto-start-on-boot)
4. **Run receivers:** See [examples/](../examples/)

---

## Additional Resources

- [USAGE.md](USAGE.md) - How to use the streamer
- [DEVELOPMENT.md](DEVELOPMENT.md) - Architecture and testing
- [Main README](../README.md) - Project overview
- [Makefile](../Makefile) - Build system details
