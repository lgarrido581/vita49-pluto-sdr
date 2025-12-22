FROM debian:bullseye

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
CMD ["make", "cross"]
