#!/usr/bin/env bash
# restore-photos (and modernize-photos, which reuses its scripts). Safe to
# re-run. The Python deps (opencv-python-headless, numpy, pillow) are in the
# shared venv made by the root setup.sh. This:
#   1. checks exiftool (keeps EXIF on re-saved files);
#   2. writes the empty settings files in ~/.config/photo-restore/ the
#      scripts read (the folder keeps the name of the repo these skills
#      came from, so existing settings stay where they are);
#   3. checks the local ComfyUI (FLUX.2 klein / Qwen-Image-Edit), installed
#      by local-ai-setup (github.com/diegochagas/local-ai-setup).
set -euo pipefail
cd "$(dirname "$0")/.."

command -v exiftool >/dev/null || echo "WARN: exiftool not found (apt install libimage-exiftool-perl) - EXIF will not be copied to results"

CONF="$HOME/.config/photo-restore/comfyui.env"
if [ ! -f "$CONF" ]; then
    mkdir -p "$(dirname "$CONF")"
    cat > "$CONF" <<'CFG'
# Local ComfyUI server used by restore-photos (AI inpainting). Both optional.
COMFYUI_URL=
COMFYUI_SERVICE=
CFG
    echo "Wrote $CONF - fill in COMFYUI_URL (default http://127.0.0.1:8188) and COMFYUI_SERVICE (systemd --user unit) if you have them."
fi
CCONF="$HOME/.config/photo-restore/compare.env"
if [ ! -f "$CCONF" ]; then
    cat > "$CCONF" <<'CFG'
# restore-photos review page (restore-photos/scripts/compare_server.py).
# Set before each review; --results / --originals override them.
COMPARE_RESULTS=
COMPARE_ORIGINALS=
COMPARE_PORT=8790
COMPARE_ALT=Higgsfield,Higgsfield 2
CFG
    echo "Wrote $CCONF - set COMPARE_RESULTS / COMPARE_ORIGINALS before a review."
fi
if [ -x venv/bin/python ]; then
    venv/bin/python restore-photos/scripts/comfy_client.py --check 2>/dev/null || true
fi
