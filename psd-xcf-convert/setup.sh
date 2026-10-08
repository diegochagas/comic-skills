#!/usr/bin/env bash
# psd-xcf-convert setup (also run by <repo>/setup.sh): installs ./node_modules
# (ag-psd) and checks for GIMPhoto (flatpak GIMP 3) and fontconfig, both required - GIMP
# reads/writes the XCF side, fc-list maps Photoshop font names to GIMP's.
# No Python deps beyond the standard library. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

if command -v npm >/dev/null 2>&1; then
    npm install --silent
    echo "node_modules ready: ag-psd $(node -p "require('./node_modules/ag-psd/package.json').version")"
else
    echo "WARNING: npm not found - install Node.js; the PSD side is read and written by the .mjs scripts (ag-psd)."
fi

if ! flatpak info io.github.diegochagas.GIMPhoto >/dev/null 2>&1; then
    echo "WARNING: GIMPhoto (io.github.diegochagas.GIMPhoto, GIMP 3) not found - required (or set GIMP_CMD to another GIMP 3 launcher)."
    echo "  curl -fLO https://github.com/diegochagas/gimphoto/releases/latest/download/GIMPhoto.flatpak && flatpak install --user GIMPhoto.flatpak"
fi
command -v fc-list >/dev/null 2>&1 || echo "WARNING: fc-list (fontconfig) not found - fonts cannot be matched between the two formats."
