#!/usr/bin/env python3
"""ComfyUI client shared by the local backends of generate-comic-page:
anima_panel.py (Anima Turbo draws a panel) and separate_layers.py (BiRefNet
mattes the characters out of a panel). Nothing is written in the ComfyUI
output folder: results come back through PreviewImage (temp/) and /view.

Env: COMFYUI_URL (default http://127.0.0.1:8188), COMFYUI_SERVICE (a systemd
--user unit started when the server is down, e.g. comfyui), COMFYUI_TIMEOUT
(seconds to wait for one workflow, default 600).

Example:
    comfyui.ensure_server()
    name = comfyui.upload_image(png_bytes, "panel.png")
    entry = comfyui.run({"1": comfyui.node("LoadImage", image=name), ...})
    img = comfyui.result_image(entry)
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import cv2
import numpy as np
import requests

URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
SERVICE = os.environ.get("COMFYUI_SERVICE", "")
TIMEOUT = int(os.environ.get("COMFYUI_TIMEOUT", "600"))


def node(cls: str, **inputs) -> dict:
    """One workflow node in ComfyUI's API format."""
    return {"class_type": cls, "inputs": inputs}


def get(path: str, timeout: int = 10) -> bytes:
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return r.read()


def _server_up() -> bool:
    try:
        get("/system_stats", 3)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _start_service() -> bool:
    if not SERVICE:
        return False
    print(f"comfyui: starting systemd --user unit {SERVICE} ...", file=sys.stderr)
    subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
    for _ in range(90):
        time.sleep(2)
        if _server_up():
            return True
    return False


def ensure_server() -> None:
    """Exit with the reason when ComfyUI is not reachable (after one start attempt)."""
    if _server_up() or _start_service():
        return
    sys.exit(f"ERROR ComfyUI not reachable at {URL} — start it with "
             f"systemctl --user start {SERVICE or 'comfyui'} (or set COMFYUI_SERVICE)")


def model_files(kind: str) -> set[str]:
    """Files ComfyUI lists in models/<kind> (e.g. "diffusion_models", "BiRefNet")."""
    return set(json.loads(get(f"/models/{kind}")))


def upload_image(png: bytes, name: str) -> str:
    """Put an image in ComfyUI's input folder (for LoadImage); returns its stored name."""
    r = requests.post(URL + "/upload/image", files={"image": (name, png, "image/png")},
                      data={"overwrite": "true"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"ERROR ComfyUI refused the upload of {name}: {r.text[:300]}")
    return r.json()["name"]


def run(workflow: dict) -> dict:
    """Submit a workflow and wait for its history entry (exits on failure/timeout)."""
    req = urllib.request.Request(URL + "/prompt", json.dumps({"prompt": workflow}).encode(),
                                 {"Content-Type": "application/json"})
    try:
        pid = json.load(urllib.request.urlopen(req, timeout=30))["prompt_id"]
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR ComfyUI rejected the workflow: {e.read().decode()[:500]}")
    end = time.time() + TIMEOUT
    while time.time() < end:
        time.sleep(2)
        hist = json.loads(get(f"/history/{pid}"))
        if pid in hist:
            return _checked(hist[pid])
    sys.exit(f"ERROR ComfyUI run timed out after {TIMEOUT}s")


def _checked(entry: dict) -> dict:
    status = entry["status"]
    if status.get("status_str") != "success":
        sys.exit(f"ERROR ComfyUI run failed: {json.dumps(status.get('messages'))[-500:]}")
    return entry


def result_image(entry: dict, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """The first image a PreviewImage/SaveImage node produced, decoded by OpenCV."""
    img = next(i for o in entry["outputs"].values() for i in o.get("images", []))
    q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img["subfolder"], "type": img["type"]})
    return cv2.imdecode(np.frombuffer(get(f"/view?{q}", 60), np.uint8), flags)
