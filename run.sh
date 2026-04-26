#!/bin/bash
# Launch the NAS Cache Installer
# Requires Python 3 and tkinter (python-tkinter / tk package)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "tkinter not found. Installing..."
    sudo pacman -S --noconfirm tk 2>/dev/null || \
    sudo apt-get install -y python3-tk 2>/dev/null || \
    echo "Please install python3-tkinter manually for your distro."
fi

python3 "$SCRIPT_DIR/installer.py"
