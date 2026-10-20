#!/usr/bin/env bash
# generate-comic-page setup: installs the Node deps of build_psd.mjs (ag-psd,
# pngjs) and checks the external tools the skill needs. Safe to re-run.
set -e
cd "$(dirname "$0")"
if command -v npm >/dev/null; then
    npm install --no-fund --no-audit --loglevel=error
    echo "node_modules: ok (ag-psd, pngjs)"
else
    echo "WARNING: npm not found — build_psd.mjs needs Node.js + ag-psd + pngjs"
fi
command -v higgsfield >/dev/null \
    && echo "higgsfield CLI: $(higgsfield --version 2>/dev/null | head -1)" \
    || echo "WARNING: higgsfield CLI not found — npm i -g @higgsfield/cli && higgsfield auth login"
fc-list | grep -qi "CCWildWords" \
    && echo "CCWildWords font: ok" \
    || echo "WARNING: CCWildWords font not installed — balloon text would be measured with DejaVu and written under a guessed font name"
[ -f ../manga-translator-ptbr/models/lama_fp32.onnx ] \
    && echo "LaMa (manga-translator-ptbr/models): ok" \
    || echo "WARNING: manga-translator-ptbr/models/lama_fp32.onnx missing (run manga-translator-ptbr/setup.sh) — layer separation would fall back to cv2.inpaint"
curl -s -m 3 "${COMFYUI_URL:-http://127.0.0.1:8188}/models/birefnet" 2>/dev/null | grep -q General.safetensors \
    && echo "ComfyUI BiRefNet: ok" \
    || echo "NOTE: ComfyUI not running or without models/BiRefNet/General.safetensors — Characters layers need it (build_psd.py --no-characters otherwise)"
