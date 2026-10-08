#!/usr/bin/env bash
# manga-translator-ptbr setup (also run by <repo>/setup.sh): downloads the two
# ONNX models into ./models, installs ./node_modules (ag-psd, canvas, pngjs)
# and checks for GIMPhoto (flatpak GIMP 3), needed only for XCF output. Python deps come
# from the shared <repo>/venv (created by <repo>/setup.sh). Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

MODELS_DIR="models"
mkdir -p "$MODELS_DIR"

# comic-text-detector (text strokes + text blocks), from manga-image-translator
MODEL_URL="https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/comictextdetector.pt.onnx"
MODEL_PATH="$MODELS_DIR/comictextdetector.pt.onnx"
if [ ! -f "$MODEL_PATH" ]; then
    echo "Downloading text-detection model (~95 MB)..."
    curl -L --fail -o "$MODEL_PATH" "$MODEL_URL"
fi
echo "model ready: $MODEL_PATH"

# LaMa inpainting model ("generative fill" for text erased over art; solid
# backgrounds never use it). ONNX export, Apache-2.0, runs on CPU. Optional:
# without it inpaint_lama.py falls back to OpenCV Telea.
LAMA_URL="https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
LAMA_PATH="$MODELS_DIR/lama_fp32.onnx"
if [ ! -f "$LAMA_PATH" ]; then
    echo "Downloading LaMa inpainting model (~208 MB)..."
    curl -L --fail -o "$LAMA_PATH" "$LAMA_URL"
fi
echo "inpainting model ready: $LAMA_PATH"

if command -v npm >/dev/null 2>&1; then
    npm install --silent
    echo "node_modules ready: ag-psd $(node -p "require('./node_modules/ag-psd/package.json').version")"
else
    echo "WARNING: npm not found - install Node.js; every PSD is written by the .mjs scripts (ag-psd)."
fi

if ! flatpak info io.github.diegochagas.GIMPhoto >/dev/null 2>&1; then
    echo "WARNING: GIMPhoto (io.github.diegochagas.GIMPhoto, GIMP 3) not found - only needed for XCF output (build_translated_xcf.py); PSDs work without it."
    echo "  curl -fLO https://github.com/diegochagas/gimphoto/releases/latest/download/GIMPhoto.flatpak && flatpak install --user GIMPhoto.flatpak"
fi
