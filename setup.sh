#!/usr/bin/env bash
# One-time setup for the whole repo. Safe to re-run.
#   1. creates the shared Python venv (<repo>/venv) with the deps of every
#      Python skill - the only piece that lives at the repo root;
#   2. runs <skill>/setup.sh for every skill that has one (models,
#      node_modules, tool checks). Each of those can also be run on its own.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Creating venv..."
    python3 -m venv venv
    venv/bin/pip install --quiet --upgrade pip
fi
# manga-translator-ptbr: onnxruntime opencv numpy pillow | pdf-psd-convert: pymupdf psd-tools pillow
# generate-comic-page, clean-texts: pillow opencv numpy | image-utils, comic-archive: pillow
# describe-pages: pillow (the model comes from describe-pages/setup.sh via ollama) | find-pages: stdlib only
venv/bin/pip install --quiet onnxruntime opencv-python-headless numpy \
    pillow pymupdf "psd-tools[composite]"
echo "venv ready: $(venv/bin/python -c 'import onnxruntime; print("onnxruntime", onnxruntime.__version__)')"

for s in */setup.sh; do
    [ "$s" = "*/setup.sh" ] && break
    echo "== ${s%/setup.sh} =="
    bash "$s"
done

echo "Setup complete."
