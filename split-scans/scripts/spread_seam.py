#!/usr/bin/env python3
"""Join the two scanned pages of a double-page spread into one picture with
no visible fold: the pages are aligned, the strip of drawing the binding
swallowed is given back as a gap, and that strip is drawn by the local
Qwen-Image-Edit model (GIMPhoto's Generative Fill process, through
manga-translator-ptbr's qwen_fill.py) so lines run straight across.

    spread_seam.py <left page> <right page> <out.png> [--theta DEG] [--dy N] [--gap N]
                   [--eat N] [--no-ai]

  1. align   the straight lines that cross the fold (speed lines, rays,
             panel borders) must continue at the same angle and height on
             the other page: the right page's rotation (theta), vertical
             shift (dy) and the missing width (gap) that make them agree are
             fitted (spread_align.py). Pages scanned one at a time are
             often turned a little against each other (0.4 deg on Soul of
             Gold 056-057 = 15 px from top to bottom). A spread with fewer
             than 3 such lines falls back to matching the two edges' grey
             profiles (shift and gap only, theta 0).
  2. join    the washed-out or shaded band next to each page's fold gets
             the page's tones back, block by block of rows (even_band), then left page, gap, right page turned
             by theta and moved by dy, cut to the rows both pages have
             (join_pages).
  3. repair  the fold is selected - the gap, up to MODEL_BAND px of each
             page's band (fold_band) and EAT px more - and the
             model, which sees the drawing there, takes the fold away and
             joins the lines (repair_seam), tile by tile from the
             top, each tile seeing the picture on both sides. Each result is
             blended in by Poisson editing (seamless: the model's paper is
             whiter than the scan's, which showed as a faint line) and
             cross-faded into the tile above it (repair_seam).
             Without the model (--no-ai, or no ComfyUI) the strip is LaMa's.

Writes <out.png>, <out>_joined.png (step 2, before the model) and prints
theta, dy, gap and the strip's columns as JSON. --theta/--dy/--gap replace
the estimate.
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "manga-translator-ptbr", "scripts"))
sys.path.insert(0, HERE)
from spread_align import find_alignment  # noqa: E402  (re-exported: split_scans uses it)

EAT = 24             # px of each inner edge always redrawn, past its washed-out band
FEATHER = 10         # px over which the redrawn strip fades into the pages
BAND_MAX = 200       # px from the fold where the scan's tones may be off
BAND_REF = (210, 320)  # px from the fold: columns taken as the page's normal tones
MODEL_BAND = 30      # px of a band the model redraws (the rest only gets its tones back; 60 made it invent texture)
BAND_TOL = 6         # grey levels a column's darks may differ from normal
BAND_RUN = 8         # columns in a row that must match before the band ends
BLOCK_ROWS = 160     # rows per block in which the band's tones are measured
BAND_COLS = 8        # columns per block
LEVEL_SHARE = 0.03   # a block needs this share of dark and of light pixels to be measured
MAX_GAIN = 2.5       # the band's contrast is raised at most this much (more only shows the scan's noise)
BAND_REF_PX = 40     # columns past the band whose tones the band is given
BAND_FADE = 12       # px over which the tone correction fades out at the band's end
SEAMLESS_PAD = 8     # px mirrored around a tile so the Poisson blend may touch its border
TRIES = 3            # model passes per tile, each on the best result so far
GOOD_ENOUGH = 0.65   # share of the fold's visibility (fold_left) a run may leave and still be taken at once
TILE = 768           # px side of the square the model sees per step
TILE_OVERLAP = 96


def _gray(img):
    return cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2GRAY).astype(np.float32)


def fold_band(page, fold_on_left):
    """Width in px of the band next to the page's fold where the scan is
    washed out or shaded (the paper curves into the binding): columns
    counted from the fold until BAND_RUN in a row have the page's normal
    darks AND lights again (8th / 92nd percentile). On 056-057 the right
    page's darks are washed out over 79 px, the left page's lights shaded
    over ~140 px with its darks untouched.
    Example: fold_band(right_page, fold_on_left=True) -> 79"""
    view = page if fold_on_left else page[:, ::-1]
    if view.shape[1] < BAND_REF[1]:
        return 0
    levels = np.percentile(_gray(view[:, :BAND_REF[1]]), (8, 92), axis=0).astype(np.float32)
    levels = cv2.blur(levels, (5, 1))
    normal = np.median(levels[:, BAND_REF[0]:], axis=1, keepdims=True)
    off = (np.abs(levels - normal) > BAND_TOL).any(axis=0)
    run = 0
    for x in range(BAND_MAX):
        run = 0 if off[x] else run + 1
        if run >= BAND_RUN:
            return x + 1 - BAND_RUN
    return BAND_MAX


def turn_page(page, theta):
    """(page turned by theta degrees about the middle of its left edge,
    valid-pixel mask, pad): pad px are added on the left for what moves past
    the edge."""
    h, w = page.shape[:2]
    th = np.radians(theta)
    pad = int(np.ceil(h / 2 * abs(np.sin(th)))) + 2
    c, s, cy = np.cos(th), np.sin(th), h / 2
    m = np.float32([[c, -s, pad + s * cy], [s, c, cy - c * cy]])
    turned = cv2.warpAffine(page, m, (w + pad, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    valid = cv2.warpAffine(np.full((h, w), 255, np.uint8), m, (w + pad, h), flags=cv2.INTER_NEAREST)
    return turned, valid, pad


def _cut(gray):
    """Grey level between a column group's darks and lights: the middle of
    its 5th and 95th percentiles over the whole height. A fixed cut fails in
    the band: its washed-out darks (120-140 on 056-057) straddle 128."""
    lo, hi = np.percentile(gray, (5, 95))
    return (lo + hi) / 2


def _levels(gray, cut, fallback=None):
    """(dark, light) of a block of pixels: the medians of its pixels below
    and above `cut` - medians of the two groups, not percentiles of the
    whole, so ink lines on a white fist compare with ink, not with paper. A
    group under LEVEL_SHARE of the block (a starfield has almost no light
    pixels) takes fallback's value, or None without one."""
    out = []
    for group, k in ((gray[gray < cut], 0), (gray[gray >= cut], 1)):
        if group.size >= LEVEL_SHARE * gray.size:
            out.append(float(np.median(group)))
        else:
            out.append(None if fallback is None else fallback[k])
    return None if None in out else tuple(out)


def _band_map(gray, band):
    """(gain, offset) per row block x band column that give the band's darks
    and lights those of the columns just past it in the same rows; a level
    a block lacks comes from the whole height."""
    ref_px = gray[:, band + 4:band + 4 + BAND_REF_PX]
    ref_cut = _cut(ref_px)
    ref_all = _levels(ref_px, ref_cut, (60.0, 250.0))
    tops = range(0, gray.shape[0], BLOCK_ROWS)
    gain = np.ones((len(tops), band))
    offset = np.zeros((len(tops), band))
    for x0 in range(0, band, BAND_COLS):
        col_px = gray[:, x0:x0 + BAND_COLS]
        cut = _cut(col_px)
        col_all = _levels(col_px, cut, ref_all)
        for i, t in enumerate(tops):
            ref = _levels(ref_px[t:t + BLOCK_ROWS], ref_cut, ref_all)
            col = _levels(col_px[t:t + BLOCK_ROWS], cut, col_all)
            g = np.clip((ref[1] - ref[0]) / max(col[1] - col[0], 20.0), 1 / MAX_GAIN, MAX_GAIN)
            gain[i, x0:x0 + BAND_COLS], offset[i, x0:x0 + BAND_COLS] = g, ref[0] - col[0] * g
    return gain, offset


def even_band(page, band, fold_on_left):
    """The page with the washed-out or shaded band next to its fold given
    the page's own tones back: per block of BLOCK_ROWS rows and BAND_COLS
    columns, the band's dark and light levels are mapped onto those of the
    drawing just past the band in the same rows (the washing-out changes
    with the height: the paper curves more at the top and bottom), smoothed
    so the map has no steps. The model is then shown a fold with the right
    tones, which it keeps (shown the washed-out band, it kept it lighter).
    Example: right = even_band(right, 79, fold_on_left=True)"""
    if band < 3:
        return page
    view = (page if fold_on_left else page[:, ::-1]).astype(np.float32)
    h = view.shape[0]
    gain, offset = _band_map(_gray(view[:, :band + BAND_REF_PX + 4]), band)
    rows = (np.arange(h) // BLOCK_ROWS).clip(0, len(gain) - 1)
    smooth = lambda m: cv2.GaussianBlur(m[rows].astype(np.float32), (0, 0), sigmaX=BAND_COLS, sigmaY=BLOCK_ROWS / 2)  # noqa: E731
    fade = np.clip((band - np.arange(band)) / BAND_FADE, 0, 1)[None, :]   # 1 at the fold, 0 at the band's end
    gain, offset = 1 + (smooth(gain) - 1) * fade, smooth(offset) * fade
    view[:, :band, :3] = view[:, :band, :3] * gain[:, :, None] + offset[:, :, None]
    out = np.clip(view + 0.5, 0, 255).astype(np.uint8)
    return out if fold_on_left else out[:, ::-1]


def join_pages(left, right, dy, gap, eat=(EAT, EAT), theta=0.0):
    """(joined picture, strip mask, {'x0','x1','top','right_x'}): the rows both
    pages have, the right page turned by theta and drawn over the left where
    the turn moves it past its edge, the gap between them empty, the strip to
    redraw marked."""
    top = max(0, dy)                              # first left row kept
    rows = min(left.shape[0] - top, right.shape[0] - max(0, -dy))
    lw, rw = left.shape[1], right.shape[1]
    joined = np.full((rows, lw + gap + rw, left.shape[2]), 255, np.uint8)
    joined[:, :lw] = left[top:top + rows]
    r0 = max(0, -dy)
    if theta:
        turned, valid, pad = turn_page(right, theta)
        x0 = lw + gap - pad
        src, ok = turned[r0:r0 + rows, max(0, -x0):], valid[r0:r0 + rows, max(0, -x0):] > 0
        dest = joined[:, max(0, x0):max(0, x0) + src.shape[1]]
        dest[ok[:, :dest.shape[1]]] = src[:, :dest.shape[1]][ok[:, :dest.shape[1]]]
    else:
        joined[:, lw + gap:] = right[r0:r0 + rows]
    mask = np.zeros((rows, lw + gap + rw), np.uint8)
    mask[:, max(0, lw - eat[0]):lw + gap + eat[1]] = 255
    if gap:                                       # start the gap from its two edges
        joined[:, lw:lw + gap, :3] = cv2.inpaint(joined[..., :3], mask, 3, cv2.INPAINT_TELEA)[:, lw:lw + gap]
    return joined, mask, {"x0": max(0, lw - eat[0]), "x1": lw + gap + eat[1], "top": top, "right_x": lw + gap}


def _tiles(height, width, x_mid):
    x0 = int(min(max(0, x_mid - TILE // 2), max(0, width - TILE)))
    step = TILE - TILE_OVERLAP
    tops = list(range(0, max(1, height - TILE + 1), step))
    if tops[-1] + TILE < height:
        tops.append(height - TILE)
    return [(x0, max(0, t), min(width, x0 + TILE), min(height, t + TILE)) for t in tops]


def _tones(gray, x0, x1):
    return np.percentile(gray[:, x0:x1], (8, 30, 50, 70), axis=0)


def fold_visibility(tile, x0, x1, fold):
    """(band, line): how much the strip x0..x1 of a tile differs in tone
    from the columns beside it, and how strong the vertical step at the fold
    column is (grey levels, averaged over the tile's rows)."""
    gray = _gray(tile)
    beside = np.hstack([gray[:, max(0, x0 - 70):max(1, x0 - 6)], gray[:, x1 + 6:x1 + 70]])
    normal = np.median(np.percentile(beside, (8, 30, 50, 70), axis=0), axis=1)
    band = float(np.mean(np.abs(_tones(gray, x0, x1) - normal[:, None])))
    near = cv2.blur(gray, (3, 1))[:, max(0, fold - 6):fold + 7]
    line = float(np.abs((near[:, 4:] - near[:, :-4]).mean(axis=0)).max())
    return band, line


def fold_left(before, after, x0, x1, fold):
    """Share (0 = gone, 1 = untouched) of the fold still visible in `after`.
    Measured on Soul of Gold 056-057: 0.2-0.6 when the model removed it,
    0.9 when it returned the tile almost unchanged."""
    band0, line0 = fold_visibility(before, x0, x1, fold)
    band1, line1 = fold_visibility(after, x0, x1, fold)
    return max(band1 / band0 if band0 > 8 else 0.0, line1 / line0 if line0 > 8 else 0.0)


def seamless(base, gen, sel):
    """gen's pixels inside the selection, blended into base by Poisson
    editing (cv2.seamlessClone): the model's drawing is kept, its tones are
    bent to meet base at the selection's edge. Without it the model's
    whiter paper showed as a faint line along the strip.
    Example: tile = seamless(tile, model_out, selection)"""
    m = cv2.dilate((sel > 0).astype(np.uint8) * 255, np.ones((3, 3), np.uint8))
    if not m.any():
        return base
    p = SEAMLESS_PAD
    pad = lambda a, v: cv2.copyMakeBorder(a, p, p, p, p, cv2.BORDER_REFLECT if v is None else cv2.BORDER_CONSTANT, value=v)  # noqa: E731
    ys, xs = np.nonzero(m)
    centre = ((int(xs.min()) + int(xs.max()) + 1) // 2 + p, (int(ys.min()) + int(ys.max()) + 1) // 2 + p)
    try:
        out = cv2.seamlessClone(pad(gen, None), pad(base, None), pad(m, 0), centre, cv2.NORMAL_CLONE)
    except cv2.error:                               # degenerate selection: plain feathered paste
        return qwen_fill_blend(base, gen, sel)
    return out[p:-p, p:-p]


def qwen_fill_blend(base, gen, sel):
    import qwen_fill
    return qwen_fill.blend(base, gen, sel, FEATHER)


def _repair_tile(crop, sel, fold):
    """(share of the fold left, tile with its strip redrawn). Up to TRIES
    passes, each on the best result so far: a faint line one pass leaves is
    what the next one is shown and takes away."""
    import qwen_fill
    xs = np.where(sel.any(axis=0))[0]
    best = (2.0, crop)          # the score misreads busy art (084-085: ~1.0 on passes that look right), so a pass is always taken
    for _ in range(TRIES):
        gen = seamless(best[1], qwen_fill.fill(best[1], sel, qwen_fill.FOLD_PROMPT), sel)
        left = fold_left(crop, gen, int(xs[0]), int(xs[-1]) + 1, fold)
        best = min(best, (left, gen), key=lambda t: t[0])
        if best[0] <= GOOD_ENOUGH:
            break
    return best


def repair_seam(joined, mask, fold_x, use_ai=True, progress=None):
    """(picture with the strip redrawn, method 'qwen' | 'lama' | 'telea').
    The model sees the strip as scanned (fold line at column fold_x,
    washed-out band, the gap started from its two edges) and is asked to
    take the fold away."""
    import inpaint_lama
    import qwen_fill
    out = joined.copy()
    bgr = np.ascontiguousarray(out[..., :3])
    xs = np.where(mask.any(axis=0))[0]
    if not len(xs):
        return out, "none"
    if not (use_ai and qwen_fill.available()):
        out[..., :3] = inpaint_lama.lama_or_telea(bgr, mask)
        return out, "lama" if inpaint_lama.available() else "telea"
    tiles = _tiles(bgr.shape[0], bgr.shape[1], (xs[0] + xs[-1] + 1) // 2)
    done_to = 0
    for k, (x0, y0, x1, y1) in enumerate(tiles):
        left, tile = _repair_tile(bgr[y0:y1, x0:x1].copy(), mask[y0:y1, x0:x1], fold_x - x0)
        # cross-fade over the rows the previous tile already drew: no step between tiles
        alpha = np.ones((y1 - y0, 1, 1), np.float32)
        overlap = max(0, done_to - y0)
        alpha[:overlap, 0, 0] = np.linspace(0, 1, overlap, dtype=np.float32)
        old = bgr[y0:y1, x0:x1].astype(np.float32)
        bgr[y0:y1, x0:x1] = np.clip(old * (1 - alpha) + tile * alpha + 0.5, 0, 255).astype(np.uint8)
        done_to = y1
        if progress:
            progress(k + 1, len(tiles), left)
    out[..., :3] = bgr
    return out, "qwen"


def join_spread(left, right, dy=None, gap=None, eat=EAT, use_ai=True, progress=None, theta=None, max_dy=None):
    """Steps 1-3 for two page images (BGR or BGRA, left page first).
    Returns (picture, info dict with theta, dy, gap, bands, x0, x1, top,
    right_x, method (of the repair), align (of the alignment), joined, mask).
    Example: pic, info = join_spread(cv2.imread('057.png'), cv2.imread('056.png'))"""
    found = find_alignment(left, right, max_dy=max_dy) if None in (dy, gap, theta) else {}
    dy = found["dy"] if dy is None else dy
    gap = found["gap"] if gap is None else gap
    theta = found.get("theta", 0.0) if theta is None else theta
    align = found.pop("method", "given")
    bands = [fold_band(left, fold_on_left=False), fold_band(right, fold_on_left=True)]
    left, right = even_band(left, bands[0], fold_on_left=False), even_band(right, bands[1], fold_on_left=True)
    strip = (min(bands[0], MODEL_BAND) + eat, min(bands[1], MODEL_BAND) + eat)
    joined, mask, where = join_pages(left, right, dy, gap, strip, theta)
    fixed, method = repair_seam(joined, mask, where["right_x"] - gap // 2, use_ai, progress)
    return fixed, {**found, "theta": theta, "dy": dy, "gap": gap, "align": align, "bands": bands, **where,
                   "method": method, "joined": joined, "mask": mask}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("left"); ap.add_argument("right"); ap.add_argument("out")
    ap.add_argument("--theta", type=float); ap.add_argument("--dy", type=int); ap.add_argument("--gap", type=int)
    ap.add_argument("--eat", type=int, default=EAT)
    ap.add_argument("--no-ai", action="store_true")
    a = ap.parse_args()
    left, right = (cv2.imread(p, cv2.IMREAD_UNCHANGED) for p in (a.left, a.right))
    if left is None or right is None:
        sys.exit(f"cannot read {a.left if left is None else a.right} (expected an image file)")
    pic, info = join_spread(left[..., :3], right[..., :3], a.dy, a.gap, a.eat, not a.no_ai,
                            lambda i, n, left: print(f"  seam tile {i}/{n}: fold left {left:.2f}", flush=True),
                            theta=a.theta)
    cv2.imwrite(a.out, pic)
    cv2.imwrite(os.path.splitext(a.out)[0] + "_joined.png", info.pop("joined"))
    info.pop("mask")
    print(json.dumps(info))


if __name__ == "__main__":
    main()
