FROM debian:stretch

# Use Debian archive for Stretch (EOL)
RUN echo "deb http://archive.debian.org/debian stretch main" > /etc/apt/sources.list && \
    echo "deb http://archive.debian.org/debian-security stretch/updates main" >> /etc/apt/sources.list && \
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99no-check-valid-until

# Enable ARM architecture for multi-arch packages
RUN dpkg --add-architecture armhf

# Install ARM cross-compiler and dependencies
RUN apt-get update && apt-get install -y \
    gcc-arm-linux-gnueabihf \
    make \
    file \
    libiio-dev:armhf \
    && rm -rf /var/lib/apt/lists/*

# Set up pkg-config for cross-compilation
ENV PKG_CONFIG_PATH=/usr/lib/arm-linux-gnueabihf/pkgconfig
ENV PKG_CONFIG_LIBDIR=/usr/lib/arm-linux-gnueabihf/pkgconfig

WORKDIR /build
# Build both streamer and diagnostic tool
CMD ["make", "all-tools"]
