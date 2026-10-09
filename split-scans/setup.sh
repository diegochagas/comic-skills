#!/usr/bin/env bash
# split-scans setup (also run by <repo>/setup.sh): installs ./node_modules
# (ag-psd + canvas, for PSD inputs). Python deps (opencv, numpy) come from the
# shared <repo>/venv; the gutter inpainting reuses manga-translator-ptbr's LaMa
# model (that skill's setup.sh downloads it; without it the core is filled white).
set -euo pipefail
cd "$(dirname "$0")"
if command -v npm >/dev/null 2>&1; then
    npm install --silent
    echo "node_modules ready: ag-psd $(node -p "require('./node_modules/ag-psd/package.json').version")"
else
    echo "WARNING: npm not found - PSD inputs need node + ag-psd (images work without)."
fi
