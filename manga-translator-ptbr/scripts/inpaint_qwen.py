#!/usr/bin/env python3
"""Text erased over ART by GIMPhoto's Generative Fill process: each text is
selected and a local Qwen-Image-Edit (ComfyUI) is asked to erase it - what
"select the text, type erase text" does in GIMPhoto, automated (qwen_fill.py
runs GIMPhoto's own graph). It redraws what is behind the lettering (hair,
faces, armor, hatching) where LaMa (inpaint_lama.py) smears.

inpaint_lama.inpaint() dispatches here whenever the ComfyUI server and the
model are available (INPAINT=lama or telea opts out) and uses LaMa otherwise.

Same contract as inpaint_lama: only pixels of the selection change (the
erased text plus SELECT_GROW px around it); everything else stays byte-exact.

How a page is processed:
  - the mask is grouped into regions (GROUP_PX), specks <= SMALL_PX use
    cv2.inpaint like LaMa does
  - each region becomes a selection: the rectangle around its strokes grown
    by SELECT_GROW, as drawn by hand around the text in GIMPhoto
  - regions are packed greedily into crops (region bbox + CONTEXT_FRAC
    context) as long as a crop still reaches the model at >= MIN_SCALE of its
    source resolution; a normal page is one or two crops, a 70 MP scan several
  - each crop goes through qwen_fill.fill() with ERASE_PROMPT: the model sees
    the picture, samples only inside the selections (4 steps, Lightning LoRA)
    and only the selections' pixels (feathered) replace the original
  - erase_boxes(): the same for boxes given by hand (the box is the selection)
  - check: the text detector reads the result; a region where it still finds
    lettering (the model sometimes redraws the characters, or others) is run
    again with a new seed, up to TRIES times, then goes to LaMa. A region the
    model barely changed (mean change < MISS_DIFF) counts as not erased too,
    and so does one it returned as a flat patch (_flat).
  - routing (INPAINT_ROUTE=auto, the default): a region whose surroundings are
    flat screentone - many halftone dots (DOTS_FRAC) and almost no line
    structure once the dots are blurred away (EDGE_FRAC) - goes to LaMa, which
    continues a dot pattern better than the model (measured on Golden Age
    pages: the model leaves a flat grey patch there). Everything else - art,
    faces, armor, gradients, plain tone - goes to the model.
    INPAINT_ROUTE=qwen sends every region to the model.

About 100 s per crop on a 6 GB GPU. Server and model files: see qwen_fill.py.

Standalone use (debug):
    python inpaint_qwen.py <image> <mask.png> <out.png>
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qwen_fill  # noqa: E402

GROUP_PX = 48        # glyphs closer than this form one region
CONTEXT_FRAC = 0.6   # context around a region, as a fraction of its size
MIN_CTX = 96         # ...but at least this many px
MIN_SCALE = 0.8      # a crop may reach the model at no less than this scale; else split (0.5 blurred big lettering)
MODEL_MP = 1.0       # megapixels the model works at (GIMPhoto's WORK_PIXELS)
SELECT_GROW = 8      # px the strokes are grown into the selection
SMALL_PX = 32        # regions no bigger than this use cv2.inpaint
MISS_DIFF = 12.0     # mean |change| (0-255) inside a region below which the text was not erased
TEXT_LEFT = 0.25     # share of a region's stroke pixels the detector may still call text afterwards
SMALL_VIEW = 1024    # px side of the canvas a crop is also read on at half size
FLAT_RATIO = 0.5     # a region with less than this share of its surroundings' detail is a flat patch
FLAT_MIN_DETAIL = 4.0  # ...unless the surroundings themselves are smooth (a plain background)
RING_FLAT = 61       # px of surroundings the detail is compared with
TRIES = 3            # model runs per crop before its unfinished regions go to LaMa
ROUTE = os.environ.get("INPAINT_ROUTE", "auto").lower()   # auto | qwen
DOTS_FRAC = 0.70     # >= this share of the ring is halftone dots...
EDGE_FRAC = 0.25     # ...and <= this share holds a line/edge -> flat screentone -> LaMa
RING_IN, RING_OUT = 11, 81   # the neighbourhood a region is judged on (px)

_detector = {}


def available():
    return qwen_fill.available()


def selection_of(strokes):
    """Selection around a stroke mask (uint8): one rectangle per group of
    glyphs (strokes closer than GROUP_PX), grown by SELECT_GROW - what is
    drawn by hand in GIMPhoto. The detector often marks only part of big
    outlined lettering (084-085's 什么?!); shown only those parts, the model
    redrew the rest, three runs in a row."""
    sel = np.zeros_like(strokes)
    grouped = cv2.dilate(strokes, np.ones((2 * GROUP_PX + 1,) * 2, np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    h, w = strokes.shape[:2]
    for i in range(1, n):
        gx, gy, gw, gh, _ = stats[i]
        ys, xs = np.nonzero(strokes[gy:gy + gh, gx:gx + gw] & (labels[gy:gy + gh, gx:gx + gw] == i))
        if len(xs):
            x0, y0 = max(0, gx + xs.min() - SELECT_GROW), max(0, gy + ys.min() - SELECT_GROW)
            x1, y1 = min(w, gx + xs.max() + 1 + SELECT_GROW), min(h, gy + ys.max() + 1 + SELECT_GROW)
            sel[y0:y1, x0:x1] = 255
    return sel


def _detect(crop):
    if not _detector:
        import clean_blocks
        import detect_text
        _detector.update(run=clean_blocks.seg_mask_for_crop, sess=detect_text.make_session())
    return _detector["run"](_detector["sess"], crop)


def _strokes_seen(crop):
    """The text detector's stroke mask for a crop (None when it cannot run).
    The detector is blind to lettering much bigger than it is on a whole
    page (a 300 px sound effect in a tight crop), so the crop is also read
    at half size on a page-sized white canvas and both answers are joined."""
    try:
        seen = _detect(crop)
        h, w = crop.shape[:2]
        if max(h, w) <= 2 * SMALL_VIEW:
            small = cv2.resize(crop, ((w + 1) // 2, (h + 1) // 2), interpolation=cv2.INTER_AREA)
            page = np.full((SMALL_VIEW, SMALL_VIEW, 3), 255, np.uint8)
            page[:small.shape[0], :small.shape[1]] = small
            half = _detect(page)[:small.shape[0], :small.shape[1]]
            seen = seen | cv2.resize(half, (w, h), interpolation=cv2.INTER_NEAREST)
        return seen
    except Exception as e:  # noqa: BLE001 - the check is optional, the erase is not
        print(f"inpaint_qwen: text check unavailable ({e})", file=sys.stderr)
        return None


def _detail(gray):
    """Fine detail per pixel: |gray - blurred gray| (grain, dots, lines)."""
    return np.abs(gray - cv2.GaussianBlur(gray, (0, 0), 2.0))


def _flat(detail, region):
    """True when a region came back as a flat patch: less than FLAT_RATIO of
    the fine detail of the RING_FLAT px around it. GIMPhoto's client checks
    the same (FLAT_RATIO there); with a random seed Qwen sometimes returns
    one for a big selection. Measured on 084-085: flat patches 0.15-0.36,
    a good erase over a starfield 0.72."""
    ring = (cv2.dilate(region.astype(np.uint8), np.ones((RING_FLAT,) * 2, np.uint8)) > 0) & ~region
    around = float(detail[ring].mean()) if ring.any() else 0.0
    return around > FLAT_MIN_DETAIL and float(detail[region].mean()) < FLAT_RATIO * around


def _unfinished(crop, gen, sel, strokes):
    """Labels of the selection's regions where gen still shows text, or
    came back as a flat patch."""
    n, lab = cv2.connectedComponents((sel > 0).astype(np.uint8))
    diff = np.abs(crop.astype(np.int16) - gen.astype(np.int16)).max(axis=2)
    seen = _strokes_seen(gen)
    detail = _detail(cv2.cvtColor(gen, cv2.COLOR_BGR2GRAY).astype(np.float32))
    bad = []
    for r in range(1, n):
        region = lab == r
        was = region & (strokes > 0)
        if not was.any():
            continue
        left = 0.0 if seen is None else float(((seen > 0) & was).sum()) / was.sum()
        if diff[was].mean() < MISS_DIFF or left > TEXT_LEFT or _flat(detail, region):
            bad.append(r)
    return lab, bad


def _erase_crop(crop, strokes, sel=None):
    """(crop with the text erased, mask of strokes the model could not erase).
    sel: the selection to repaint (default: selection_of(strokes)). The
    prompt alternates between runs: on a run that failed, the other wording
    often works (ERASE_PROMPT for drawings, GIMPhoto's CLEAN_PROMPT for
    textures and paper)."""
    sel = selection_of(strokes) if sel is None else sel
    prompts = (qwen_fill.ERASE_PROMPT, qwen_fill.gimphoto.CLEAN_PROMPT)
    out, todo = crop.copy(), sel.copy()
    for attempt in range(TRIES):
        gen = qwen_fill.fill(out, todo, prompts[attempt % 2])
        lab, bad = _unfinished(out, gen, todo, strokes)
        done = todo.copy()
        done[np.isin(lab, bad)] = 0
        out = qwen_fill.blend(out, gen, done)
        todo[done > 0] = 0
        if not bad:
            break
        print(f"inpaint_qwen: {len(bad)} region(s) still show text or came back flat after run {attempt + 1}",
              file=sys.stderr)
    return out, strokes & todo


def erase_boxes(img, boxes):
    """img with the text inside each box (x, y, w, h) erased by the model:
    the box is the selection, as when it is drawn by hand in GIMPhoto - for
    lettering the detector does not find on the page. What the model cannot
    erase in TRIES runs goes to LaMa. Returns a new image.
    Example: erase_boxes(page, [(3130, 1090, 345, 330)])"""
    import inpaint_lama
    H, W = img.shape[:2]
    out = img.copy()
    for x, y, w, h in boxes:
        box = (max(0, x), max(0, y), min(W, x + w), min(H, y + h))
        (x0, y0, x1, y1), _ = _crops([box], W, H)[0]
        sel = np.zeros((y1 - y0, x1 - x0), np.uint8)
        sel[box[1] - y0:box[3] - y0, box[0] - x0:box[2] - x0] = 255
        crop = out[y0:y1, x0:x1]
        seen = _strokes_seen(crop)
        strokes = sel if seen is None or not (seen & sel).any() else seen & sel
        out[y0:y1, x0:x1], left = _erase_crop(crop, strokes, sel)
        if left.any():
            out[y0:y1, x0:x1] = inpaint_lama.lama_or_telea(out[y0:y1, x0:x1], left)
    return out


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


def _split(mask, W, H):
    """(labels, region boxes, their labels) of the mask's regions; None, [], []
    when there are none. Specks are returned as boxes with label < 0."""
    grouped = cv2.dilate(mask, np.ones((2 * GROUP_PX + 1,) * 2, np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    boxes, ids = [], []
    for i in range(1, n):
        gx, gy, gw, gh, _ = stats[i]
        sub = mask[gy:gy + gh, gx:gx + gw] & ((labels[gy:gy + gh, gx:gx + gw] == i).astype(np.uint8) * 255)
        ys, xs = np.where(sub > 0)
        if not len(xs):
            continue
        boxes.append((gx + xs.min(), gy + ys.min(), gx + xs.max() + 1, gy + ys.max() + 1))
        ids.append(i)
    return labels, boxes, ids


def _region_mask(mask, labels, box, label):
    x0, y0, x1, y1 = box
    return mask[y0:y1, x0:x1] & ((labels[y0:y1, x0:x1] == label).astype(np.uint8) * 255)


def _speck(out, mask, labels, box, label, W, H):
    import inpaint_lama
    p = inpaint_lama.TELEA_RADIUS + 2
    x0, y0, x1, y1 = max(0, box[0] - p), max(0, box[1] - p), min(W, box[2] + p), min(H, box[3] + p)
    wm = _region_mask(mask, labels, (x0, y0, x1, y1), label)
    out[y0:y1, x0:x1] = cv2.inpaint(out[y0:y1, x0:x1], wm, inpaint_lama.TELEA_RADIUS, cv2.INPAINT_TELEA)


def inpaint(img, mask, progress=None, inplace=False, deadline=None, pending=None):
    """img BGR uint8, mask uint8 (>0 = erase). Same contract as
    inpaint_lama.inpaint_lama (deadline/pending included). Regions the model
    fails on are handed to LaMa."""
    import inpaint_lama
    H, W = img.shape[:2]
    out = img if inplace else img.copy()
    mask = (mask > 0).astype(np.uint8) * 255
    if pending is not None:
        pending[:] = 0
    labels, boxes, ids = _split(mask, W, H)
    fallback = np.zeros((H, W), np.uint8)
    regions, big = [], []
    for box, label in zip(boxes, ids):
        if max(box[2] - box[0], box[3] - box[1]) <= SMALL_PX:
            _speck(out, mask, labels, box, label, W, H)
            continue
        if ROUTE == "auto":
            rm = np.zeros((H, W), np.uint8)
            rm[box[1]:box[3], box[0]:box[2]] = _region_mask(mask, labels, box, label)
            if _flat_screentone(out, rm):
                fallback |= rm
                continue
        regions.append(box)
        big.append(label)
    if fallback.any():
        print("inpaint_qwen: flat-screentone region(s) -> LaMa", file=sys.stderr)
    crops = _crops(regions, W, H)
    for k, ((x0, y0, x1, y1), idx) in enumerate(crops):
        cmask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        for j in idx:
            rx0, ry0, rx1, ry1 = regions[j]
            cmask[ry0 - y0:ry1 - y0, rx0 - x0:rx1 - x0] |= _region_mask(mask, labels, regions[j], big[j])
        if deadline is not None and time.time() > deadline:
            pending[y0:y1, x0:x1] |= cmask
            continue
        try:
            out[y0:y1, x0:x1], left = _erase_crop(out[y0:y1, x0:x1], cmask)
        except (qwen_fill.gimphoto.ComfyUIError, OSError) as e:
            print(f"inpaint_qwen: {str(e).splitlines()[0]} - LaMa for this crop", file=sys.stderr)
            left = cmask
        fallback[y0:y1, x0:x1] |= left
        if progress:
            progress(k + 1, len(crops))
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
