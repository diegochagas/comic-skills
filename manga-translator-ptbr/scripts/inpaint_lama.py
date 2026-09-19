#!/usr/bin/env python3
"""AI inpainting ("generative fill") for text erased over ART, using LaMa.

The comic-text-detector model only *finds* text. Erasing it over a busy
background (screentone, hatching, gradients, a drawing behind a balloon-less
SFX) used to be cv2.inpaint (Telea), which smears. This module runs the LaMa
inpainting network (ONNX export from https://huggingface.co/Carve/LaMa-ONNX,
fixed 512x512 input, CPU ok) on a crop around each masked region and pastes
back ONLY the masked pixels, so everything outside the mask stays byte-exact.

It is used by detect_text.py and clean_blocks.py for the components whose
surroundings are NOT one plain color; plain-color components keep the exact
sampled solid fill they always had.

    from inpaint_lama import inpaint
    cleaned = inpaint(img_bgr, mask_u8)          # LaMa if the model exists,
                                                 # cv2.inpaint otherwise

Model: manga-translator-ptbr/models/lama_fp32.onnx (setup.sh downloads it). Env override:
INPAINT=telea forces the old OpenCV path; INPAINT=qwen sends the regions to a
local Qwen-Image-Edit through ComfyUI (inpaint_qwen.py), falling back to LaMa
when the server is unreachable or the model leaves a region's text in place.

How a region is processed:
  - the mask is grouped into regions (mask dilated by GROUP_PX so the glyphs
    of one balloon/SFX form one region)
  - a square crop around the region with >= CONTEXT_FRAC context on each side
    (at least MIN_CTX px) is cut; if it is bigger than 512 px it is
    downscaled, but never below MIN_SCALE - beyond that the crop is tiled
    into windows (512 px in model space) that intersect the mask, so fine
    600 dpi screentone keeps enough detail
  - LaMa output is resized back and only mask pixels (feathered 1 px) replace
    the original

Memory: everything per region works on bbox slices; the only full-page
buffers are the mask, its grouped copy and one int32 label map, so 71 MP
scans fit in ~1 GB.

Standalone use (debug):
    python inpaint_lama.py <image> <mask.png> <out.png>
"""
import os
import sys
import time

import cv2
import numpy as np

MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "lama_fp32.onnx")  # <skill>/models
SIZE = 512
GROUP_PX = 48        # glyphs closer than this are inpainted as one region
CONTEXT_FRAC = 0.6   # context around a region, as a fraction of its size
MIN_CTX = 96         # ...but at least this many px
MIN_SCALE = 0.5      # never downscale the crop more than this; tile instead
MASK_GROW = 2        # px the mask is grown before inpainting (anti-aliased edges)
TELEA_RADIUS = 4
SMALL_PX = 32        # regions no bigger than this use cv2.inpaint (not worth a LaMa call)

_sess = None


def available():
    return os.environ.get("INPAINT", "").lower() != "telea" and os.path.exists(MODEL)


def _qwen():
    """inpaint_qwen when INPAINT=qwen and its ComfyUI server is usable, else None."""
    if os.environ.get("INPAINT", "").lower() != "qwen":
        return None
    import inpaint_qwen
    return inpaint_qwen if inpaint_qwen.available() else None


def _session():
    global _sess
    if _sess is None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.add_session_config_entry("session.set_denormal_as_zero", "1")
        _sess = ort.InferenceSession(MODEL, so, providers=["CPUExecutionProvider"])
    return _sess


def _run(crop_bgr, crop_mask):
    """crop_bgr: (512,512,3) uint8, crop_mask: (512,512) uint8 0/255 -> (512,512,3) uint8 BGR"""
    img = crop_bgr[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    m = (crop_mask > 0).astype(np.float32)[None, None]
    out = _session().run(None, {"image": np.ascontiguousarray(img), "mask": m})[0][0]
    out = out.transpose(1, 2, 0)
    if out.max() <= 1.5:          # some exports return 0..1
        out = out * 255.0
    return np.clip(out, 0, 255).astype(np.uint8)[:, :, ::-1]


def _inpaint_window(img, wmask, x0, y0, x1, y1, scale):
    """Inpaint img[y0:y1, x0:x1] in place; wmask is the mask for that window
    (same shape as the window), the model sees the window at `scale`."""
    crop = img[y0:y1, x0:x1]
    h, w = crop.shape[:2]
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    small = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA) if scale != 1 else crop
    smask = cv2.resize(wmask, (nw, nh), interpolation=cv2.INTER_NEAREST) if scale != 1 else wmask
    smask = cv2.dilate(smask, np.ones((3, 3), np.uint8))   # keep a full-pixel margin after resize
    canvas = np.zeros((SIZE, SIZE, 3), np.uint8)
    canvas[:nh, :nw] = small
    # pad by edge replication instead of black so LaMa doesn't hallucinate a border
    if nh < SIZE:
        canvas[nh:, :nw] = small[-1:, :]
    if nw < SIZE:
        canvas[:, nw:] = canvas[:, nw - 1:nw]
    mcan = np.zeros((SIZE, SIZE), np.uint8)
    mcan[:nh, :nw] = smask
    out = _run(canvas, mcan)[:nh, :nw]
    if scale != 1:
        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC)
    # paste only the masked pixels (soft 1 px edge)
    hard = (wmask > 0).astype(np.float32)
    alpha = np.maximum(cv2.GaussianBlur(hard, (3, 3), 0), hard)[:, :, None]
    res = crop.astype(np.float32) * (1 - alpha) + out.astype(np.float32) * alpha
    img[y0:y1, x0:x1] = np.clip(res + 0.5, 0, 255).astype(np.uint8)


def _place(region_mask, rx0, ry0, x0, y0, x1, y1):
    """Window-sized mask (for img[y0:y1, x0:x1]) holding region_mask, whose
    top-left is at page (rx0, ry0)."""
    wm = np.zeros((y1 - y0, x1 - x0), np.uint8)
    rh, rw = region_mask.shape
    sx0, sy0 = max(x0, rx0), max(y0, ry0)
    sx1, sy1 = min(x1, rx0 + rw), min(y1, ry0 + rh)
    if sx1 > sx0 and sy1 > sy0:
        wm[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = region_mask[sy0 - ry0:sy1 - ry0, sx0 - rx0:sx1 - rx0]
    return wm


def inpaint_lama(img, mask, progress=None, inplace=False, deadline=None, pending=None):
    """img BGR uint8, mask uint8 (>0 = erase). Returns the inpainted image
    (img itself when inplace=True).

    deadline: optional time.time() value; regions are processed in order until
    it passes, then the function returns early.  If `pending` (a uint8 array
    of the mask's shape) is given, the mask pixels of the regions that were NOT
    processed are written into it (all zero = finished) so a caller can save
    the partial result and resume later with mask=pending."""
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
    for i in range(1, n):
        gx, gy, gw, gh, _ = stats[i]
        if deadline is not None and time.time() > deadline:
            # out of time: hand the untouched regions back to the caller
            pending[gy:gy + gh, gx:gx + gw] |= mask[gy:gy + gh, gx:gx + gw] & ((labels[gy:gy + gh, gx:gx + gw] == i).astype(np.uint8) * 255)
            continue
        # this region's mask, on the group's bbox slice only
        region = mask[gy:gy + gh, gx:gx + gw] & ((labels[gy:gy + gh, gx:gx + gw] == i).astype(np.uint8) * 255)
        ys, xs = np.where(region > 0)
        if not len(xs):
            continue
        rx0, ry0 = gx + xs.min(), gy + ys.min()
        rx1, ry1 = gx + xs.max() + 1, gy + ys.max() + 1
        region = region[ry0 - gy:ry1 - gy, rx0 - gx:rx1 - gx]
        rw, rh = rx1 - rx0, ry1 - ry0
        if max(rw, rh) <= SMALL_PX:
            # specks (anti-alias flecks, a stray dot): a LaMa call costs
            # seconds and changes nothing visible at this size -> OpenCV
            p = TELEA_RADIUS + 2
            sx0, sy0 = max(0, rx0 - p), max(0, ry0 - p)
            sx1, sy1 = min(W, rx1 + p), min(H, ry1 + p)
            wm = _place(region, rx0, ry0, sx0, sy0, sx1, sy1)
            out[sy0:sy1, sx0:sx1] = cv2.inpaint(out[sy0:sy1, sx0:sx1], wm, TELEA_RADIUS, cv2.INPAINT_TELEA)
            continue
        ctx = max(MIN_CTX, int(CONTEXT_FRAC * max(rw, rh)))
        side = max(rw, rh) + 2 * ctx
        cx, cy = (rx0 + rx1) // 2, (ry0 + ry1) // 2
        x0, y0 = max(0, cx - side // 2), max(0, cy - side // 2)
        x1, y1 = min(W, x0 + side), min(H, y0 + side)
        x0, y0 = max(0, x1 - side), max(0, y1 - side)
        scale = min(1.0, SIZE / max(x1 - x0, y1 - y0))
        if scale >= MIN_SCALE:
            _inpaint_window(out, _place(region, rx0, ry0, x0, y0, x1, y1), x0, y0, x1, y1, scale)
        else:
            # big region: tile in windows of SIZE/MIN_SCALE source px, only
            # where the mask is; each tile owns the mask pixels of its inner
            # part, the border strip is context for the neighbouring tile
            win = int(SIZE / MIN_SCALE)
            margin = MIN_CTX // 2
            step = win - 2 * MIN_CTX
            remaining = region.copy()
            for ty in range(y0, y1, step):
                for tx in range(x0, x1, step):
                    wx1, wy1 = min(W, tx + win), min(H, ty + win)
                    wx0, wy0 = max(0, wx1 - win), max(0, wy1 - win)
                    sub = _place(remaining, rx0, ry0, wx0, wy0, wx1, wy1)
                    inner = np.zeros_like(sub)
                    iy0 = 0 if wy0 == 0 else margin
                    ix0 = 0 if wx0 == 0 else margin
                    iy1 = sub.shape[0] if wy1 == H else sub.shape[0] - margin
                    ix1 = sub.shape[1] if wx1 == W else sub.shape[1] - margin
                    inner[iy0:iy1, ix0:ix1] = sub[iy0:iy1, ix0:ix1]
                    if not inner.any():
                        continue
                    _inpaint_window(out, inner, wx0, wy0, wx1, wy1, MIN_SCALE)
                    # mark those pixels done (in region coordinates)
                    sx0, sy0 = max(wx0, rx0), max(wy0, ry0)
                    sx1, sy1 = min(wx1, rx1), min(wy1, ry1)
                    if sx1 > sx0 and sy1 > sy0:
                        remaining[sy0 - ry0:sy1 - ry0, sx0 - rx0:sx1 - rx0] &= ~inner[sy0 - wy0:sy1 - wy0, sx0 - wx0:sx1 - wx0]
        if progress:
            progress(i, n - 1)
    return out


def inpaint(img, mask, progress=None, inplace=False, deadline=None, pending=None):
    """Qwen with INPAINT=qwen (see inpaint_qwen.py), else LaMa when the model is
    present (and INPAINT != telea), else cv2.inpaint.
    deadline/pending: see inpaint_lama (ignored by the OpenCV fallback)."""
    q = _qwen()
    if q:
        return q.inpaint(img, mask, progress, inplace, deadline, pending)
    return lama_or_telea(img, mask, progress, inplace, deadline, pending)


def lama_or_telea(img, mask, progress=None, inplace=False, deadline=None, pending=None):
    """LaMa when the model is present (and INPAINT != telea), else cv2.inpaint."""
    if available():
        return inpaint_lama(img, mask, progress, inplace, deadline, pending)
    if pending is not None:
        pending[:] = 0
    res = cv2.inpaint(img, (mask > 0).astype(np.uint8) * 255, TELEA_RADIUS, cv2.INPAINT_TELEA)
    if inplace:
        img[:] = res
        return img
    return res


def method_name():
    return "qwen" if _qwen() else "lama" if available() else "telea"


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    im = cv2.imread(sys.argv[1])
    mk = cv2.imread(sys.argv[2], cv2.IMREAD_GRAYSCALE)
    cv2.imwrite(sys.argv[3], inpaint(im, mk, progress=lambda i, n: print(f"\r{i}/{n}", end="")))
    print(f"\n{method_name()} -> {sys.argv[3]}")
