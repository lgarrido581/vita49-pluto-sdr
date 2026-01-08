# Makefile for VITA49 Pluto Streamer
#
# This Makefile supports both native compilation (on Pluto) and cross-compilation (on PC)
#
# Usage:
#   make              # Cross-compile for ARM (default)
#   make native       # Compile natively on Pluto
#   make deploy       # Cross-compile and deploy to Pluto
#   make diagnostic   # Build IIO buffer diagnostic tool
#   make clean        # Clean build files

# Cross-compiler settings (for building on PC)
CROSS_COMPILE ?= arm-linux-gnueabihf-
CC_CROSS = $(CROSS_COMPILE)gcc
STRIP_CROSS = $(CROSS_COMPILE)strip

# Native compiler (for building on Pluto)
CC_NATIVE = gcc
STRIP_NATIVE = strip

# Detect if we're on Pluto or PC
IS_PLUTO = $(shell test -f /sys/bus/iio/devices/iio:device0/name && echo 1 || echo 0)

# Select compiler based on target
ifeq ($(TARGET),native)
    CC = $(CC_NATIVE)
    STRIP = $(STRIP_NATIVE)
else
    CC = $(CC_CROSS)
    STRIP = $(STRIP_CROSS)
endif

# Compiler flags
CFLAGS = -Wall -Wextra -O2 -std=gnu99
LDFLAGS = -liio -lpthread
LDFLAGS_DIAG = -liio -lm

# Source files
SRC_DIR = src
C_SOURCE = $(SRC_DIR)/pluto_vita49_streamer.c
DIAG_SOURCE = $(SRC_DIR)/iio_buffer_diagnostic.c

# Target binaries
TARGET_BIN = vita49_streamer
DIAG_BIN = iio_buffer_diagnostic

# Pluto connection settings
PLUTO_IP ?= pluto.local
PLUTO_USER ?= root
PLUTO_PASS ?= analog

.PHONY: all native cross deploy clean install help diagnostic deploy-all test-ring-buffer

# Default target: cross-compile
all: cross

# Cross-compile for ARM
cross:
	@echo "=========================================="
	@echo "Cross-compiling for ARM (Pluto)"
	@echo "=========================================="
	$(CC_CROSS) $(CFLAGS) -o $(TARGET_BIN) $(C_SOURCE) $(LDFLAGS)
	$(STRIP_CROSS) $(TARGET_BIN)
	@echo ""
	@echo "✓ Build complete: $(TARGET_BIN)"
	@file $(TARGET_BIN)
	@ls -lh $(TARGET_BIN)

# Cross-compile diagnostic tool for ARM
diagnostic:
	@echo "=========================================="
	@echo "Cross-compiling IIO Diagnostic for ARM"
	@echo "=========================================="
	$(CC_CROSS) $(CFLAGS) -o $(DIAG_BIN) $(DIAG_SOURCE) $(LDFLAGS_DIAG)
	$(STRIP_CROSS) $(DIAG_BIN)
	@echo ""
	@echo "✓ Build complete: $(DIAG_BIN)"
	@file $(DIAG_BIN)
	@ls -lh $(DIAG_BIN)

# Build both streamer and diagnostic
all-tools: cross diagnostic
	@echo ""
	@echo "✓ All tools built successfully"

# Native compile (on Pluto)
native:
	@echo "=========================================="
	@echo "Native compilation (on Pluto)"
	@echo "=========================================="
	$(CC_NATIVE) $(CFLAGS) -o $(TARGET_BIN) $(C_SOURCE) $(LDFLAGS)
	$(STRIP_NATIVE) $(TARGET_BIN)
	@echo ""
	@echo "✓ Build complete: $(TARGET_BIN)"
	@ls -lh $(TARGET_BIN)

# Deploy to Pluto
deploy: cross
	@echo ""
	@echo "=========================================="
	@echo "Deploying to Pluto"
	@echo "=========================================="
	@echo "Target: $(PLUTO_USER)@$(PLUTO_IP)"
	@echo ""
	@if command -v sshpass >/dev/null 2>&1; then \
		echo "Using sshpass for password authentication..."; \
		sshpass -p $(PLUTO_PASS) scp -o StrictHostKeyChecking=no $(TARGET_BIN) $(PLUTO_USER)@$(PLUTO_IP):/root/; \
		sshpass -p $(PLUTO_PASS) ssh -o StrictHostKeyChecking=no $(PLUTO_USER)@$(PLUTO_IP) "chmod +x /root/$(TARGET_BIN)"; \
	else \
		echo "Enter password when prompted (default: analog)"; \
		scp $(TARGET_BIN) $(PLUTO_USER)@$(PLUTO_IP):/root/; \
		ssh $(PLUTO_USER)@$(PLUTO_IP) "chmod +x /root/$(TARGET_BIN)"; \
	fi
	@echo ""
	@echo "✓ Deployment complete!"
	@echo ""
	@echo "To run on Pluto:"
	@echo "  ssh $(PLUTO_USER)@$(PLUTO_IP)"
	@echo "  ./$(TARGET_BIN)"
	@echo ""

# Install dependencies (run on Pluto)
install-deps:
	@echo "Installing dependencies on Pluto..."
	@if [ "$(IS_PLUTO)" = "1" ]; then \
		echo "Running on Pluto - installing..."; \
		opkg update; \
		opkg install libiio0; \
	else \
		echo "Not running on Pluto. Use 'make deploy' instead."; \
	fi

# Clean build files
clean:
	@echo "Cleaning build files..."
	rm -f $(TARGET_BIN) $(DIAG_BIN)
	@echo "✓ Clean complete"

# Deploy pre-built binary (for Docker/Windows users)
deploy-binary:
	@echo ""
	@echo "=========================================="
	@echo "Deploying Pre-Built Binary to Pluto"
	@echo "=========================================="
	@echo "Target: $(PLUTO_USER)@$(PLUTO_IP)"
	@echo ""
	@if [ ! -f "$(TARGET_BIN)" ]; then \
		echo "ERROR: Binary $(TARGET_BIN) not found"; \
		echo "Build it first with: make cross (or use Docker)"; \
		exit 1; \
	fi
	@if command -v sshpass >/dev/null 2>&1; then \
		echo "Using sshpass for password authentication..."; \
		sshpass -p $(PLUTO_PASS) scp -o StrictHostKeyChecking=no $(TARGET_BIN) $(PLUTO_USER)@$(PLUTO_IP):/root/; \
		sshpass -p $(PLUTO_PASS) ssh -o StrictHostKeyChecking=no $(PLUTO_USER)@$(PLUTO_IP) "chmod +x /root/$(TARGET_BIN)"; \
	else \
		echo "Enter password when prompted (default: analog)"; \
		scp $(TARGET_BIN) $(PLUTO_USER)@$(PLUTO_IP):/root/; \
		ssh $(PLUTO_USER)@$(PLUTO_IP) "chmod +x /root/$(TARGET_BIN)"; \
	fi
	@echo ""
	@echo "✓ Deployment complete!"
	@echo ""
	@echo "To run on Pluto:"
	@echo "  ssh $(PLUTO_USER)@$(PLUTO_IP)"
	@echo "  ./$(TARGET_BIN)"
	@echo ""

# Deploy all binaries (streamer + diagnostic)
deploy-all: all-tools
	@echo ""
	@echo "=========================================="
	@echo "Deploying All Tools to Pluto"
	@echo "=========================================="
	@echo "Target: $(PLUTO_USER)@$(PLUTO_IP)"
	@echo ""
	@if command -v sshpass >/dev/null 2>&1; then \
		sshpass -p $(PLUTO_PASS) scp -o StrictHostKeyChecking=no $(TARGET_BIN) $(DIAG_BIN) $(PLUTO_USER)@$(PLUTO_IP):/root/; \
		sshpass -p $(PLUTO_PASS) ssh -o StrictHostKeyChecking=no $(PLUTO_USER)@$(PLUTO_IP) "chmod +x /root/$(TARGET_BIN) /root/$(DIAG_BIN)"; \
	else \
		scp $(TARGET_BIN) $(DIAG_BIN) $(PLUTO_USER)@$(PLUTO_IP):/root/; \
		ssh $(PLUTO_USER)@$(PLUTO_IP) "chmod +x /root/$(TARGET_BIN) /root/$(DIAG_BIN)"; \
	fi
	@echo ""
	@echo "✓ Deployment complete!"
	@echo ""
	@echo "To run on Pluto:"
	@echo "  ./$(TARGET_BIN)              # Run streamer"
	@echo "  ./$(DIAG_BIN) --help         # Run diagnostics"
	@echo ""

# Show help
help:
	@echo "VITA49 Pluto Streamer - Makefile Help"
	@echo "======================================="
	@echo ""
	@echo "Targets:"
	@echo "  make              Cross-compile streamer for ARM"
	@echo "  make diagnostic   Cross-compile IIO diagnostic tool"
	@echo "  make all-tools    Build both streamer and diagnostic"
	@echo "  make native       Compile natively on Pluto"
	@echo "  make deploy       Cross-compile streamer and deploy"
	@echo "  make deploy-all   Build and deploy all tools"
	@echo "  make deploy-binary Deploy pre-built binaries"
	@echo "  make clean        Clean build files"
	@echo "  make help         Show this help"
	@echo ""
	@echo "Diagnostic Tool:"
	@echo "  ./iio_buffer_diagnostic --help"
	@echo "  ./iio_buffer_diagnostic --all-rates"
	@echo "  ./iio_buffer_diagnostic --memory-test"
	@echo ""
	@echo "Configuration:"
	@echo "  PLUTO_IP          Pluto IP address (default: pluto.local)"
	@echo "  PLUTO_USER        SSH username (default: root)"
	@echo "  PLUTO_PASS        SSH password (default: analog)"
	@echo "  CROSS_COMPILE     Cross-compiler prefix (default: arm-linux-gnueabihf-)"
	@echo ""
	@echo "Examples:"
	@echo "  make deploy PLUTO_IP=192.168.2.1"
	@echo "  make deploy-all"
	@echo "  make cross CROSS_COMPILE=arm-buildroot-linux-gnueabihf-"
	@echo ""
	@echo "Docker workflow (Windows):"
	@echo "  build-with-docker.bat"
	@echo "  make deploy-binary PLUTO_IP=pluto.local"
	@echo ""

# Test ring buffer implementation
test-ring-buffer:
	@echo "=========================================="
	@echo "Testing Lock-Free Ring Buffer"
	@echo "=========================================="
	gcc -o tests/test_ring_buffer tests/test_ring_buffer.c -pthread -std=c11 -O2 -Wall -Wextra
	./tests/test_ring_buffer
	@rm -f tests/test_ring_buffer
	@echo ""
	@echo "✓ Ring buffer tests completed"
