#!/usr/bin/env bash
# Build script for USB Relay IP installers
# Usage: bash build/build_installers.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Step 1: Clean previous builds ==="
rm -rf dist/ build/USBRelayHost build/USBRelayClient

echo "=== Step 2: Install dependencies ==="
pip install -r requirements-client.txt -r requirements-host.txt

echo "=== Step 3: Build Host (PyInstaller, spec com icon embedded) ==="
# IMPORTANTE: o icone e embutido pelo PyInstaller via .spec (campo EXE(icon=...)).
# NAO use rcedit depois — ele corrompe o recurso PYZ (bytecode comprimido)
# do executavel --onedir, causando erro: "Could not load pyInstaller's embedded PYZ archive".
python -m PyInstaller --noconfirm USBRelayHost.spec

echo "=== Step 4: Build Client (PyInstaller, spec com icon embedded) ==="
python -m PyInstaller --noconfirm USBRelayClient.spec

echo "=== Step 5: Build NSIS installers ==="
"/c/Program Files (x86)/NSIS/makensis.exe" "build/installer_host.nsi"
"/c/Program Files (x86)/NSIS/makensis.exe" "build/installer_client.nsi"

echo "=== Done! Installers gerados ==="
ls -lh dist/*.exe
