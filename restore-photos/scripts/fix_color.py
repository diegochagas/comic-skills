#!/usr/bin/env python3
"""Automatic colour and contrast fix for faded / colour-cast prints.

Steps (each can be turned down or off):
  1. auto levels   every channel is stretched so that its darkest 0.5 % and
                   brightest 0.5 % reach black and white - this alone removes
                   most of the orange/magenta/green/blue cast of a faded print
                   (--levels 0..1, default 1.0)
  2. grey world    the remaining tint of the mid-tones is neutralised in LAB
                   (--wb 0..1, default 0.5: half way, keeps warm skin tones)
  3. contrast      CLAHE on the lightness channel (--contrast 0..3, default 1.2)
  4. saturation    (--sat, default 1.1)
A damage mask (--mask) excludes the white patches from the statistics so they
do not pull the white point. --strength blends the result with the original.

    fix_color.py <image> <out> [--mask m.png] [--levels 1] [--wb 0.5]
                 [--contrast 1.2] [--sat 1.1] [--strength 1]
"""
import argparse
import sys

import cv2
import numpy as np


def auto_levels(bgr, valid, amount=1.0, lo_p=0.5, hi_p=99.5):
    out = bgr.astype(np.float32)
    for c in range(3):
        ch = bgr[:, :, c][valid]
        lo, hi = np.percentile(ch, lo_p), np.percentile(ch, hi_p)
        if hi - lo < 10:
            continue
        stretched = (out[:, :, c] - lo) * (255.0 / (hi - lo))
        out[:, :, c] = out[:, :, c] * (1 - amount) + stretched * amount
    return np.clip(out, 0, 255)


def grey_world(bgr_f, valid, amount=0.5):
    lab = cv2.cvtColor(bgr_f.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    mid = valid & (lab[:, :, 0] > 40) & (lab[:, :, 0] < 215)
    if mid.sum() < 100:
        mid = valid
    for c in (1, 2):
        shift = lab[:, :, c][mid].mean() - 128.0
        lab[:, :, c] -= shift * amount
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)


def contrast(bgr_f, clip=1.2):
    if clip <= 0:
        return bgr_f
    lab = cv2.cvtColor(bgr_f.astype(np.uint8), cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR).astype(np.float32)


def saturation(bgr_f, k=1.1):
    if abs(k - 1.0) < 1e-3:
        return bgr_f
    hsv = cv2.cvtColor(bgr_f.astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * k, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)


def fix(bgr, mask=None, levels=1.0, wb=0.5, clahe=1.2, sat=1.1, strength=1.0):
    valid = np.ones(bgr.shape[:2], bool) if mask is None else (mask == 0)
    # ignore a plain scanner border as well: 2 % frame
    H, W = bgr.shape[:2]
    frame = np.zeros((H, W), bool)
    frame[int(H * 0.02):H - int(H * 0.02), int(W * 0.02):W - int(W * 0.02)] = True
    valid &= frame
    out = auto_levels(bgr, valid, levels) if levels > 0 else bgr.astype(np.float32)
    if wb > 0:
        out = grey_world(out, valid, wb)
    out = contrast(out, clahe)
    out = saturation(out, sat)
    if strength < 1.0:
        out = bgr.astype(np.float32) * (1 - strength) + out * strength
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--mask")
    ap.add_argument("--levels", type=float, default=1.0)
    ap.add_argument("--wb", type=float, default=0.5)
    ap.add_argument("--contrast", type=float, default=1.2)
    ap.add_argument("--sat", type=float, default=1.1)
    ap.add_argument("--strength", type=float, default=1.0)
    a = ap.parse_args()
    bgr = cv2.imread(a.image)
    if bgr is None:
        sys.exit(f"cannot read {a.image}")
    mask = cv2.imread(a.mask, cv2.IMREAD_GRAYSCALE) if a.mask else None
    out = fix(bgr, mask, a.levels, a.wb, a.contrast, a.sat, a.strength)
    cv2.imwrite(a.out, out, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
