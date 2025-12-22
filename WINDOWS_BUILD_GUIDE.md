# Building VITA49 Streamer on Windows

## The Problem

Windows doesn't have the ARM cross-compiler by default. You have 3 options:

## Option 1: Use WSL (Recommended - Easiest)

Windows Subsystem for Linux gives you a real Linux environment.

### Install WSL

```powershell
# In PowerShell as Administrator
wsl --install
```

Reboot, then:

```bash
# Inside WSL (Ubuntu)
sudo apt-get update
sudo apt-get install -y gcc-arm-linux-gnueabihf libiio-dev make

# Navigate to your repo (Windows drives are at /mnt/)
cd /mnt/c/git-repos/vita49-pluto

# Build and deploy
make deploy
```

Done! This is the easiest and most reliable method.

## Option 2: Use Docker (No WSL Needed)

If you have Docker Desktop for Windows:

### Step 1: Create Dockerfile

Already created in your repo. Just verify `Dockerfile` exists with this content:

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

### Step 2: Build with Docker

```powershell
# In PowerShell or CMD
cd C:\git-repos\vita49-pluto

# Build Docker image
docker build -t pluto-builder .

# Compile the binary
docker run --rm -v ${PWD}:/build pluto-builder

# Deploy to Pluto (you'll need an SSH client)
scp vita49_streamer root@pluto.local:/root/
```

## Option 3: Pre-Built Binary (Fastest)

I can provide a pre-built ARM binary. Just copy it to Pluto:

### Using SCP (if you have it)

```powershell
scp vita49_streamer root@pluto.local:/root/
```

### Using WinSCP (GUI tool)

1. Download WinSCP: https://winscp.net/
2. Connect to Pluto:
   - Host: `pluto.local` (or `192.168.2.1`)
   - User: `root`
   - Password: `analog`
3. Copy `vita49_streamer` to `/root/`
4. Right-click → Properties → Set permissions to `rwxr-xr-x` (755)

### Using PuTTY/PSCP

```powershell
pscp vita49_streamer root@pluto.local:/root/
```

## Option 4: Cloud Build (GitHub Actions)

Set up GitHub Actions to build it automatically:

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

Push to GitHub, download the artifact from Actions tab.

---

## Quick Comparison

| Method | Setup Time | Difficulty | Best For |
|--------|------------|------------|----------|
| **WSL** | 5 min | Easy | Regular development |
| **Docker** | 10 min | Medium | One-time builds |
| **Pre-built** | 0 min | Very Easy | Just want to use it |
| **Cloud** | 15 min | Medium | CI/CD, teams |

---

## Recommendation for You

Since you're already on Windows and want to get running quickly:

### Immediate Solution: Use WSL

```powershell
# 1. Install WSL (if not already)
wsl --install

# 2. Restart computer

# 3. Open Ubuntu from Start Menu

# 4. In WSL terminal:
sudo apt-get update
sudo apt-get install -y gcc-arm-linux-gnueabihf libiio-dev make

# 5. Navigate to your repo
cd /mnt/c/git-repos/vita49-pluto

# 6. Build and deploy
make deploy
```

This gives you a permanent Linux environment that works seamlessly with Windows files.

---

## Alternative: I'll Provide a Pre-Built Binary

If you want to skip building entirely, I can create the Dockerfile and you can use Docker, or we can set up a GitHub Action to build it for you automatically.

Would you prefer:
1. **WSL setup** (5 minutes, best long-term)
2. **Docker setup** (10 minutes, good for occasional builds)
3. **Pre-built binary** (0 minutes, just copy and run)
4. **GitHub Actions** (15 minutes setup, automatic thereafter)

Let me know which approach you'd like to take!
