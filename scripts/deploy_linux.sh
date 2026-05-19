#!/bin/bash
# Proxion Sovereign Bootstrapper for Linux
# ========================================
# This script prepares the Linux environment for native Proxion deployment.

set -e

echo "=== Proxion Sovereign Migration: Linux Bootstrapper ==="

# 1. Dependency Check
echo "[1/4] Checking Dependencies..."
MISSING_DEPS=()

if ! command -v docker &> /dev/null; then
    MISSING_DEPS+=("docker")
fi

if ! docker compose version &> /dev/null && ! command -v docker-compose &> /dev/null; then
    MISSING_DEPS+=("docker-compose")
fi

if ! command -v python3 &> /dev/null; then
    MISSING_DEPS+=("python3")
fi

if [ ${#MISSING_DEPS[@]} -ne 0 ]; then
    echo "ERROR: Missing required dependencies: ${MISSING_DEPS[*]}"
    echo "Please install them using your package manager (e.g., sudo apt install ${MISSING_DEPS[*]})"
    exit 1
fi
echo "OK: All core dependencies found."

# 2. Python Environment Setup
echo "[2/4] Setting up Python dependencies..."
# Check for fusepy and guessit
pip3 install --quiet fusepy guessit pycryptodome requests || {
    echo "WARNING: Failed to install Python dependencies via pip. Ensure pip3 is installed."
}

# 3. Environment Abstraction (.env)
echo "[3/4] Initializing Environment..."
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "CREATED: .env from .env.example. PLEASE EDIT .env TO SET YOUR PATHS."
    else
        echo "WARNING: .env.example not found. Cannot initialize .env automatically."
    fi
else
    echo "OK: .env already exists."
fi

# 4. FUSE Setup
echo "[4/4] Verifying FUSE permissions..."
if [ -c /dev/fuse ]; then
    if [ ! -r /dev/fuse ] || [ ! -w /dev/fuse ]; then
        echo "WARNING: /dev/fuse is not readable/writable by current user."
        echo "Suggestion: sudo usermod -aG fuse $USER (or equivalent for your distro)"
    else
        echo "OK: FUSE device access verified."
    fi
else
    echo "WARNING: /dev/fuse not found. Ensure FUSE is enabled in your kernel."
fi

echo ""
echo "=== Setup Complete ==="
echo "Next Steps:"
echo "1. Edit the .env file to match your Linux mount points (e.g., /mnt/stash, /mnt/media)."
echo "2. Run 'python3 proxion-fuse/mount.py P:' to mount the sovereign bridge."
echo "3. Run 'python3 proxion-keyring/manager.py' to start the orchestration layer."
echo ""
echo "Welcome to the Sovereign Frontier."
