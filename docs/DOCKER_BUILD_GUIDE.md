# Docker Build Guide for ADALM-Pluto ARM Applications

**Complete guide to building ARM binaries for Pluto using Docker**

This guide provides comprehensive coverage of using Docker to build ARM applications for the ADALM-Pluto SDR, eliminating the need for native cross-compiler installation. Perfect for Windows, macOS, and Linux users who want a consistent, reproducible build environment.

---

## Table of Contents

1. [Why Use Docker?](#why-use-docker)
2. [Quick Start](#quick-start)
3. [Docker Architecture](#docker-architecture)
4. [Platform-Specific Setup](#platform-specific-setup)
5. [Understanding the Dockerfile](#understanding-the-dockerfile)
6. [Building Your Applications](#building-your-applications)
7. [Advanced Docker Workflows](#advanced-docker-workflows)
8. [Multi-Stage Builds](#multi-stage-builds)
9. [Docker Compose Development Environment](#docker-compose-development-environment)
10. [CI/CD Integration](#cicd-integration)
11. [Troubleshooting](#troubleshooting)

---

## Why Use Docker?

### Problems Docker Solves

#### 1. **Cross-Platform Consistency**
- No more "works on my machine" issues
- Identical build environment on Windows, macOS, and Linux
- Same compiler version, same libraries, every time

#### 2. **Zero Local Toolchain Installation**
- No need to install ARM cross-compiler (can be tricky on Windows/macOS)
- No library version conflicts with your system
- Clean separation between build environment and host

#### 3. **Reproducible Builds**
- Dockerfile defines exact build environment
- Version control your build configuration
- Anyone can build your project with `docker build`

#### 4. **Isolated Dependencies**
- Build dependencies don't pollute your system
- Multiple projects with different toolchain versions
- Easy cleanup: just delete the container

### Comparison: Docker vs Native Toolchain

| Aspect | Docker | Native Toolchain |
|--------|--------|------------------|
| **Setup Time** | 5 minutes | 15-60 minutes |
| **Windows Support** | Excellent | Difficult (need WSL) |
| **macOS Support** | Excellent | Difficult |
| **Linux Support** | Excellent | Excellent |
| **Build Speed** | Slightly slower (first build) | Fast |
| **Disk Space** | ~500 MB (image) | ~200 MB (toolchain) |
| **Updates** | Change Dockerfile | System package manager |
| **Portability** | Perfect | System-dependent |
| **CI/CD** | Native support | Requires setup |

### When NOT to Use Docker

- **Building on Pluto itself**: Use native compilation
- **Very frequent small changes**: Native toolchain may be faster for rapid iteration
- **Limited disk space**: Docker images take ~500 MB
- **No Docker support**: Embedded systems, restricted environments

---

## Quick Start

### Windows

1. **Install Docker Desktop**
   - Download: https://www.docker.com/products/docker-desktop
   - Run installer, reboot if prompted
   - Start Docker Desktop from Start Menu

2. **Build the project**
   ```powershell
   cd C:\git-repos\vita49-pluto
   .\scripts\build-with-docker.bat
   ```

3. **Deploy to Pluto**
   ```powershell
   scp vita49_streamer root@pluto.local:/root/
   ssh root@pluto.local
   ./vita49_streamer
   ```

### Linux

1. **Install Docker**
   ```bash
   # Ubuntu/Debian
   sudo apt-get update
   sudo apt-get install docker.io
   sudo systemctl start docker
   sudo usermod -aG docker $USER
   # Log out and back in for group to take effect

   # Fedora
   sudo dnf install docker
   sudo systemctl start docker
   sudo usermod -aG docker $USER
   ```

2. **Build the project**
   ```bash
   cd ~/vita49-pluto
   ./scripts/build-with-docker.sh
   ```

3. **Deploy to Pluto**
   ```bash
   make deploy-binary
   ```

### macOS

1. **Install Docker Desktop**
   - Download: https://www.docker.com/products/docker-desktop
   - Drag Docker.app to Applications
   - Launch Docker Desktop

2. **Build the project**
   ```bash
   cd ~/vita49-pluto
   ./scripts/build-with-docker.sh
   ```

3. **Deploy to Pluto**
   ```bash
   make deploy-binary
   ```

---

## Docker Architecture

### How Docker Works for Cross-Compilation

```
┌─────────────────────────────────────────────────────────────────┐
│                         Your Computer                           │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      Docker Engine                        │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │              Docker Container                       │  │  │
│  │  │  ┌───────────────────────────────────────────────┐  │  │  │
│  │  │  │           Debian Linux (ARM Tools)            │  │  │  │
│  │  │  │                                               │  │  │  │
│  │  │  │  • gcc-arm-linux-gnueabihf (cross-compiler)  │  │  │  │
│  │  │  │  • libiio-dev:armhf (ARM libraries)          │  │  │  │
│  │  │  │  • make, build tools                         │  │  │  │
│  │  │  │                                               │  │  │  │
│  │  │  │  /build (volume mount) ◄──────────┐          │  │  │  │
│  │  │  │     ├── src/                      │          │  │  │  │
│  │  │  │     ├── Makefile ──► Compilation  │          │  │  │  │
│  │  │  │     └── vita49_streamer (output)  │          │  │  │  │
│  │  │  └───────────────────────────────────┼──────────┘  │  │  │
│  │  └────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Your Project Directory (bind mounted to /build)                │
│  C:\git-repos\vita49-pluto  or  ~/vita49-pluto                  │
│     ├── src/                                                     │
│     ├── Makefile                                                 │
│     └── vita49_streamer ◄─── Binary appears here!               │
└──────────────────────────────────────────────────────────────────┘
```

### Key Concepts

#### 1. **Docker Image** (Build Recipe)
- Read-only template containing OS + tools
- Built once from Dockerfile
- Stored locally, can be shared
- ~500 MB for our build environment

#### 2. **Docker Container** (Running Instance)
- Temporary execution environment from image
- Isolated from host system
- Shares kernel with host (Linux containers on Linux kernel)
- Destroyed after build completes

#### 3. **Volume Mount** (File Sharing)
- Your project directory mounted into container
- Container writes output to mount
- Files appear on host immediately
- **Critical**: This is how binaries get back to your system

#### 4. **Dockerfile** (Build Instructions)
- Text file describing how to build image
- Lists packages to install
- Sets up build environment
- Defines default command

### Build Flow

```
Step 1: Build Docker Image (once)
┌────────────────────────────┐
│ docker build -t pluto-builder │
│                            │
│ Dockerfile ──► Docker Image│
│               (500 MB)     │
└────────────────────────────┘
           ▼
Step 2: Run Container (each build)
┌────────────────────────────┐
│ docker run --rm -v pwd:/build │
│                            │
│ Docker Image ──► Container │
│ + Volume Mount             │
└────────────────────────────┘
           ▼
Step 3: Compilation Inside Container
┌────────────────────────────┐
│ Container executes:        │
│   make cross               │
│                            │
│ Compiles ARM binary        │
└────────────────────────────┘
           ▼
Step 4: Binary Appears on Host
┌────────────────────────────┐
│ Your Project Directory:    │
│   vita49_streamer (ARM)    │
│                            │
│ Ready to deploy!           │
└────────────────────────────┘
```

---

## Platform-Specific Setup

### Windows Detailed Setup

#### Prerequisites

**System Requirements:**
- Windows 10/11 64-bit (Pro, Enterprise, or Education for Hyper-V)
- 4 GB RAM minimum (8 GB recommended)
- Virtualization enabled in BIOS
- 10 GB free disk space

#### Installation Steps

1. **Install Docker Desktop**
   ```
   Download: https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe
   ```

   - Run installer as Administrator
   - Follow installation wizard
   - When prompted, enable WSL 2 (recommended)
   - Reboot when installation completes

2. **Verify Docker Installation**
   ```powershell
   # Open PowerShell
   docker --version
   # Expected: Docker version 24.0.x or higher

   docker run hello-world
   # Should download and run test container
   ```

3. **Configure Docker Desktop**
   - Open Docker Desktop
   - Settings → Resources → Advanced
   - Set CPU: 2 cores minimum (4 recommended)
   - Set Memory: 4 GB minimum (8 GB recommended)
   - Click "Apply & Restart"

4. **Clone Repository**
   ```powershell
   cd C:\git-repos
   git clone https://github.com/your-repo/vita49-pluto.git
   cd vita49-pluto
   ```

5. **Build with Docker**
   ```powershell
   .\scripts\build-with-docker.bat
   ```

#### Windows-Specific Tips

**Line Endings:**
Git on Windows may convert line endings to CRLF, which breaks shell scripts.

```powershell
# Configure Git to preserve line endings
git config --global core.autocrlf false
git config --global core.eol lf

# Re-checkout repository
git checkout .
```

**Path Issues:**
Use Windows-native paths in PowerShell:
```powershell
# Good
docker run --rm -v ${PWD}:/build pluto-builder

# Also works (Git Bash)
docker run --rm -v "$(pwd)":/build pluto-builder
```

**Antivirus:**
Some antivirus software interferes with Docker. Add exclusions:
- `C:\ProgramData\Docker`
- `C:\Users\<YourUser>\AppData\Local\Docker`
- Your project directory

**Firewall:**
First Docker run may trigger Windows Firewall prompt. Click "Allow access".

### Linux Detailed Setup

#### Installation by Distribution

**Ubuntu/Debian:**
```bash
# Update package database
sudo apt-get update

# Install Docker
sudo apt-get install -y docker.io

# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group (avoid sudo)
sudo usermod -aG docker $USER

# Apply group changes (log out and back in, or:)
newgrp docker
```

**Fedora/RHEL/CentOS:**
```bash
# Install Docker
sudo dnf install -y docker

# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

**Arch Linux:**
```bash
# Install Docker
sudo pacman -S docker

# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

#### Verify Installation

```bash
# Check Docker version
docker --version

# Test Docker
docker run hello-world

# Check user is in docker group
groups | grep docker
```

#### SELinux Considerations (Fedora/RHEL/CentOS)

If using SELinux, volume mounts need special handling:

```bash
# Add :z flag to volume mount for SELinux
docker run --rm -v "$(pwd)":/build:z pluto-builder
```

Or modify scripts to include `:z`:
```bash
# In build-with-docker.sh, change:
docker run --rm -v "$(pwd)":/build pluto-builder

# To:
docker run --rm -v "$(pwd)":/build:z pluto-builder
```

### macOS Detailed Setup

#### Prerequisites

- macOS 11 (Big Sur) or later
- Apple Silicon (M1/M2) or Intel Mac
- 4 GB RAM available for Docker
- 10 GB free disk space

#### Installation

1. **Install Docker Desktop**
   ```
   Download: https://desktop.docker.com/mac/main/arm64/Docker.dmg (Apple Silicon)
   Or: https://desktop.docker.com/mac/main/amd64/Docker.dmg (Intel)
   ```

2. **Install and Configure**
   - Open the downloaded .dmg file
   - Drag Docker.app to Applications
   - Launch Docker from Applications
   - Grant privileged access when prompted
   - Wait for Docker engine to start (whale icon in menu bar)

3. **Verify Installation**
   ```bash
   docker --version
   docker run hello-world
   ```

4. **Configure Resources**
   - Click Docker icon in menu bar
   - Preferences → Resources
   - Set CPU: 2 cores minimum
   - Set Memory: 4 GB minimum
   - Click "Apply & Restart"

#### macOS-Specific Notes

**Apple Silicon (M1/M2):**
Docker runs ARM containers natively, but our build still cross-compiles for ARM hard-float ABI used by Pluto.

**File Sharing:**
Docker Desktop automatically shares `/Users`, `/Volumes`, `/tmp`, and `/private`. If your repo is elsewhere, add it in:
- Preferences → Resources → File Sharing

**Performance:**
macOS Docker uses a VM (HyperKit on Intel, Virtualization.framework on Apple Silicon). Builds are slightly slower than native Linux but faster than Windows.

---

## Understanding the Dockerfile

### Current Dockerfile Explained

Location: `docker/Dockerfile`

```dockerfile
# Line-by-line explanation

# Base image: Debian Stretch (oldstable)
FROM debian:stretch

# Configure archive repository (Stretch reached EOL)
RUN echo "deb http://archive.debian.org/debian stretch main" > /etc/apt/sources.list && \
    echo "deb http://archive.debian.org/debian-security stretch/updates main" >> /etc/apt/sources.list && \
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99no-check-valid-until

# Enable ARM architecture for multi-arch packages
# This allows installing armhf libraries alongside amd64
RUN dpkg --add-architecture armhf

# Install cross-compilation toolchain
RUN apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf \    # ARM cross-compiler
    make \                        # Build system
    file \                        # Binary inspection tool
    libiio-dev:armhf \           # LibIIO for ARM (cross-compile target)
    && rm -rf /var/lib/apt/lists/*  # Clean up to reduce image size

# Configure pkg-config for cross-compilation
# Tells pkg-config where to find ARM library metadata
ENV PKG_CONFIG_PATH=/usr/lib/arm-linux-gnueabihf/pkgconfig
ENV PKG_CONFIG_LIBDIR=/usr/lib/arm-linux-gnueabihf/pkgconfig

# Set working directory inside container
WORKDIR /build

# Default command when container starts
CMD ["make", "all-tools"]
```

### Why Debian Stretch?

**Stability and Compatibility:**
- Pluto firmware is based on older kernel (4.x)
- Debian Stretch provides compatible glibc version
- Newer distributions may produce binaries that won't run on Pluto

**Alternative: Use Debian Bullseye**
```dockerfile
FROM debian:bullseye

RUN dpkg --add-architecture armhf && \
    apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf \
    make \
    file \
    libiio-dev:armhf \
    && rm -rf /var/lib/apt/lists/*

ENV PKG_CONFIG_PATH=/usr/lib/arm-linux-gnueabihf/pkgconfig
ENV PKG_CONFIG_LIBDIR=/usr/lib/arm-linux-gnueabihf/pkgconfig

WORKDIR /build
CMD ["make", "all-tools"]
```

Works fine for Pluto, more up-to-date packages.

### Key Dockerfile Concepts

#### Multi-Architecture Support

```dockerfile
RUN dpkg --add-architecture armhf
```

Debian can host libraries for multiple architectures:
- `amd64`: x86_64 (your PC)
- `armhf`: ARM hard-float (Pluto)
- `arm64`: 64-bit ARM (not used here)

This allows installing ARM libraries (`libiio-dev:armhf`) on an x86_64 system.

#### Layer Caching

Each `RUN` command creates a new layer. Docker caches layers:

```dockerfile
# Good: Packages rarely change, so this layer is cached
RUN apt-get update && apt-get install -y gcc-arm-linux-gnueabihf

# Bad: Rebuilds layer every time even if code unchanged
COPY src/ /build/
RUN make
```

**Best Practice**: Put frequently changing operations last.

#### Image Size Optimization

```dockerfile
# Clean up in same layer
RUN apt-get update && apt-get install -y package \
    && rm -rf /var/lib/apt/lists/*

# Not this (creates extra layer with cached packages):
RUN apt-get update && apt-get install -y package
RUN rm -rf /var/lib/apt/lists/*
```

---

## Building Your Applications

### Building the Default Project

#### Using Scripts (Easiest)

**Windows:**
```powershell
.\scripts\build-with-docker.bat
```

**Linux/macOS:**
```bash
./scripts/build-with-docker.sh
```

What happens:
1. Script changes to project root
2. Builds Docker image from `docker/Dockerfile`
3. Runs container with volume mount
4. Compiles ARM binaries
5. Verifies output

#### Manual Build (More Control)

**Step 1: Build Docker Image**
```bash
# From project root
docker build -t pluto-builder -f docker/Dockerfile .

# -t pluto-builder: Tag (name) the image
# -f docker/Dockerfile: Path to Dockerfile
# .: Build context (current directory)
```

**Step 2: Run Compilation**
```bash
# Linux/macOS
docker run --rm -v "$(pwd)":/build pluto-builder

# Windows PowerShell
docker run --rm -v ${PWD}:/build pluto-builder

# Explanation:
#   --rm: Delete container after run
#   -v "$(pwd)":/build: Mount current dir to /build in container
#   pluto-builder: Image to run
#   (no command): Uses CMD from Dockerfile (make all-tools)
```

**Step 3: Verify Output**
```bash
ls -lh vita49_streamer iio_buffer_diagnostic
file vita49_streamer
```

Expected:
```
vita49_streamer: ELF 32-bit LSB executable, ARM, EABI5 version 1 (SYSV)
```

### Building Custom Applications

#### Method 1: Modify Makefile

Add your application to `Makefile`:

```makefile
# Add to targets
TARGET_EMULATOR = target_emulator

all-tools: cross diagnostic target-emulator

target-emulator:
	@echo "Building target emulator..."
	$(CC_CROSS) $(CFLAGS) -o $(TARGET_EMULATOR) src/target_emulator.c $(LDFLAGS)
	$(STRIP_CROSS) $(TARGET_EMULATOR)
```

Build with Docker:
```bash
docker run --rm -v "$(pwd)":/build pluto-builder make target-emulator
```

#### Method 2: Interactive Docker Shell

Enter the container interactively:

```bash
# Start interactive shell in container
docker run --rm -it -v "$(pwd)":/build pluto-builder bash

# Inside container, you're at /build (your project directory)
root@abc123:/build#

# Compile manually
arm-linux-gnueabihf-gcc -o my_app src/my_app.c -liio -lpthread -lm

# Or use make
make target-emulator

# Exit container
exit

# Binary is now on your host system
ls -lh my_app
```

#### Method 3: Custom Dockerfile

Create `docker/Dockerfile.custom`:

```dockerfile
FROM debian:bullseye

RUN dpkg --add-architecture armhf && \
    apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf \
    make \
    libiio-dev:armhf \
    libfftw3-dev:armhf \      # Add custom libraries
    && rm -rf /var/lib/apt/lists/*

ENV PKG_CONFIG_PATH=/usr/lib/arm-linux-gnueabihf/pkgconfig
ENV PKG_CONFIG_LIBDIR=/usr/lib/arm-linux-gnueabihf/pkgconfig

WORKDIR /build
CMD ["make", "my-custom-target"]
```

Build and use:
```bash
docker build -t pluto-custom -f docker/Dockerfile.custom .
docker run --rm -v "$(pwd)":/build pluto-custom
```

### Compilation Flags and Optimization

#### Debug Build

```bash
# Interactive mode
docker run --rm -it -v "$(pwd)":/build pluto-builder bash

# Inside container
arm-linux-gnueabihf-gcc -g -O0 -DDEBUG -o my_app src/my_app.c -liio

# Or via make
make clean
make cross CFLAGS="-g -O0 -DDEBUG"
```

#### Optimized Build (Production)

```bash
# Maximum optimization
docker run --rm -v "$(pwd)":/build pluto-builder \
    make cross CFLAGS="-O3 -march=armv7-a -mtune=cortex-a9 -mfpu=neon -ffast-math"
```

#### Static Linking (No Dependencies)

```bash
# Fully static binary
docker run --rm -v "$(pwd)":/build pluto-builder bash -c "\
    arm-linux-gnueabihf-gcc -static -o my_app src/my_app.c -liio -lpthread -lm && \
    arm-linux-gnueabihf-strip my_app"
```

**Pros**: No library dependencies on Pluto
**Cons**: Much larger binary (~2 MB vs 50 KB)

---

## Advanced Docker Workflows

### Caching Build Artifacts

Speed up rebuilds by caching compiled objects:

```bash
# Create cache directory
mkdir -p .docker-cache

# Mount cache directory
docker run --rm \
    -v "$(pwd)":/build \
    -v "$(pwd)/.docker-cache":/cache \
    pluto-builder bash -c "make clean && make cross"
```

### Building for Multiple Targets

Build several applications in one go:

```bash
# Build all targets
docker run --rm -v "$(pwd)":/build pluto-builder make all-tools

# Or specific targets
docker run --rm -v "$(pwd)":/build pluto-builder bash -c "\
    make vita49_streamer && \
    make iio_buffer_diagnostic && \
    make target_emulator"
```

### Parallel Builds

Use make's parallel build feature:

```bash
# Use 4 parallel jobs
docker run --rm -v "$(pwd)":/build pluto-builder make -j4 all-tools
```

### Build Automation Script

Create `scripts/docker-build-all.sh`:

```bash
#!/bin/bash
set -e

echo "Building all Pluto applications with Docker..."

# Build Docker image
docker build -t pluto-builder -f docker/Dockerfile .

# Build applications
APPS=(
    "vita49_streamer"
    "iio_buffer_diagnostic"
    "target_emulator"
)

for app in "${APPS[@]}"; do
    echo "Building $app..."
    docker run --rm -v "$(pwd)":/build pluto-builder make "$app"

    if [ -f "$app" ]; then
        echo "✓ $app built successfully"
        file "$app"
        ls -lh "$app"
    else
        echo "✗ $app build failed"
        exit 1
    fi
done

echo "All applications built successfully!"
```

Make executable and run:
```bash
chmod +x scripts/docker-build-all.sh
./scripts/docker-build-all.sh
```

### Using Docker BuildKit

Enable advanced Docker features:

```bash
# Linux/macOS
export DOCKER_BUILDKIT=1
docker build -t pluto-builder -f docker/Dockerfile .

# Windows PowerShell
$env:DOCKER_BUILDKIT=1
docker build -t pluto-builder -f docker/Dockerfile .
```

Benefits:
- Faster builds
- Better caching
- Parallel stage execution
- Build secrets support

---

## Multi-Stage Builds

Reduce final image size with multi-stage builds:

### Example: Development + Production Images

Create `docker/Dockerfile.multistage`:

```dockerfile
# Stage 1: Build environment (large)
FROM debian:bullseye AS builder

RUN dpkg --add-architecture armhf && \
    apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf \
    make \
    file \
    libiio-dev:armhf \
    git \
    vim \
    gdb-multiarch \
    && rm -rf /var/lib/apt/lists/*

ENV PKG_CONFIG_PATH=/usr/lib/arm-linux-gnueabihf/pkgconfig
ENV PKG_CONFIG_LIBDIR=/usr/lib/arm-linux-gnueabihf/pkgconfig

WORKDIR /build

# Stage 2: Minimal runtime (small)
FROM debian:bullseye-slim AS runtime

# Only install runtime libraries
RUN dpkg --add-architecture armhf && \
    apt-get update && apt-get install -y \
    libiio0:armhf \
    && rm -rf /var/lib/apt/lists/*

# Copy only built binaries from builder stage
COPY --from=builder /build/vita49_streamer /usr/local/bin/
COPY --from=builder /build/iio_buffer_diagnostic /usr/local/bin/

CMD ["/usr/local/bin/vita49_streamer"]
```

Build specific stage:
```bash
# Build development image (with all tools)
docker build --target builder -t pluto-dev -f docker/Dockerfile.multistage .

# Build runtime image (minimal)
docker build --target runtime -t pluto-runtime -f docker/Dockerfile.multistage .

# Use development image for building
docker run --rm -v "$(pwd)":/build pluto-dev make all-tools

# Use runtime image for testing (if running x86 emulation)
docker run --rm pluto-runtime vita49_streamer --help
```

---

## Docker Compose Development Environment

For complex setups with multiple services (build, test, deploy):

### docker-compose.yml

Create in project root:

```yaml
version: '3.8'

services:
  # Build service
  builder:
    build:
      context: .
      dockerfile: docker/Dockerfile
    image: pluto-builder
    volumes:
      - .:/build
      - build-cache:/cache
    working_dir: /build
    command: make all-tools

  # Interactive development shell
  dev:
    image: pluto-builder
    volumes:
      - .:/build
    working_dir: /build
    stdin_open: true
    tty: true
    command: bash

  # Automated testing
  test:
    image: pluto-builder
    volumes:
      - .:/build
    working_dir: /build
    command: bash -c "make all-tools && pytest tests/"

volumes:
  build-cache:
```

### Using Docker Compose

```bash
# Build all services
docker-compose build

# Run build
docker-compose run --rm builder

# Start development shell
docker-compose run --rm dev

# Run tests
docker-compose run --rm test

# Custom command
docker-compose run --rm builder make target-emulator
```

### Advanced Compose with Deployment

```yaml
version: '3.8'

services:
  builder:
    build:
      context: .
      dockerfile: docker/Dockerfile
    volumes:
      - .:/build
    command: make all-tools

  deployer:
    image: pluto-builder
    volumes:
      - .:/build
      - ~/.ssh:/root/.ssh:ro  # Mount SSH keys
    environment:
      - PLUTO_IP=${PLUTO_IP:-pluto.local}
    working_dir: /build
    command: make deploy-binary

  full-pipeline:
    image: pluto-builder
    volumes:
      - .:/build
      - ~/.ssh:/root/.ssh:ro
    environment:
      - PLUTO_IP=${PLUTO_IP:-pluto.local}
    working_dir: /build
    command: bash -c "make all-tools && make deploy-all"
```

Run full pipeline:
```bash
# Set Pluto IP
export PLUTO_IP=192.168.2.1

# Build and deploy in one command
docker-compose run --rm full-pipeline
```

---

## CI/CD Integration

### GitHub Actions

Create `.github/workflows/docker-build.yml`:

```yaml
name: Docker ARM Build

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:  # Manual trigger

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build Docker image
        run: |
          docker build -t pluto-builder -f docker/Dockerfile .

      - name: Compile ARM binaries
        run: |
          docker run --rm -v ${{ github.workspace }}:/build pluto-builder

      - name: Verify binaries
        run: |
          ls -lh vita49_streamer iio_buffer_diagnostic
          file vita49_streamer
          test -f vita49_streamer || exit 1
          test -f iio_buffer_diagnostic || exit 1

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: pluto-arm-binaries
          path: |
            vita49_streamer
            iio_buffer_diagnostic
          retention-days: 30

      - name: Create release (on tag)
        if: startsWith(github.ref, 'refs/tags/v')
        uses: softprops/action-gh-release@v1
        with:
          files: |
            vita49_streamer
            iio_buffer_diagnostic
```

### GitLab CI

Create `.gitlab-ci.yml`:

```yaml
image: docker:latest

services:
  - docker:dind

stages:
  - build
  - test
  - deploy

variables:
  DOCKER_DRIVER: overlay2
  DOCKER_TLS_CERTDIR: "/certs"

build:arm:
  stage: build
  script:
    - docker build -t pluto-builder -f docker/Dockerfile .
    - docker run --rm -v $(pwd):/build pluto-builder
    - ls -lh vita49_streamer
    - file vita49_streamer
  artifacts:
    paths:
      - vita49_streamer
      - iio_buffer_diagnostic
    expire_in: 1 week

test:binaries:
  stage: test
  script:
    - test -f vita49_streamer
    - test -f iio_buffer_diagnostic
    - file vita49_streamer | grep ARM

deploy:pluto:
  stage: deploy
  only:
    - main
  script:
    - apt-get update && apt-get install -y openssh-client
    - eval $(ssh-agent -s)
    - echo "$SSH_PRIVATE_KEY" | tr -d '\r' | ssh-add -
    - mkdir -p ~/.ssh
    - chmod 700 ~/.ssh
    - scp -o StrictHostKeyChecking=no vita49_streamer root@${PLUTO_IP}:/root/
```

### Jenkins Pipeline

Create `Jenkinsfile`:

```groovy
pipeline {
    agent any

    environment {
        DOCKER_IMAGE = 'pluto-builder'
        PLUTO_IP = credentials('pluto-ip')
    }

    stages {
        stage('Build Docker Image') {
            steps {
                script {
                    docker.build(DOCKER_IMAGE, '-f docker/Dockerfile .')
                }
            }
        }

        stage('Compile ARM Binaries') {
            steps {
                script {
                    docker.image(DOCKER_IMAGE).inside('-v $WORKSPACE:/build') {
                        sh 'make all-tools'
                    }
                }
            }
        }

        stage('Test Binaries') {
            steps {
                sh 'ls -lh vita49_streamer iio_buffer_diagnostic'
                sh 'file vita49_streamer | grep ARM'
            }
        }

        stage('Archive Artifacts') {
            steps {
                archiveArtifacts artifacts: 'vita49_streamer,iio_buffer_diagnostic', fingerprint: true
            }
        }

        stage('Deploy to Pluto') {
            when {
                branch 'main'
            }
            steps {
                sshagent(credentials: ['pluto-ssh-key']) {
                    sh 'scp vita49_streamer root@${PLUTO_IP}:/root/'
                }
            }
        }
    }

    post {
        always {
            cleanWs()
        }
    }
}
```

---

## Troubleshooting

### Common Issues and Solutions

#### 1. "Cannot connect to Docker daemon"

**Symptoms:**
```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock
```

**Solutions:**

**Linux:**
```bash
# Start Docker service
sudo systemctl start docker

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Or run with sudo (not recommended)
sudo docker build ...
```

**Windows/macOS:**
- Ensure Docker Desktop is running (check system tray)
- Restart Docker Desktop
- Reinstall Docker Desktop if persistent

#### 2. "Volume mount not working" (Files don't appear)

**Symptoms:**
- Docker build succeeds
- No binaries appear in project directory
- Empty /build directory in container

**Solutions:**

**Windows:**
```powershell
# Check Docker Desktop settings
# Settings → Resources → File Sharing
# Ensure C:\ (or your drive) is shared

# Use absolute paths
docker run --rm -v C:\git-repos\vita49-pluto:/build pluto-builder

# Or use ${PWD}
docker run --rm -v ${PWD}:/build pluto-builder
```

**Linux:**
```bash
# Use $(pwd) not $PWD
docker run --rm -v "$(pwd)":/build pluto-builder

# Check permissions
ls -ld "$(pwd)"
```

**macOS:**
```bash
# Ensure directory is under /Users
# Add to File Sharing if outside: Docker → Preferences → Resources → File Sharing
```

#### 3. "Permission denied" writing to volume

**Symptoms:**
```
make: *** cannot create executable file
Permission denied
```

**Solutions:**

**Linux (SELinux):**
```bash
# Add :z flag
docker run --rm -v "$(pwd)":/build:z pluto-builder
```

**All platforms:**
```bash
# Check ownership
ls -l

# Fix permissions
chmod -R u+w .
```

#### 4. "Cross-compiler not found" inside container

**Symptoms:**
```
arm-linux-gnueabihf-gcc: command not found
```

**Solutions:**
```bash
# Rebuild Docker image
docker build --no-cache -t pluto-builder -f docker/Dockerfile .

# Verify image contains compiler
docker run --rm -it pluto-builder bash
# Inside container:
which arm-linux-gnueabihf-gcc
arm-linux-gnueabihf-gcc --version
```

#### 5. "libiio not found" during linking

**Symptoms:**
```
/usr/bin/ld: cannot find -liio
```

**Solutions:**

Check Dockerfile has:
```dockerfile
RUN apt-get install -y libiio-dev:armhf
```

Rebuild image:
```bash
docker build --no-cache -t pluto-builder -f docker/Dockerfile .
```

Verify libraries in container:
```bash
docker run --rm -it pluto-builder bash
# Inside:
dpkg -L libiio-dev:armhf
```

#### 6. Binary won't run on Pluto: "not found"

**Symptoms:**
```
./vita49_streamer
-sh: ./vita49_streamer: not found
```

**Cause:** Wrong architecture or missing dynamic linker

**Solutions:**

Check binary on host:
```bash
file vita49_streamer
# Should show: ARM, EABI5, dynamically linked, interpreter /lib/ld-linux-armhf.so.3
```

If shows x86_64, rebuild was not cross-compiled:
```bash
# Ensure using correct image
docker run --rm -v "$(pwd)":/build pluto-builder make clean cross

# Verify cross-compiler is used
docker run --rm pluto-builder which arm-linux-gnueabihf-gcc
```

#### 7. Docker build extremely slow (Windows)

**Cause:** Windows file system performance

**Solutions:**
```powershell
# Use WSL 2 backend (not Hyper-V)
# Docker Desktop → Settings → General → "Use WSL 2 based engine"

# Move repository to WSL filesystem
wsl
cd ~
git clone <repo>
./scripts/build-with-docker.sh
```

**Alternative:** Build inside WSL entirely:
```powershell
# Install Docker in WSL
wsl
sudo apt-get update
sudo apt-get install docker.io
sudo usermod -aG docker $USER
```

#### 8. "No space left on device"

**Symptoms:**
```
Error response from daemon: no space left on device
```

**Solutions:**

**Clean up Docker:**
```bash
# Remove unused images
docker system prune -a

# Remove all containers
docker container prune

# Check disk usage
docker system df
```

**Increase Docker disk space (Docker Desktop):**
- Settings → Resources → Advanced → Disk image size
- Increase to 20-30 GB
- Apply & Restart

#### 9. Build cache not invalidated

**Symptoms:**
- Changes to source not reflected in binary
- Old binary keeps reappearing

**Solutions:**

```bash
# Force rebuild without cache
docker build --no-cache -t pluto-builder -f docker/Dockerfile .

# Clean build
make clean
docker run --rm -v "$(pwd)":/build pluto-builder make clean all-tools

# Or in Docker Compose
docker-compose build --no-cache
```

#### 10. Container runs but doesn't compile

**Symptoms:**
- Docker run succeeds with exit code 0
- No binary produced

**Debug:**

```bash
# Run with interactive shell
docker run --rm -it -v "$(pwd)":/build pluto-builder bash

# Inside container, check what's in /build
ls -la

# Try make manually
make clean
make cross

# Check for errors
echo $?
```

### Debug Mode

Enable verbose Docker output:

```bash
# Set debug mode
export DOCKER_BUILDKIT=0

# Verbose docker run
docker run --rm -v "$(pwd)":/build pluto-builder bash -x -c "make all-tools"
```

### Inspect Docker Image

```bash
# List all layers
docker history pluto-builder

# Inspect image metadata
docker inspect pluto-builder

# Enter image interactively
docker run --rm -it pluto-builder bash

# Check installed packages
docker run --rm pluto-builder dpkg -l | grep arm
```

---

## Best Practices

### 1. Version Pin Dependencies

**Good:**
```dockerfile
FROM debian:bullseye-20230109

RUN apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf=4:10.2.1-1 \
    libiio-dev:armhf=0.21-1
```

**Benefit:** Reproducible builds, no surprises

### 2. Use .dockerignore

Create `.dockerignore` in project root:
```
.git
.github
*.md
tests/
docs/
*.pyc
__pycache__/
venv/
build/
dist/
```

**Benefit:** Faster builds, smaller context

### 3. Tag Images with Versions

```bash
# Tag with version
docker build -t pluto-builder:1.0 -f docker/Dockerfile .
docker build -t pluto-builder:latest -f docker/Dockerfile .

# Use specific version
docker run --rm -v "$(pwd)":/build pluto-builder:1.0
```

### 4. Multi-Platform Builds

For building on different architectures:

```bash
# Create buildx instance
docker buildx create --name multiplatform --use

# Build for multiple platforms
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -t pluto-builder:multiplatform \
    -f docker/Dockerfile .
```

### 5. Automate with Make

Add to `Makefile`:
```makefile
.PHONY: docker-build docker-compile docker-clean

docker-build:
	docker build -t pluto-builder -f docker/Dockerfile .

docker-compile: docker-build
	docker run --rm -v $(PWD):/build pluto-builder

docker-clean:
	docker rmi pluto-builder
	docker system prune -f
```

Use:
```bash
make docker-compile
```

---

## Summary

### Quick Reference

| Task | Command |
|------|---------|
| **Build Docker image** | `docker build -t pluto-builder -f docker/Dockerfile .` |
| **Compile with Docker** | `docker run --rm -v "$(pwd)":/build pluto-builder` |
| **Interactive shell** | `docker run --rm -it -v "$(pwd)":/build pluto-builder bash` |
| **Custom command** | `docker run --rm -v "$(pwd)":/build pluto-builder make target` |
| **Clean Docker** | `docker system prune -a` |
| **View images** | `docker images` |
| **View containers** | `docker ps -a` |

### When to Use What

| Scenario | Method |
|----------|--------|
| **First time build** | `scripts/build-with-docker.bat` or `.sh` |
| **Regular builds** | `docker run --rm -v "$(pwd)":/build pluto-builder` |
| **Debugging** | `docker run --rm -it -v "$(pwd)":/build pluto-builder bash` |
| **Custom targets** | `docker run --rm -v "$(pwd)":/build pluto-builder make target` |
| **CI/CD** | GitHub Actions / GitLab CI with Docker |
| **Multi-target** | Docker Compose |

### Advantages Recap

✅ No local toolchain installation
✅ Works on Windows, macOS, Linux
✅ Reproducible builds
✅ Isolated dependencies
✅ Easy CI/CD integration
✅ Version-controlled build environment

### Next Steps

1. **Build your first binary**: `scripts/build-with-docker.sh`
2. **Deploy to Pluto**: `make deploy-binary`
3. **Customize Dockerfile**: Add your dependencies
4. **Set up CI/CD**: Use GitHub Actions example
5. **Build custom apps**: See [LIBIIO_ARM_MULTICORE_GUIDE.md](LIBIIO_ARM_MULTICORE_GUIDE.md)

---

## Additional Resources

- **Docker Documentation**: https://docs.docker.com/
- **Docker Hub**: https://hub.docker.com/
- **ARM Cross-Compilation**: https://wiki.debian.org/CrossCompiling
- **LibIIO Documentation**: https://analogdevicesinc.github.io/libiio/
- **ADALM-Pluto Wiki**: https://wiki.analog.com/university/tools/pluto
