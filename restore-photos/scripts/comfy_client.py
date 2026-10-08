#!/usr/bin/env python3
"""Minimal ComfyUI client shared by the restore-photos scripts.

Two image-edit backends, both local and free:
  qwen   Qwen-Image-Edit-2511 (GGUF Q4 + Lightning 4-step LoRA), ~100 s per
         call on a 6 GB GPU, follows the instruction best.
  klein  FLUX.2 klein 4B (fp8, distilled 4 steps), ~30 s per call, keeps
         structure well.

Settings (env, else ~/.config/photo-restore/comfyui.env):
  COMFYUI_URL      default http://127.0.0.1:8188
  COMFYUI_SERVICE  systemd --user unit to start when the server is down
                   (optional, e.g. comfyui)

Nothing is written in the ComfyUI output folder: results come back through
PreviewImage (temp/) and /view.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import cv2
import numpy as np

CONFIG = os.path.expanduser("~/.config/photo-restore/comfyui.env")


def _load_env():
    if os.environ.get("COMFYUI_URL") or not os.path.exists(CONFIG):
        return
    for line in open(CONFIG, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            if v:
                os.environ.setdefault(k.strip(), v)


_load_env()
URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
SERVICE = os.environ.get("COMFYUI_SERVICE", "")
TIMEOUT = int(os.environ.get("COMFYUI_TIMEOUT", "900"))

QWEN = {"unet": "qwen-image-edit-2511-Q4_K_M.gguf",
        "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "vae": "qwen_image_vae.safetensors",
        "lora": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"}
KLEIN = {"unet": "flux-2-klein-4b-fp8.safetensors",
         "clip": "qwen_3_4b.safetensors",
         "vae": "flux2-vae.safetensors"}

_state = {}


def _get(path, timeout=10):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return r.read()


def _start_service():
    if not SERVICE:
        return False
    print(f"comfy_client: starting systemd --user unit {SERVICE} ...", file=sys.stderr)
    subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
    for _ in range(90):
        time.sleep(2)
        try:
            _get("/system_stats", 3)
            return True
        except (urllib.error.URLError, OSError):
            pass
    return False


def available(backend="qwen"):
    """True when ComfyUI answers and has every model file of `backend`.
    Starts COMFYUI_SERVICE once if the server is down. Cached per process."""
    if backend in _state:
        return _state[backend]
    ok, why = False, ""
    for attempt in range(2):
        try:
            have = set()
            for kind in ("unet", "diffusion_models", "text_encoders", "vae", "loras"):
                try:
                    have |= set(json.loads(_get(f"/models/{kind}")))
                except (urllib.error.HTTPError, ValueError):
                    pass
            if backend == "qwen":
                info = json.loads(_get("/object_info/UnetLoaderGGUF"))
                if "UnetLoaderGGUF" not in info:
                    why = "ComfyUI-GGUF custom node missing"
                    break
                have |= set(info["UnetLoaderGGUF"]["input"]["required"]["unet_name"][0])
            need = QWEN if backend == "qwen" else KLEIN
            missing = [f for f in need.values() if f not in have]
            ok, why = not missing, f"model files missing: {', '.join(missing)}"
            break
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            why = (f"ComfyUI not reachable at {URL}; start it with: "
                   f"systemctl --user start {SERVICE or 'comfyui'}")
            if attempt or not _start_service():
                break
    if not ok:
        print(f"comfy_client: {backend} unavailable ({why})", file=sys.stderr)
    _state[backend] = ok
    return ok


def upload(bgr):
    name = f"restore_{uuid.uuid4().hex[:12]}.png"
    png = cv2.imencode(".png", bgr)[1].tobytes()
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"subfolder\"\r\n\r\nphoto-restore\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(URL + "/upload/image", body,
                                 {"Content-Type": f"multipart/form-data; boundary={b}"})
    res = json.load(urllib.request.urlopen(req, timeout=60))
    return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]


def workflow_qwen(image_name, w, h, prompt, seed, refs=(), strength=1.0):
    """refs: up to two uploaded reference image names, fed to the edit
    encoder as image2 / image3 (Qwen-Image-Edit-2511 multi-image input)."""
    m = QWEN
    wf = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": m["unet"]}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["clip"], "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "CFGNorm", "inputs": {"model": ["4", 0], "strength": 1.0}},
        "6": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["5", 0], "lora_name": m["lora"], "strength_model": 1.0}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "8": {"class_type": "ImageScale", "inputs": {"image": ["7", 0], "upscale_method": "lanczos",
                                                     "width": w, "height": h, "crop": "disabled"}},
        "9": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["8", 0], "prompt": prompt}},
        "10": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["8", 0], "prompt": ""}},
        "11": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["9", 0], "reference_latents_method": "index_timestep_zero"}},
        "12": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["10", 0], "reference_latents_method": "index_timestep_zero"}},
        "13": {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["3", 0]}},
        "14": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["11", 0], "negative": ["12", 0],
                                                    "latent_image": ["13", 0], "seed": seed, "steps": 4, "cfg": 1.0,
                                                    "sampler_name": "euler", "scheduler": "simple", "denoise": strength}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
        "16": {"class_type": "PreviewImage", "inputs": {"images": ["15", 0]}},
    }
    for k, name in enumerate(refs[:2]):
        i = 100 + 10 * k
        wf[str(i)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf[str(i + 1)] = {"class_type": "ImageScaleToTotalPixels",
                          "inputs": {"image": [str(i), 0], "upscale_method": "lanczos", "megapixels": 0.5,
                                     "resolution_steps": 16}}
        for node in ("9", "10"):
            wf[node]["inputs"][f"image{k + 2}"] = [str(i + 1), 0]
    return wf


def workflow_klein(image_name, w, h, prompt, seed, refs=()):
    """refs: uploaded reference image names (same people, undamaged). Each is
    VAE-encoded and chained as one more ReferenceLatent after the photo, the
    FLUX.2 multi-reference layout."""
    m = KLEIN
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": m["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["clip"], "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "8": {"class_type": "ImageScale", "inputs": {"image": ["7", 0], "upscale_method": "lanczos",
                                                     "width": w, "height": h, "crop": "disabled"}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "10": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["9", 0]}},
        "13": {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["3", 0]}},
        "11": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["9", 0], "latent": ["13", 0]}},
        "12": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["10", 0], "latent": ["13", 0]}},
        "17": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["11", 0], "negative": ["12", 0], "cfg": 1.0}},
        "18": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "19": {"class_type": "Flux2Scheduler", "inputs": {"steps": 4, "width": w, "height": h}},
        "20": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "21": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "14": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["21", 0], "guider": ["17", 0], "sampler": ["18", 0],
                                                                 "sigmas": ["19", 0], "latent_image": ["20", 0]}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
        "16": {"class_type": "PreviewImage", "inputs": {"images": ["15", 0]}},
    }
    pos, neg = "11", "12"
    for k, name in enumerate(refs):
        i = 100 + 10 * k
        wf[str(i)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf[str(i + 1)] = {"class_type": "ImageScaleToTotalPixels",
                          "inputs": {"image": [str(i), 0], "upscale_method": "lanczos", "megapixels": 0.5,
                                     "resolution_steps": 16}}
        wf[str(i + 2)] = {"class_type": "VAEEncode", "inputs": {"pixels": [str(i + 1), 0], "vae": ["3", 0]}}
        wf[str(i + 3)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": [pos, 0], "latent": [str(i + 2), 0]}}
        wf[str(i + 4)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": [neg, 0], "latent": [str(i + 2), 0]}}
        pos, neg = str(i + 3), str(i + 4)
    wf["17"]["inputs"].update(positive=[pos, 0], negative=[neg, 0])
    return wf


def edit(bgr, prompt, backend="qwen", seed=42, model_mp=1.0, refs=(), strength=1.0):
    """Run one image-edit call. Returns BGR at the input's size (the model
    works at ~model_mp megapixels, sides multiple of 16). refs: BGR images of
    the same people undamaged, used as identity references (qwen: at most 2).
    strength (qwen): below 1 the model starts from the photo itself and only
    partly repaints it, so pose, gaze and expression stay the photo's."""
    h0, w0 = bgr.shape[:2]
    s = min(1.0, (model_mp * 1e6 / (w0 * h0)) ** 0.5) if model_mp else 1.0
    w, h = max(16, round(w0 * s / 16) * 16), max(16, round(h0 * s / 16) * 16)
    name = upload(bgr)
    ref_names = [upload(r) for r in refs]
    wf = (workflow_klein(name, w, h, prompt, seed, ref_names) if backend == "klein"
          else workflow_qwen(name, w, h, prompt, seed, ref_names, strength))
    req = urllib.request.Request(URL + "/prompt", json.dumps({"prompt": wf}).encode(),
                                 {"Content-Type": "application/json"})
    try:
        pid = json.load(urllib.request.urlopen(req, timeout=30))["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI rejected the workflow: {e.read().decode()[:500]}")
    end = time.time() + TIMEOUT
    while time.time() < end:
        time.sleep(2)
        hist = json.loads(_get(f"/history/{pid}"))
        if pid not in hist:
            continue
        st = hist[pid]["status"]
        if st.get("status_str") != "success":
            raise RuntimeError(f"ComfyUI run failed: {json.dumps(st.get('messages'))[-500:]}")
        img = next(i for o in hist[pid]["outputs"].values() for i in o.get("images", []))
        q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img["subfolder"], "type": img["type"]})
        out = cv2.imdecode(np.frombuffer(_get(f"/view?{q}", 60), np.uint8), cv2.IMREAD_COLOR)
        return cv2.resize(out, (w0, h0), interpolation=cv2.INTER_LANCZOS4 if out.shape[1] < w0 else cv2.INTER_AREA)
    raise RuntimeError(f"ComfyUI run timed out after {TIMEOUT}s")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--check":
        for be in ("qwen", "klein"):
            print(f"{be}: {'ok' if available(be) else 'unavailable'}")
        sys.exit(0)
    if len(sys.argv) < 4:
        print("usage: comfy_client.py <image> <out.png> \"<prompt>\" [qwen|klein] [seed]")
        sys.exit(1)
    be = sys.argv[4] if len(sys.argv) > 4 else "qwen"
    if not available(be):
        sys.exit(1)
    t = time.time()
    cv2.imwrite(sys.argv[2], edit(cv2.imread(sys.argv[1]), sys.argv[3], be, int(sys.argv[5]) if len(sys.argv) > 5 else 42))
    print(f"{be} -> {sys.argv[2]} ({time.time() - t:.0f}s)")
