# VITA49 Pluto Repository Cleanup - Migration Plan

**Date:** 2025-12-21
**Current State:** Baseline commit `6126631`
**Goal:** Clean, organized repository ready for GitHub publication

---

## Overview

This plan reorganizes the VITA49 Pluto repository from a development workspace into a professional, user-friendly open-source project. Each phase can be executed and committed independently.

---

## Phase 1: Create New Directory Structure

**Objective:** Set up the target directory structure without moving files yet.

### Actions:
```bash
mkdir -p src/vita49
mkdir -p src/streamers
mkdir -p examples
mkdir -p tests/e2e
mkdir -p scripts
mkdir -p docker
mkdir -p docs
mkdir -p systemd
```

### Create Package Files:
- `src/vita49/__init__.py` (empty for now, will populate later)

### Git Commit:
```
Phase 1: Create target directory structure

- Added src/vita49/ for VITA49 library
- Added src/streamers/ for Pluto streamer implementations
- Added examples/ for example code
- Added tests/e2e/ for end-to-end tests
- Added scripts/ for deployment scripts
- Added docker/ for Docker configurations
- Added docs/ for documentation
- Added systemd/ for service files
```

**Files Created:** 8 directories
**Files Modified:** 0
**Files Deleted:** 0

---

## Phase 2: Consolidate Documentation

**Objective:** Create a clear, comprehensive documentation structure.

### 2.1 Create Main README.md

**New Content Structure:**
```markdown
# VITA49 Pluto Streamer

Quick description and value proposition

## Quick Start
- 3-step deployment guide
- Link to detailed docs

## Features
- Key capabilities

## Documentation
- [Build Guide](docs/BUILD.md)
- [Usage Guide](docs/USAGE.md)
- [Development Guide](docs/DEVELOPMENT.md)

## License
MIT
```

### 2.2 Create docs/BUILD.md

**Consolidate from:**
- BUILD_AND_DEPLOY.md
- WINDOWS_BUILD_GUIDE.md
- README_C_VERSION.md (build sections)

**Sections:**
1. Prerequisites (Linux/Mac/Windows)
2. Building the C Streamer
3. Building with Docker
4. Cross-compilation details
5. Troubleshooting build issues

### 2.3 Create docs/USAGE.md

**Consolidate from:**
- QUICKSTART.md
- WINDOWS_QUICKSTART.md
- README.md (usage sections)
- README_C_VERSION.md (usage sections)

**Sections:**
1. Deployment to Pluto
2. Running the Streamer
3. Configuring from Host PC
4. Running Receivers
5. Common Use Cases
6. Troubleshooting runtime issues

### 2.4 Create docs/DEVELOPMENT.md

**Consolidate from:**
- E2E_TEST_README.md
- NOTES.md
- BUGFIX_CONTEXT_PACKETS.md

**Sections:**
1. Architecture Overview
2. VITA49 Packet Format
3. Testing Strategy
4. Running Tests
5. Known Issues & Workarounds
6. Contributing Guidelines

### 2.5 Create docs/ARCHITECTURE.md

**New document for technical details:**
- System architecture diagrams
- Component interactions
- Network protocols
- Thread safety considerations

### Files to Archive (move to docs/archive/):
- BUGFIX_CONTEXT_PACKETS.md → docs/archive/bugfix-context-packets.md
- NOTES.md → docs/archive/development-notes.md

### Files to Delete:
None yet - keep originals until new docs are verified

### Git Commit:
```
Phase 2: Consolidate documentation

- Created comprehensive docs/BUILD.md
- Created comprehensive docs/USAGE.md
- Created comprehensive docs/DEVELOPMENT.md
- Created docs/ARCHITECTURE.md
- Archived historical notes to docs/archive/
- Updated main README.md with clear structure
```

**Files Created:** 5 markdown files
**Files Modified:** 1 (README.md)
**Files Moved:** 2 (to archive)

---

## Phase 3: Reorganize Source Code

**Objective:** Move Python library code into proper package structure.

### 3.1 Move Core VITA49 Library

**From root → To src/vita49/:**
```
vita49_packets.py          → src/vita49/packets.py
vita49_stream_server.py    → src/vita49/stream_server.py
vita49_config_client.py    → src/vita49/config_client.py
```

**Update src/vita49/__init__.py:**
```python
"""
VITA49 Python Library for Pluto SDR Streaming

This library provides VITA49 packet encoding/decoding, streaming server,
and configuration client for use with ADALM-Pluto SDR.
"""

__version__ = "1.0.0"

from .packets import (
    VRTPacket,
    VRTDataPacket,
    VRTContextPacket,
    # ... other exports
)
from .stream_server import VITA49StreamClient
from .config_client import ConfigClient

__all__ = [
    'VRTPacket',
    'VRTDataPacket',
    'VRTContextPacket',
    'VITA49StreamClient',
    'ConfigClient',
]
```

### 3.2 Move Pluto Streamers

**From root → To src/streamers/:**
```
pluto_vita49_standalone.py → src/streamers/standalone.py
vita49_embedded.py         → src/streamers/embedded.py (deprecated)
```

**Update imports** in both files:
```python
# Old:
from vita49_packets import VRTDataPacket, VRTContextPacket

# New:
from vita49.packets import VRTDataPacket, VRTContextPacket
```

### 3.3 Move C Streamer

**From root → To src/:**
```
pluto_vita49_streamer.c → src/pluto_vita49_streamer.c
```

**Update Makefile** to reference new path:
```makefile
SRC_DIR = src
C_SOURCE = $(SRC_DIR)/pluto_vita49_streamer.c
```

### Git Commit:
```
Phase 3: Reorganize source code into packages

- Moved VITA49 Python library to src/vita49/
- Moved Pluto streamers to src/streamers/
- Moved C streamer to src/
- Updated all import statements
- Updated Makefile with new paths
- Created proper __init__.py for library exports
```

**Files Created:** 1 (__init__.py)
**Files Modified:** ~5 (imports, Makefile)
**Files Moved:** 6

---

## Phase 4: Reorganize Examples and Tools

**Objective:** Separate example code from core library.

### 4.1 Move to examples/

```
signal_processing_harness.py → examples/signal_processing_harness.py
example_parallel_receivers.py → examples/parallel_receivers.py
```

### 4.2 Move/Archive Additional Tools

**Decision needed on these files:**

Option A - Move to examples/:
```
vita49_packet_inspector.py → examples/packet_inspector.py
vita49_nats_bridge.py      → examples/nats_bridge.py
```

Option B - Archive (if not needed for basic usage):
```
vita49_packet_inspector.py → tools/packet_inspector.py
vita49_nats_bridge.py      → tools/nats_bridge.py
```

### 4.3 Update Imports in Examples

All example files need updated imports:
```python
# Old:
from vita49_stream_server import VITA49StreamClient
from vita49_packets import VRTDataPacket

# New:
from vita49.stream_server import VITA49StreamClient
from vita49.packets import VRTDataPacket
```

### Git Commit:
```
Phase 4: Organize examples and tools

- Moved example scripts to examples/
- Moved utility tools to tools/ (or examples/)
- Updated all imports in example files
```

**Files Modified:** 4-6 (import updates)
**Files Moved:** 4

---

## Phase 5: Reorganize Tests

**Objective:** Clean test structure with clear organization.

### 5.1 Move Unit Tests

```
test_vita49.py           → tests/test_vita49.py
test_pluto_config.py     → tests/test_pluto_config.py
test_streaming_simple.py → tests/test_streaming_simple.py
```

### 5.2 Move E2E Tests

```
test_e2e_full_pipeline.py             → tests/e2e/test_full_pipeline.py
test_e2e_step1_receive_from_pluto.py  → tests/e2e/test_receive_from_pluto.py
test_e2e_step2_vita49_restreamer.py   → tests/e2e/test_vita49_restreamer.py
test_e2e_step3_plotting_receiver.py   → tests/e2e/test_plotting_receiver.py
```

### 5.3 Create Test Configuration

**Create tests/__init__.py:**
```python
"""Test suite for VITA49 Pluto"""
```

**Create tests/conftest.py (pytest configuration):**
```python
"""Pytest configuration and shared fixtures"""
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
```

### 5.4 Update Test Imports

All test files need updated imports:
```python
# Old:
from vita49_packets import VRTDataPacket
from vita49_stream_server import VITA49StreamClient

# New:
from vita49.packets import VRTDataPacket
from vita49.stream_server import VITA49StreamClient
```

### 5.5 Create Test Runner Script

**Create scripts/run_tests.sh:**
```bash
#!/bin/bash
# Run all tests
cd "$(dirname "$0")/.."
python -m pytest tests/ -v
```

**Create scripts/run_tests.bat:**
```batch
@echo off
REM Run all tests
cd /d "%~dp0\.."
python -m pytest tests/ -v
```

### Git Commit:
```
Phase 5: Reorganize test suite

- Moved unit tests to tests/
- Moved E2E tests to tests/e2e/
- Created pytest configuration
- Updated test imports
- Added test runner scripts
```

**Files Created:** 4 (conftest.py, test runners)
**Files Modified:** 9 (test import updates)
**Files Moved:** 9

---

## Phase 6: Organize Scripts and Build Files

**Objective:** Clean up deployment and build infrastructure.

### 6.1 Move Deployment Scripts

```
deploy_to_pluto.sh  → scripts/deploy_to_pluto.sh
deploy_to_pluto.bat → scripts/deploy_to_pluto.bat
build-with-docker.sh → scripts/build-with-docker.sh
build-with-docker.bat → scripts/build-with-docker.bat
```

### 6.2 Update Script Paths

Each script needs path updates to reference new file locations:

**deploy_to_pluto.sh:**
```bash
# Old:
SCP_FILES="pluto_vita49_standalone.py"

# New:
SCP_FILES="../src/streamers/standalone.py"
# Or for C version:
SCP_FILES="../vita49_streamer"  # built binary in root
```

### 6.3 Move Docker Files

```
Dockerfile → docker/Dockerfile
```

**Update docker/Dockerfile paths:**
```dockerfile
# Update WORKDIR and COPY paths as needed
WORKDIR /build
COPY src/ ./src/
COPY Makefile ./
```

### 6.4 Move Systemd Service

```
vita49.service → systemd/vita49.service
```

**Update service file paths if needed:**
```ini
[Service]
ExecStart=/root/vita49_streamer
# Paths should reference where files will be on Pluto
```

### 6.5 Update Makefile

**Update targets to use new script locations:**
```makefile
deploy: cross
	@scripts/deploy_to_pluto.sh $(PLUTO_IP)
```

### Git Commit:
```
Phase 6: Organize build and deployment scripts

- Moved deployment scripts to scripts/
- Moved Docker configuration to docker/
- Moved systemd service to systemd/
- Updated all script paths and references
- Updated Makefile for new structure
```

**Files Modified:** 6 (path updates)
**Files Moved:** 6

---

## Phase 7: Add Missing Essential Files

**Objective:** Add professional project files.

### 7.1 Create LICENSE

**Create LICENSE (MIT):**
```
MIT License

Copyright (c) 2025 [Your Name/Organization]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

[... full MIT license text ...]
```

### 7.2 Create requirements.txt

**For host PC Python dependencies:**
```txt
# VITA49 Pluto Host Requirements
numpy>=1.20.0
matplotlib>=3.3.0  # For plotting examples
pyadi-iio>=0.0.14  # If running Python streamer from host
```

### 7.3 Create requirements-pluto.txt

**For Pluto device (minimal):**
```txt
# Pluto SDR Requirements (minimal)
pyadi-iio>=0.0.14
# Note: numpy only needed for embedded.py, not standalone.py
```

### 7.4 Create pyproject.toml

**Modern Python packaging:**
```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "vita49-pluto"
version = "1.0.0"
description = "VITA49 streaming for ADALM-Pluto SDR"
readme = "README.md"
license = {text = "MIT"}
authors = [
    {name = "Your Name", email = "your.email@example.com"}
]
requires-python = ">=3.7"
dependencies = [
    "numpy>=1.20.0",
    "pyadi-iio>=0.0.14",
]

[project.optional-dependencies]
examples = [
    "matplotlib>=3.3.0",
]
dev = [
    "pytest>=6.0",
    "pytest-cov>=2.0",
]

[project.urls]
Homepage = "https://github.com/yourusername/vita49-pluto"
Documentation = "https://github.com/yourusername/vita49-pluto/tree/main/docs"
Repository = "https://github.com/yourusername/vita49-pluto"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
```

### 7.5 Create .editorconfig

**For consistent formatting:**
```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true

[*.{py,c,h}]
indent_style = space
indent_size = 4

[*.{yml,yaml,json}]
indent_style = space
indent_size = 2

[Makefile]
indent_style = tab

[*.md]
trim_trailing_whitespace = false
```

### 7.6 Create CONTRIBUTING.md

```markdown
# Contributing to VITA49 Pluto

## Development Setup

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Run tests: `scripts/run_tests.sh`

## Running Tests

See docs/DEVELOPMENT.md for testing guide

## Code Style

- Python: Follow PEP 8
- C: Follow Linux kernel style
- Use .editorconfig settings

## Pull Request Process

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit PR with clear description
```

### Git Commit:
```
Phase 7: Add essential project files

- Added LICENSE (MIT)
- Added requirements.txt for host dependencies
- Added requirements-pluto.txt for device dependencies
- Added pyproject.toml for modern Python packaging
- Added .editorconfig for consistent formatting
- Added CONTRIBUTING.md for contributors
```

**Files Created:** 6
**Files Modified:** 0

---

## Phase 8: Update .gitignore

**Objective:** Ensure proper files are ignored.

### Update .gitignore:
```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
dist/
build/
.eggs/
*.egg

# Testing
.pytest_cache/
.coverage
htmlcov/
.tox/
lastfailed
nodeids

# IDE
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store

# Build artifacts
vita49_streamer
*.o
*.a
*.exe
*.dll
*.so
*.dylib

# OS
.DS_Store
Thumbs.db
ehthumbs.db
Desktop.ini

# Temporary files
*.tmp
*.bak
*.log
nul

# Mount points and temporary directories
mnt/
tmp/

# Cache
CACHEDIR.TAG
*.cache

# Virtual environments
venv/
env/
ENV/
.venv
```

### Git Commit:
```
Phase 8: Update .gitignore

- Added comprehensive Python ignores
- Added build artifact patterns
- Added IDE and OS-specific files
- Added virtual environment patterns
```

**Files Modified:** 1 (.gitignore)

---

## Phase 9: Clean Up Root Directory

**Objective:** Remove old files and verify everything works.

### 9.1 Files to Delete

**Old documentation (replaced by docs/):**
```
QUICKSTART.md
WINDOWS_QUICKSTART.md
BUILD_AND_DEPLOY.md
WINDOWS_BUILD_GUIDE.md
E2E_TEST_README.md
README_C_VERSION.md
NOTES.md (if archived)
BUGFIX_CONTEXT_PACKETS.md (if archived)
```

### 9.2 Deprecated Source Files

**Only if verified working from new locations:**
```
vita49_packets.py (moved to src/vita49/)
vita49_stream_server.py (moved to src/vita49/)
vita49_config_client.py (moved to src/vita49/)
pluto_vita49_standalone.py (moved to src/streamers/)
vita49_embedded.py (moved to src/streamers/)
signal_processing_harness.py (moved to examples/)
example_parallel_receivers.py (moved to examples/)
# All test_*.py files (moved to tests/)
```

### 9.3 Old Build Files

```
setup.py (replaced by pyproject.toml)
```

### 9.4 Verification Steps Before Deletion

**Run this checklist:**
- [ ] All imports updated and working
- [ ] Tests pass from new locations
- [ ] Build system works with new paths
- [ ] Examples run successfully
- [ ] Documentation is complete

### Git Commit:
```
Phase 9: Remove old files after migration

- Deleted old documentation files (replaced by docs/)
- Deleted old source files (moved to src/)
- Deleted deprecated files
- Verified all functionality preserved
```

**Files Deleted:** ~25
**Files Modified:** 0

---

## Phase 10: Final Verification and Polish

**Objective:** Ensure everything works perfectly.

### 10.1 Test Matrix

**Build Tests:**
```bash
# Test C build
make clean
make cross

# Test Docker build
scripts/build-with-docker.sh

# Test Python package
pip install -e .
```

**Deployment Tests:**
```bash
# Test deployment script
scripts/deploy_to_pluto.sh pluto.local

# Verify on Pluto
ssh root@pluto.local './vita49_streamer'
```

**Python Tests:**
```bash
# Run all tests
scripts/run_tests.sh

# Test imports
python -c "from vita49.packets import VRTDataPacket"
python -c "from vita49.stream_server import VITA49StreamClient"
```

**Example Tests:**
```bash
# Run examples
python examples/signal_processing_harness.py
python examples/parallel_receivers.py
```

### 10.2 Documentation Review

- [ ] README.md is clear and complete
- [ ] docs/BUILD.md has all build instructions
- [ ] docs/USAGE.md has all usage examples
- [ ] docs/DEVELOPMENT.md has architecture details
- [ ] All links work (internal and external)
- [ ] Code examples in docs are accurate

### 10.3 Final Polish

**Update README.md badges (optional):**
```markdown
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-blue)
```

**Update version numbers consistently:**
- pyproject.toml
- src/vita49/__init__.py
- Documentation references

### Git Commit:
```
Phase 10: Final verification and polish

- Verified all tests pass
- Verified build system works
- Verified examples run
- Updated version numbers
- Added badges to README
- Final documentation review
```

**Files Modified:** 2-3 (README, version bumps)

---

## Phase 11: Create GitHub Release Prep

**Objective:** Prepare for first GitHub release.

### 11.1 Create CHANGELOG.md

```markdown
# Changelog

All notable changes to this project will be documented in this file.

## [1.0.0] - 2025-12-21

### Added
- Initial release of VITA49 Pluto Streamer
- C implementation for minimal footprint
- Python library for host-side processing
- Comprehensive documentation
- End-to-end test suite
- Cross-platform build support (Linux/Mac/Windows)
- Docker-based build environment
- Example receivers and signal processing

### Changed
- Reorganized repository structure for clarity
- Consolidated documentation
- Modernized Python packaging

### Migration
- This release represents a major cleanup from development workspace
- See MIGRATION_PLAN.md for details of reorganization
```

### 11.2 Create Release Checklist

**docs/RELEASE_CHECKLIST.md:**
```markdown
# Release Checklist

Before pushing to GitHub:

## Code Quality
- [ ] All tests pass
- [ ] No TODO/FIXME in critical paths
- [ ] Code follows style guidelines
- [ ] No hardcoded credentials or secrets

## Documentation
- [ ] README is accurate and complete
- [ ] All docs are up to date
- [ ] Examples work as documented
- [ ] Links are not broken

## Build System
- [ ] Makefile works on all platforms
- [ ] Docker build succeeds
- [ ] Python package installs cleanly
- [ ] Scripts have correct permissions

## Legal/Attribution
- [ ] LICENSE file present
- [ ] Copyright notices correct
- [ ] Third-party licenses acknowledged
- [ ] No proprietary code included

## Repository
- [ ] .gitignore is complete
- [ ] No sensitive files committed
- [ ] Clean commit history
- [ ] Reasonable commit messages
```

### Git Commit:
```
Phase 11: Prepare for initial release

- Created CHANGELOG.md
- Created release checklist
- Repository ready for GitHub publication
```

**Files Created:** 2

---

## Summary of Changes

### Directory Structure Transformation

**Before (35 files in root):**
```
vita49-pluto/
├── [35 files scattered in root]
└── __pycache__/
```

**After (organized structure):**
```
vita49-pluto/
├── README.md
├── LICENSE
├── Makefile
├── pyproject.toml
├── requirements.txt
├── requirements-pluto.txt
├── CHANGELOG.md
├── CONTRIBUTING.md
├── .gitignore
├── .editorconfig
├── src/
│   ├── pluto_vita49_streamer.c
│   ├── vita49/
│   │   ├── __init__.py
│   │   ├── packets.py
│   │   ├── stream_server.py
│   │   └── config_client.py
│   └── streamers/
│       ├── standalone.py
│       └── embedded.py
├── examples/
│   ├── signal_processing_harness.py
│   ├── parallel_receivers.py
│   └── [other examples]
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_vita49.py
│   ├── test_pluto_config.py
│   ├── test_streaming_simple.py
│   └── e2e/
│       ├── test_full_pipeline.py
│       ├── test_receive_from_pluto.py
│       ├── test_vita49_restreamer.py
│       └── test_plotting_receiver.py
├── scripts/
│   ├── deploy_to_pluto.sh
│   ├── deploy_to_pluto.bat
│   ├── build-with-docker.sh
│   ├── build-with-docker.bat
│   ├── run_tests.sh
│   └── run_tests.bat
├── docker/
│   └── Dockerfile
├── systemd/
│   └── vita49.service
└── docs/
    ├── BUILD.md
    ├── USAGE.md
    ├── DEVELOPMENT.md
    ├── ARCHITECTURE.md
    ├── RELEASE_CHECKLIST.md
    └── archive/
        ├── bugfix-context-packets.md
        └── development-notes.md
```

### File Count Summary

| Category | Before | After |
|----------|--------|-------|
| Root files | 35 | 9 |
| Source files | 15 (root) | 7 (organized) |
| Test files | 9 (root) | 9 (tests/) |
| Docs | 8 (root) | 5 (docs/) |
| Scripts | 4 (root) | 6 (scripts/) |
| **Total files** | **~50** | **~55** |

### Git Commits

Total planned commits: **11 phases**

Each commit is atomic and reversible, allowing rollback at any point.

---

## Execution Strategy

### Option A: Automated (Fast)
Run all phases in sequence, one commit per phase.
**Time:** ~30 minutes
**Risk:** Lower (everything tested)

### Option B: Incremental (Safe)
Execute 1-2 phases per session, verify thoroughly.
**Time:** Multiple sessions
**Risk:** Lowest (manual verification at each step)

### Option C: Custom
Pick specific phases based on priority:
- **Quick cleanup:** Phases 2, 9 (docs + deletion)
- **Functional:** Phases 3, 5 (code + tests)
- **Professional:** Phases 7, 11 (project files + release)

---

## Rollback Plan

At any point, you can:

```bash
# See all migration commits
git log --oneline

# Rollback to specific phase
git reset --hard <commit-hash>

# Or rollback one phase
git reset --hard HEAD~1

# Return to baseline
git reset --hard 6126631
```

---

## Post-Migration Checklist

After completing all phases:

- [ ] Run full test suite
- [ ] Build C streamer successfully
- [ ] Deploy to Pluto and verify streaming
- [ ] Run all examples
- [ ] Review all documentation
- [ ] Check all links in docs
- [ ] Verify Python package installs
- [ ] Create GitHub repository
- [ ] Push to GitHub
- [ ] Create v1.0.0 release tag

---

## Questions to Answer Before Starting

1. **Scope:** Execute all phases or specific phases?
2. **Timing:** All at once or incrementally?
3. **Verification:** Test after each phase or at end?
4. **Deprecated files:** Archive or delete? (vita49_embedded.py, NOTES.md, etc.)
5. **Tools directory:** Create tools/ for utilities or keep in examples/?
6. **Author info:** What should go in LICENSE and pyproject.toml?

---

## Next Steps

Reply with:
- **"Execute all"** - Run all 11 phases automatically
- **"Phase by phase"** - Do one phase at a time with approval
- **"Custom plan"** - Specify which phases you want
- **"Questions first"** - Answer the questions above before starting

I'm ready to execute the migration whenever you are!
