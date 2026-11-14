#!/usr/bin/env python3
"""GIMPhoto's Generative Fill, callable from the skills: a selection on a
picture is repainted by the local Qwen-Image-Edit model in ComfyUI.

It runs the very graph GIMPhoto builds (gimphoto_comfyui_client.py, copied
from that project): the model samples only inside the selection (latent
noise mask) and sees either the original pixels there ("see through": text
or marks to take away) or the area pre-filled from its surroundings
("hidden": something to draw from nothing).

    import qwen_fill
    if qwen_fill.available():
        out = qwen_fill.fill(crop_bgr, selection_u8, qwen_fill.ERASE_PROMPT)

Server: COMFYUI_URL (default http://127.0.0.1:8188). When it does not answer,
the systemd --user unit COMFYUI_SERVICE (default "comfyui", the one
local-ai-setup installs) is started, and stopped again when this process
ends. Models: the ones GIMPhoto uses (Qwen-Image-Edit + its text encoder,
VAE and Lightning 4-step LoRA), found by name on the server.

Standalone (debug): qwen_fill.py <image> <selection.png> <out.png> [erase|fold]
"""
import atexit
import os
import subprocess
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gimphoto_comfyui_client as gimphoto  # noqa: E402

MODEL = "qwen"
# Measured on Soul of Gold pages (2026-11-13): GIMPhoto's short "erase text"
# redrew the lettering in 4 of 4 runs on a sound effect over hatching; this
# wording on a crop with context erased it in 6 of 6.
ERASE_PROMPT = (
    "Erase the text: remove all the lettering, speech text and sound-effect "
    "characters from the image and continue what is behind them - the drawing, "
    "its lines, screentone and shading, or the plain texture of the paper or "
    "cover. Do not write any new text, "
    "letters or symbols. Keep everything else unchanged.")
FOLD_PROMPT = (
    "This picture is one drawing printed across two pages of a book. Remove "
    "the vertical fold line and its shadow in the middle of the image: the "
    "drawing continues straight across it, every line, shape, screentone and "
    "shading joined seamlessly, with the same tones as on both sides. Do not "
    "add anything new. No text. Keep everything else unchanged.")
MASK_GROW = 6        # px (at the model's size) the paintable zone exceeds the selection
START_WAIT = 120     # s to wait for a service this module started

_state = {"ok": None, "started": None}


def _reachable(url):
    try:
        gimphoto._request(url + "/system_stats", timeout=5, _retry=False)
        return True
    except gimphoto.ComfyUIError:
        return False


def _stop_service():
    if _state["started"]:
        subprocess.run(["systemctl", "--user", "stop", _state["started"]], check=False)
        _state["started"] = None


def _start_service(url):
    """Start the ComfyUI unit when the server is down; True once it answers."""
    unit = os.environ.get("COMFYUI_SERVICE", "").strip() or gimphoto.SERVICE
    if subprocess.run(["systemctl", "--user", "cat", unit], capture_output=True).returncode:
        return False
    print(f"qwen_fill: starting systemd --user unit {unit} ...", file=sys.stderr)
    subprocess.run(["systemctl", "--user", "start", unit], check=False)
    _state["started"] = unit
    atexit.register(_stop_service)
    deadline = time.time() + START_WAIT
    while time.time() < deadline:
        if _reachable(url):
            return True
        time.sleep(2)
    return False


def available():
    """ComfyUI answers (started when needed) and has the Qwen-Image-Edit
    files. Cached per process; says why not on stderr."""
    if _state["ok"] is not None:
        return _state["ok"]
    url = gimphoto.get_url()
    why = ""
    if not (_reachable(url) or _start_service(url)):
        why = f"ComfyUI not reachable at {url}"
    else:
        try:
            gimphoto._model_nodes(url, MODEL)
        except gimphoto.ComfyUIError as e:
            why = str(e).splitlines()[0]
    if why:
        print(f"qwen_fill: unavailable ({why})", file=sys.stderr)
    _state["ok"] = not why
    return _state["ok"]


def _png(img):
    return cv2.imencode(".png", img)[1].tobytes()


def fill(crop, selection, prompt, see_through=True, progress=None):
    """Repaint `selection` (uint8, >0) of `crop` (BGR) following `prompt`.
    Returns the model's picture at the crop's size; the caller keeps the
    pixels it wants (outside the selection they are the crop's, resampled).
    Raises gimphoto.ComfyUIError when the run fails.
    Example: fill(page[0:900, 0:900], sel, ERASE_PROMPT)"""
    url = gimphoto.get_url()
    h, w = crop.shape[:2]
    work_w, work_h = gimphoto.work_size(w, h)
    image = gimphoto._upload(url, "image.png", _png(crop))
    mask = gimphoto._upload(url, "mask.png", _png((selection > 0).astype(np.uint8) * 255))
    graph = gimphoto._inpaint_graph(url, MODEL, image, mask, prompt, work_w, work_h, w, h,
                                    see_through=see_through, grow=MASK_GROW)
    png = gimphoto._run(url, graph, progress)["out"]
    out = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if out is None or out.shape[:2] != (h, w):
        raise gimphoto.ComfyUIError(f"unexpected result for a {w}x{h} crop: "
                                    f"{None if out is None else out.shape}")
    return out


def blend(crop, gen, selection, feather=2):
    """`crop` with the selection's pixels taken from `gen` (soft edge)."""
    hard = (selection > 0).astype(np.float32)
    k = 2 * feather + 1
    alpha = np.maximum(cv2.GaussianBlur(hard, (k, k), 0), hard)[:, :, None]
    mixed = crop.astype(np.float32) * (1 - alpha) + gen.astype(np.float32) * alpha
    return np.clip(mixed + 0.5, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    if not available():
        sys.exit(1)
    im = cv2.imread(sys.argv[1])
    sel = cv2.imread(sys.argv[2], cv2.IMREAD_GRAYSCALE)
    fold = len(sys.argv) > 4 and sys.argv[4] == "fold"
    t0 = time.time()
    res = fill(im, sel, FOLD_PROMPT if fold else ERASE_PROMPT)
    cv2.imwrite(sys.argv[3], blend(im, res, sel))
    print(f"qwen -> {sys.argv[3]} ({time.time() - t0:.0f}s)")
