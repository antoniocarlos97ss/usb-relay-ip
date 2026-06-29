#!/usr/bin/env bash
# Build script for USB Relay IP installers
# Usage: bash build/build_installers.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Step 1: Clean previous builds ==="
rm -rf dist/ build/*.spec USBRelayClient.spec USBRelayHost.spec

echo "=== Step 2: Install dependencies ==="
pip install -r requirements-client.txt -r requirements-host.txt

echo "=== Step 3: Download rcedit (icon fixer) ==="
RCEDIT="rcedit.exe"
if [ ! -f "$RCEDIT" ]; then
    curl -sL -o "$RCEDIT" "https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe"
fi

echo "=== Step 4: Build Host (PyInstaller) ==="
python -m PyInstaller --noconfirm --onedir --windowed --uac-admin \
    --name USBRelayHost \
    --icon "host/assets/icon.ico" \
    --add-data "host/assets;assets" \
    --add-data "usbipd-install;usbipd-install" \
    --collect-all "uvicorn" --collect-all "fastapi" \
    --hidden-import "anyio" --hidden-import "anyio._backends._asyncio" \
    --hidden-import "PyQt6.QtCore" --hidden-import "PyQt6.QtGui" --hidden-import "PyQt6.QtWidgets" \
    "host/main.py"

echo "=== Step 5: Build Client (PyInstaller) ==="
python -m PyInstaller --noconfirm --onedir --windowed --uac-admin \
    --name USBRelayClient \
    --icon "client/assets/icon.ico" \
    --add-data "client/assets;assets" \
    --add-data "usbipd-install;usbipd-install" \
    --hidden-import "PyQt6.QtCore" --hidden-import "PyQt6.QtGui" --hidden-import "PyQt6.QtWidgets" \
    --hidden-import "httpx" --hidden-import "pydantic" \
    "client/main.py"

echo "=== Step 6: Fix icons with rcedit ==="
"$RCEDIT" "dist/USBRelayHost/USBRelayHost.exe" --set-icon "host/assets/icon.ico"
"$RCEDIT" "dist/USBRelayClient/USBRelayClient.exe" --set-icon "client/assets/icon.ico"

echo "=== Step 7: Build NSIS installers ==="
"/c/Program Files (x86)/NSIS/makensis.exe" "build/installer_host.nsi"
"/c/Program Files (x86)/NSIS/makensis.exe" "build/installer_client.nsi"

echo "=== Done! ==="
ls -lh dist/*.exe
