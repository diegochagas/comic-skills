#!/usr/bin/env python3
"""AI inpainting for text erased over ART with Qwen-Image-Edit-2511, run
locally through a ComfyUI server — a free, unlimited alternative to LaMa
(inpaint_lama.py) that redraws structure (hair, faces, armor) instead of
smearing it. Selected with INPAINT=qwen; inpaint_lama.inpaint() dispatches
here and falls back to LaMa when the server is unreachable.

Same contract as inpaint_lama: only the masked pixels change, everything else
stays byte-exact.

How a page is processed:
  - the mask is grouped into regions (GROUP_PX), specks <= SMALL_PX use
    cv2.inpaint like LaMa does
  - regions are packed greedily into crops (region bbox + CONTEXT_FRAC
    context) as long as a crop still reaches the model at >= MIN_SCALE of its
    source resolution; a normal page is one crop, a 70 MP scan several
  - each crop goes to the model at ~MODEL_MP megapixels (native aspect,
    sides multiple of 16) with PROMPT, 4 steps (Lightning LoRA)
  - the output is scaled back, aligned to the crop (ECC affine, text excluded:
    the model shifts the picture by a few px) and only mask pixels (feathered
    1 px) replace the original
  - routing (INPAINT_ROUTE=auto, the default): a region whose surroundings are
    flat screentone - many halftone dots (DOTS_FRAC) and almost no line
    structure once the dots are blurred away (EDGE_FRAC) - goes to LaMa, which
    continues a dot pattern better than the model (measured on Golden Age
    pages: the model leaves a flat grey patch there). Everything else - art,
    faces, armor, gradients, plain tone - goes to the model.
    INPAINT_ROUTE=qwen sends every region to the model.
  - miss check: a region whose pixels the model barely changed (mean change
    < MISS_DIFF) still has its text -> that region goes to LaMa instead

ComfyUI requirements (not installed by setup.sh — see SKILL.md "Local AI
inpainting"): the ComfyUI-GGUF custom node and these files in its models/
folders — unet/qwen-image-edit-2511-Q4_K_M.gguf,
text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors,
vae/qwen_image_vae.safetensors,
loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors.
Env: COMFYUI_URL (default http://127.0.0.1:8188), COMFYUI_SERVICE (a systemd
--user unit started automatically when the server is down, e.g. comfyui).

Standalone use (debug):
    python inpaint_qwen.py <image> <mask.png> <out.png>
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

URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
SERVICE = os.environ.get("COMFYUI_SERVICE", "")
UNET = "qwen-image-edit-2511-Q4_K_M.gguf"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
LORA = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
PROMPT = ("Remove all the text from the image (speech bubble text, captions, titles and sound "
          "effects). Keep everything else unchanged.")
SEED = 42
MODEL_MP = 1.0       # megapixels the model works at (quality/speed sweet spot on 6 GB VRAM)
GROUP_PX = 48        # glyphs closer than this form one region
CONTEXT_FRAC = 0.6   # context around a region, as a fraction of its size
MIN_CTX = 96         # ...but at least this many px
MIN_SCALE = 0.5      # a crop may reach the model at no less than this scale; else split
MASK_GROW = 2        # px the mask is grown (anti-aliased edges)
SMALL_PX = 32        # regions no bigger than this use cv2.inpaint
MISS_DIFF = 12.0     # mean |change| (0-255) inside a region below which the text was not erased
ROUTE = os.environ.get("INPAINT_ROUTE", "auto").lower()   # auto | qwen
DOTS_FRAC = 0.70     # >= this share of the ring is halftone dots...
EDGE_FRAC = 0.25     # ...and <= this share holds a line/edge -> flat screentone -> LaMa
RING_IN, RING_OUT = 11, 81   # the neighbourhood a region is judged on (px)
TIMEOUT = 900        # s per model call

_state = {"ok": None}


def _get(path, timeout=10):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return r.read()


def available():
    """ComfyUI reachable (started through COMFYUI_SERVICE if needed) with the
    GGUF loader and all four model files. Cached per process."""
    if _state["ok"] is not None:
        return _state["ok"]
    ok, why = False, ""
    for attempt in range(2):
        try:
            info = json.loads(_get("/object_info/UnetLoaderGGUF"))
            if "UnetLoaderGGUF" not in info:
                why = "ComfyUI-GGUF custom node missing"
                break
            have = set(info["UnetLoaderGGUF"]["input"]["required"]["unet_name"][0])
            have |= set(json.loads(_get("/models/text_encoders")))
            have |= set(json.loads(_get("/models/vae"))) | set(json.loads(_get("/models/loras")))
            missing = [f for f in (UNET, CLIP, VAE, LORA) if f not in have]
            ok, why = not missing, f"model files missing: {', '.join(missing)}"
            break
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            why = f"ComfyUI not reachable at {URL}"
            if attempt or not SERVICE:
                break
            print(f"inpaint_qwen: starting systemd --user unit {SERVICE} ...", file=sys.stderr)
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
            for _ in range(60):
                time.sleep(2)
                try:
                    _get("/system_stats", 3)
                    break
                except (urllib.error.URLError, OSError):
                    pass
    if not ok:
        print(f"inpaint_qwen: unavailable ({why}) - using LaMa", file=sys.stderr)
    _state["ok"] = ok
    return ok


def _flat_screentone(img, m):
    """True when the region's surroundings are screentone with no real
    structure. Dots survive a small blur but vanish under a big one, so
    `dots` is high and `edges` low exactly there."""
    ring = ((cv2.dilate(m, np.ones((RING_OUT,) * 2, np.uint8)) > 0)
            & (cv2.dilate(m, np.ones((RING_IN,) * 2, np.uint8)) == 0))
    if ring.sum() < 500:
        return False
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    dots = (np.abs(g - cv2.GaussianBlur(g, (0, 0), 1.5))[ring] > 18).mean()
    big = cv2.GaussianBlur(g, (0, 0), 3.0)
    mag = cv2.magnitude(cv2.Sobel(big, cv2.CV_32F, 1, 0, ksize=5),
                        cv2.Sobel(big, cv2.CV_32F, 0, 1, ksize=5)) / 255.0
    edges = (mag[ring] > 2.0).mean()
    return dots >= DOTS_FRAC and edges <= EDGE_FRAC


def _upload(bgr):
    name = f"inpaint_{uuid.uuid4().hex[:12]}.png"
    png = cv2.imencode(".png", bgr)[1].tobytes()
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"subfolder\"\r\n\r\ncomic-skills\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(URL + "/upload/image", body,
                                 {"Content-Type": f"multipart/form-data; boundary={b}"})
    res = json.load(urllib.request.urlopen(req, timeout=60))
    return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]


def _workflow(image_name, w, h):
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": UNET}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}},
        "5": {"class_type": "CFGNorm", "inputs": {"model": ["4", 0], "strength": 1.0}},
        "6": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["5", 0], "lora_name": LORA, "strength_model": 1.0}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "8": {"class_type": "ImageScale", "inputs": {"image": ["7", 0], "upscale_method": "lanczos",
                                                     "width": w, "height": h, "crop": "disabled"}},
        "9": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["8", 0], "prompt": PROMPT}},
        "10": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["8", 0], "prompt": ""}},
        "11": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["9", 0], "reference_latents_method": "index_timestep_zero"}},
        "12": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["10", 0], "reference_latents_method": "index_timestep_zero"}},
        "13": {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["3", 0]}},
        "14": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["11", 0], "negative": ["12", 0],
                                                    "latent_image": ["13", 0], "seed": SEED, "steps": 4, "cfg": 1.0,
                                                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
        "16": {"class_type": "PreviewImage", "inputs": {"images": ["15", 0]}},   # temp/, not output/
    }


def _run_model(crop):
    """crop BGR -> model output BGR at the crop's size (not yet aligned)."""
    ch, cw = crop.shape[:2]
    s = (MODEL_MP * 1e6 / (cw * ch)) ** 0.5
    w, h = max(16, round(cw * s / 16) * 16), max(16, round(ch * s / 16) * 16)
    name = _upload(crop)
    req = urllib.request.Request(URL + "/prompt", json.dumps({"prompt": _workflow(name, w, h)}).encode(),
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
        return cv2.resize(out, (cw, ch), interpolation=cv2.INTER_LANCZOS4 if out.shape[1] < cw else cv2.INTER_AREA)
    raise RuntimeError(f"ComfyUI run timed out after {TIMEOUT}s")


def _align(crop, gen, cmask):
    """Warp gen onto crop (affine, ECC on grey levels, text pixels excluded)."""
    g1 = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    g2 = cv2.cvtColor(gen, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    warp = np.eye(2, 3, dtype=np.float32)
    keep = (cv2.dilate(cmask, np.ones((9, 9), np.uint8)) == 0).astype(np.uint8)
    try:
        _, warp = cv2.findTransformECC(g1, g2, warp, cv2.MOTION_AFFINE,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), keep, 5)
    except cv2.error:
        return gen
    return cv2.warpAffine(gen, warp, (crop.shape[1], crop.shape[0]),
                          flags=cv2.INTER_LANCZOS4 + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


def _crops(regions, W, H):
    """Greedy packing of region boxes into crops the model sees at >= MIN_SCALE."""
    def frame(x0, y0, x1, y1):
        ctx = max(MIN_CTX, int(CONTEXT_FRAC * max(x1 - x0, y1 - y0)))
        return max(0, x0 - ctx), max(0, y0 - ctx), min(W, x1 + ctx), min(H, y1 + ctx)

    def scale(f):
        return min(1.0, (MODEL_MP * 1e6 / ((f[2] - f[0]) * (f[3] - f[1]))) ** 0.5)

    crops = []   # [box(x0,y0,x1,y1), [region indices]]
    for i, (x0, y0, x1, y1) in sorted(enumerate(regions), key=lambda t: (t[1][1], t[1][0])):
        for c in crops:
            u = (min(c[0][0], x0), min(c[0][1], y0), max(c[0][2], x1), max(c[0][3], y1))
            if scale(frame(*u)) >= MIN_SCALE:
                c[0] = u
                c[1].append(i)
                break
        else:
            crops.append([(x0, y0, x1, y1), [i]])
    return [(frame(*box), idx) for box, idx in crops]


def inpaint(img, mask, progress=None, inplace=False, deadline=None, pending=None):
    """img BGR uint8, mask uint8 (>0 = erase). Same contract as
    inpaint_lama.inpaint_lama (deadline/pending included). Regions the model
    fails on are handed to LaMa."""
    import inpaint_lama
    H, W = img.shape[:2]
    out = img if inplace else img.copy()
    mask = (mask > 0).astype(np.uint8) * 255
    if MASK_GROW:
        mask = cv2.dilate(mask, np.ones((2 * MASK_GROW + 1,) * 2, np.uint8))
    grouped = cv2.dilate(mask, np.ones((2 * GROUP_PX + 1,) * 2, np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    del grouped
    if pending is not None:
        pending[:] = 0
    regions, big = [], []
    for i in range(1, n):
        gx, gy, gw, gh, _ = stats[i]
        sub = mask[gy:gy + gh, gx:gx + gw] & ((labels[gy:gy + gh, gx:gx + gw] == i).astype(np.uint8) * 255)
        ys, xs = np.where(sub > 0)
        if not len(xs):
            continue
        box = (gx + xs.min(), gy + ys.min(), gx + xs.max() + 1, gy + ys.max() + 1)
        if max(box[2] - box[0], box[3] - box[1]) <= SMALL_PX:
            p = inpaint_lama.TELEA_RADIUS + 2
            sx0, sy0, sx1, sy1 = max(0, box[0] - p), max(0, box[1] - p), min(W, box[2] + p), min(H, box[3] + p)
            wm = mask[sy0:sy1, sx0:sx1] & ((labels[sy0:sy1, sx0:sx1] == i).astype(np.uint8) * 255)
            out[sy0:sy1, sx0:sx1] = cv2.inpaint(out[sy0:sy1, sx0:sx1], wm, inpaint_lama.TELEA_RADIUS, cv2.INPAINT_TELEA)
            continue
        regions.append(box)
        big.append(i)
    fallback = np.zeros((H, W), np.uint8)
    if ROUTE == "auto":
        keep = []
        for j, (rx0, ry0, rx1, ry1) in enumerate(regions):
            rm = np.zeros((H, W), np.uint8)
            rm[ry0:ry1, rx0:rx1] = mask[ry0:ry1, rx0:rx1] & ((labels[ry0:ry1, rx0:rx1] == big[j]).astype(np.uint8) * 255)
            if _flat_screentone(out, rm):
                fallback |= rm
            else:
                keep.append(j)
        if len(keep) < len(regions):
            print(f"inpaint_qwen: {len(regions) - len(keep)} flat-screentone region(s) -> LaMa", file=sys.stderr)
        regions = [regions[j] for j in keep]
        big = [big[j] for j in keep]
    crops = _crops(regions, W, H)
    misses = 0
    for k, ((x0, y0, x1, y1), idx) in enumerate(crops):
        cmask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        for j in idx:
            lab = big[j]
            rx0, ry0, rx1, ry1 = regions[j]
            cmask[ry0 - y0:ry1 - y0, rx0 - x0:rx1 - x0] |= (
                mask[ry0:ry1, rx0:rx1] & ((labels[ry0:ry1, rx0:rx1] == lab).astype(np.uint8) * 255))
        if deadline is not None and time.time() > deadline:
            pending[y0:y1, x0:x1] |= cmask
            continue
        crop = out[y0:y1, x0:x1]
        try:
            gen = _align(crop, _run_model(crop), cmask)
        except (RuntimeError, urllib.error.URLError, OSError) as e:
            print(f"inpaint_qwen: {e} - LaMa for this crop", file=sys.stderr)
            fallback[y0:y1, x0:x1] |= cmask
            continue
        # miss check per region: the model must really have changed the text pixels
        nl, rl = cv2.connectedComponents(cv2.dilate(cmask, np.ones((2 * GROUP_PX + 1,) * 2, np.uint8)))
        diff = np.abs(crop.astype(np.int16) - gen.astype(np.int16)).max(axis=2)
        use = cmask.copy()
        for r in range(1, nl):
            rm = (rl == r) & (cmask > 0)
            if rm.any() and diff[rm].mean() < MISS_DIFF:
                use[rm] = 0
                fallback[y0:y1, x0:x1][rm] = 255
                misses += 1
        hard = (use > 0).astype(np.float32)
        alpha = np.maximum(cv2.GaussianBlur(hard, (3, 3), 0), hard)[:, :, None]
        out[y0:y1, x0:x1] = np.clip(crop.astype(np.float32) * (1 - alpha) + gen.astype(np.float32) * alpha + 0.5,
                                    0, 255).astype(np.uint8)
        if progress:
            progress(k + 1, len(crops))
    if misses:
        print(f"inpaint_qwen: {misses} region(s) the model did not erase -> LaMa", file=sys.stderr)
    if fallback.any():
        inpaint_lama.lama_or_telea(out, fallback, inplace=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    if not available():
        sys.exit(1)
    im = cv2.imread(sys.argv[1])
    mk = cv2.imread(sys.argv[2], cv2.IMREAD_GRAYSCALE)
    t = time.time()
    cv2.imwrite(sys.argv[3], inpaint(im, mk, progress=lambda i, n: print(f"crop {i}/{n}")))
    print(f"qwen -> {sys.argv[3]} ({time.time() - t:.0f}s)")
