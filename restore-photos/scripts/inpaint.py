#!/usr/bin/env python3
"""Native-resolution repaint of the masked regions of a big scan (used by
restore.py --hires; also standalone). The masked regions are grouped and
packed into crops that reach the model at >= MIN_SCALE of the source
resolution (~1 MP per call), each crop is repainted with the repair prompt,
aligned back (ECC affine) and colour-matched, and only the mask pixels are
taken (feathered). Everything outside the mask stays as given.

    inpaint.py <image> <mask.png> <out.png> [--backend klein|qwen] [--seed 7] [--prompt "..."]
"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_client  # noqa: E402

MODEL_MP = 1.0
GROUP_PX = 48
CONTEXT_FRAC = 0.6
MIN_CTX = 128
MIN_SCALE = 0.5
FEATHER = 4


def _crops(regions, W, H):
    def frame(x0, y0, x1, y1):
        ctx = max(MIN_CTX, int(CONTEXT_FRAC * max(x1 - x0, y1 - y0)))
        return max(0, x0 - ctx), max(0, y0 - ctx), min(W, x1 + ctx), min(H, y1 + ctx)

    def scale(f):
        return min(1.0, (MODEL_MP * 1e6 / ((f[2] - f[0]) * (f[3] - f[1]))) ** 0.5)

    crops = []
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


def inpaint(img, mask, prompt, backend="klein", seed=7, base=None, progress=None):
    """img BGR, mask uint8 (>0 = repaint). base: image whose pixels are used
    where a crop fails (default img). Returns the repainted image."""
    from restore import align, match_colors
    H, W = img.shape[:2]
    out = (base if base is not None else img).copy()
    mask = (mask > 0).astype(np.uint8) * 255
    grouped = cv2.dilate(mask, np.ones((2 * GROUP_PX + 1,) * 2, np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    regions = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        sub = mask[y:y + h, x:x + w] & ((labels[y:y + h, x:x + w] == i).astype(np.uint8) * 255)
        ys, xs = np.where(sub > 0)
        if len(xs):
            regions.append((x + xs.min(), y + ys.min(), x + xs.max() + 1, y + ys.max() + 1))
    crops = _crops(regions, W, H)
    for k, ((x0, y0, x1, y1), _) in enumerate(crops):
        crop = img[y0:y1, x0:x1]
        cmask = mask[y0:y1, x0:x1]
        try:
            gen = comfy_client.edit(crop, prompt, backend, seed, MODEL_MP)
        except (RuntimeError, OSError) as e:
            print(f"inpaint: crop {k + 1} failed ({e}) - keeping the base pixels there", file=sys.stderr)
            continue
        gen = match_colors(crop, align(crop, gen))
        hard = (cmask > 0).astype(np.float32)
        alpha = np.maximum(cv2.GaussianBlur(hard, (0, 0), FEATHER), hard)[:, :, None]
        out[y0:y1, x0:x1] = np.clip(out[y0:y1, x0:x1].astype(np.float32) * (1 - alpha)
                                    + gen.astype(np.float32) * alpha + 0.5, 0, 255).astype(np.uint8)
        if progress:
            progress(k + 1, len(crops))
        else:
            print(f"inpaint: crop {k + 1}/{len(crops)} done", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("mask")
    ap.add_argument("out")
    ap.add_argument("--backend", choices=("klein", "qwen"), default="klein")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--prompt")
    a = ap.parse_args()
    from restore import PROMPT
    if not comfy_client.available(a.backend):
        sys.exit(1)
    img, mk = cv2.imread(a.image), cv2.imread(a.mask, cv2.IMREAD_GRAYSCALE)
    cv2.imwrite(a.out, inpaint(img, mk, a.prompt or PROMPT, a.backend, a.seed))
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
