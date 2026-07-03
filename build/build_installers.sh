#!/usr/bin/env bash
# Build script for USB Relay IP installers
# Usage: bash build/build_installers.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

USBIPD_DIR="usbipd-install"
USBIPD_WIN_MSI="$USBIPD_DIR/usbipd-win_5.3.0_x64.msi"
USBIP_WIN_EXE="$USBIPD_DIR/USBip-0.9.7.7-x64.exe"

mkdir -p "$USBIPD_DIR"

if [ ! -f "$USBIPD_WIN_MSI" ]; then
  echo "=== Download usbipd-win MSI ==="
  curl -L --fail -o "$USBIPD_WIN_MSI" "https://github.com/dorssel/usbipd-win/releases/download/v5.3.0/usbipd-win_5.3.0_x64.msi"
fi

if [ ! -f "$USBIP_WIN_EXE" ]; then
  echo "=== Download USBip VHCI installer ==="
  curl -L --fail -o "$USBIP_WIN_EXE" "https://github.com/vadimgrn/usbip-win2/releases/download/v.0.9.7.7/USBip-0.9.7.7-x64.exe"
fi

echo "=== Step 1: Clean previous builds ==="
rm -rf dist/ build/USBRelayHost build/USBRelayClient

echo "=== Step 2: Install dependencies ==="
pip install -r requirements-client.txt -r requirements-host.txt
pip install pyinstaller

echo "=== Step 3: Build Host (PyInstaller, icon embedded) ==="
python -m PyInstaller --noconfirm --clean --onedir --noconsole --uac-admin --name USBRelayHost \
  --icon "host/assets/icon.ico" \
  --add-data "host/assets/icon.ico;assets" \
  --add-data "host/assets/icon_connected.ico;assets" \
  --add-data "usbipd-install;usbipd-install" \
  --hidden-import fastapi --hidden-import uvicorn --hidden-import pydantic --hidden-import PyQt6 \
  host/main.py

echo "=== Step 4: Build Client (PyInstaller, icon embedded) ==="
python -m PyInstaller --noconfirm --clean --onedir --noconsole --name USBRelayClient \
  --icon "client/assets/icon.ico" \
  --add-data "client/assets/icon.ico;assets" \
  --add-data "client/assets/icon_connected.ico;assets" \
  --add-data "usbipd-install;usbipd-install" \
  --hidden-import httpx --hidden-import pydantic --hidden-import PyQt6 \
  client/main.py

echo "=== Step 5: Build NSIS installers ==="
"/c/Program Files (x86)/NSIS/makensis.exe" "build/installer_host.nsi"
"/c/Program Files (x86)/NSIS/makensis.exe" "build/installer_client.nsi"

echo "=== Done! Installers gerados ==="
ls -lh dist/*.exe
