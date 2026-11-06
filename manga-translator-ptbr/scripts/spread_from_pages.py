#!/usr/bin/env python3
"""Rebuild double-page spreads that were wrongly divided into two PSDs.

Input: a `split_scans.py --also-images --spreads STEM ...` output (the spread
cut from the scan, `images/STEM.png` fixed + `originals/STEM.png` as scanned)
and the folder of the already translated page PSDs (NNN.psd + MMM.psd for
STEM NNN-MMM). Output: `<out>/NNN-MMM.psd` with Original (the spread as
scanned), Copy (the fixed spread, with the text-erased pixels of the old pages
carried over) and every old text layer moved to where its art now sits, named
Text 1..n in reading order (first-number page first).

How the old pages are placed: SIFT features of each old page's Original are
matched against the spread (similarity transform, RANSAC); the spread is
resized by the mean scale so the old text boxes keep their size and only slide.

Usage: spread_from_pages.py <split dir> <pages dir> <out dir> [--only STEM ...] [--join STEM ...]
(--join: stems whose scan cannot be re-cut - dark art to the paper edge - are built by putting
the two old pages side by side; the left page is the second number unless --first-is-left)
Example: spread_from_pages.py ~/Downloads/x\\ spreads ~/Nextcloud/.../Portuguese ~/Downloads/x\\ double --only 016-017
"""
import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PSD_IO = os.path.join(REPO, "split-scans", "scripts", "psd_io.mjs")
NODE = os.environ.get("NODE", "node")
CLEAN_DIFF = 24      # max channel difference between an old Original and its Copy = erased text
MIN_INLIERS = 25


def dump_psd(path, work):
    """Dump a PSD with split-scans' psd_io; returns its layers.json."""
    os.makedirs(work, exist_ok=True)
    subprocess.run([NODE, "--max-old-space-size=4096", PSD_IO, "dump", path, work], check=True, capture_output=True)
    return json.load(open(os.path.join(work, "layers.json")))


def on_canvas(png, width, height, left, top):
    """The layer PNG placed on a white BGR canvas of the PSD's size."""
    lay = cv2.imread(png, cv2.IMREAD_UNCHANGED)
    canvas = np.full((height, width, 3), 255, np.uint8)
    h, w = lay.shape[:2]
    x0, y0, x1, y1 = max(0, left), max(0, top), min(width, left + w), min(height, top + h)
    if x1 > x0 and y1 > y0:
        canvas[y0:y1, x0:x1] = lay[y0 - top:y1 - top, x0 - left:x1 - left, :3]
    return canvas


def _sift_match(old_gray, new_gray, contrast, ratio):
    """Similarity transform old -> new from SIFT matches, or None."""
    sift = cv2.SIFT_create(8000, contrastThreshold=contrast)
    k1, d1 = sift.detectAndCompute(old_gray, None)
    k2, d2 = sift.detectAndCompute(new_gray, None)
    if d1 is None or d2 is None:
        return None
    pairs = cv2.BFMatcher().knnMatch(d1, d2, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < ratio * n.distance]
    if len(good) < MIN_INLIERS:
        return None
    src = np.float32([k1[m.queryIdx].pt for m in good]); dst = np.float32([k2[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    return (M, int(inl.sum())) if M is not None and int(inl.sum()) >= MIN_INLIERS else None


def plausible(M):
    """Old pages and the spread come from the same scan: scale ~1, rotation of a degree or two."""
    ang = abs(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    return 0.9 <= float(np.sqrt(abs(np.linalg.det(M[:, :2])))) <= 1.1 and ang <= 3.0


def _template_ecc(old_bgr, new_bgr):
    """Fallback for flat pages (water, sky, screentone) with no usable features:
    find the old page's centre in the spread by template matching at 1/4 size,
    then refine rotation+translation with ECC at 1/2 size. Returns (M, -cc*100) or None."""
    g = lambda im, f: cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    h, w = old_bgr.shape[:2]
    x0, y0, x1, y1 = int(.2 * w), int(.2 * h), int(.8 * w), int(.8 * h)
    q = 0.25
    res = cv2.matchTemplate(g(new_bgr, q), g(old_bgr[y0:y1, x0:x1], q), cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    tx, ty = loc[0] / q - x0, loc[1] / q - y0
    half = 0.5
    M = np.array([[1, 0, tx * half], [0, 1, ty * half]], np.float32)
    try:
        cc, M = cv2.findTransformECC(g(new_bgr, half), g(old_bgr, half), M, cv2.MOTION_EUCLIDEAN,
                                     (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
    except cv2.error:
        return None
    M = M.astype(np.float64); M[:, 2] /= half          # ECC ran at half size: back to full size
    return (M, -int(cc * 100)) if cc > 0.5 else None


def register(old_bgr, new_bgr):
    """Affine 2x3 mapping old-page pixels onto the spread, and a quality number
    (SIFT inliers; negative = ECC correlation x100). SIFT first, then a more
    tolerant SIFT on contrast-equalised images, then template matching + ECC;
    a transform that is not plausible for one scan (see `plausible`) is rejected."""
    gray = lambda im: cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    for eq, contrast, ratio in ((False, 0.04, 0.75), (True, 0.02, 0.8), (True, 0.01, 0.85)):
        prep = (lambda im: clahe.apply(gray(im))) if eq else gray
        found = _sift_match(prep(old_bgr), prep(new_bgr), contrast, ratio)
        if found and plausible(found[0]):
            return found
    found = _template_ecc(old_bgr, new_bgr)
    if found and plausible(found[0]):
        return found
    raise RuntimeError("no plausible registration between an old page and the spread (SIFT x3, template+ECC)")


def quality_text(q):
    return f"{q} inliers" if q >= 0 else f"ECC correlation {-q / 100:.2f}"


def scale_of(M):
    return float(np.sqrt(abs(np.linalg.det(M[:, :2]))))


def carry_clean_pixels(copy_img, old_orig, old_copy, M, size):
    """Paste into copy_img the pixels the old page's Copy erased (text), warped by M."""
    diff = np.abs(old_orig.astype(int) - old_copy.astype(int)).max(axis=2) > CLEAN_DIFF
    if not diff.any():
        return 0
    mask = cv2.dilate(diff.astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
    wm = cv2.warpAffine(mask, M, size, flags=cv2.INTER_LINEAR) > 127
    wc = cv2.warpAffine(old_copy, M, size, flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    copy_img[wm] = wc[wm]
    return int(wm.sum())


def box_center(layer):
    t = layer["text"]
    a, b, c, d, tx, ty = [float(v) for v in (t.get("transform") or [1, 0, 0, 1, layer["left"], layer["top"]])]
    bb = t.get("boxBounds") or [0, 0, layer["right"] - layer["left"], layer["bottom"] - layer["top"]]
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    return a * cx + c * cy + tx, b * cx + d * cy + ty


def moved_text_layer(layer, M, name):
    """The text layer slid to where its centre lands after the old page is mapped by M."""
    cx, cy = box_center(layer)
    nx, ny = M @ np.array([cx, cy, 1.0])
    dx, dy = float(nx - cx), float(ny - cy)
    text = dict(layer["text"])
    tr = [float(v) for v in (text.get("transform") or [1, 0, 0, 1, layer["left"], layer["top"]])]
    tr[4] += dx; tr[5] += dy
    text["transform"] = tr
    out = {k: layer[k] for k in ("opacity", "hidden", "blendMode") if k in layer}
    out.update({"kind": "text", "name": name, "text": text,
                "left": int(round(layer["left"] + dx)), "right": int(round(layer["right"] + dx)),
                "top": int(round(layer["top"] + dy)), "bottom": int(round(layer["bottom"] + dy))})
    return out


def old_page(pages_dir, stem, work):
    """(original BGR, copy BGR, text layers) of one old page PSD."""
    info = dump_psd(os.path.join(pages_dir, stem + ".psd"), os.path.join(work, stem))
    rasters = [L for L in info["layers"] if L["kind"] == "raster"]
    if len(rasters) < 2:
        raise RuntimeError(f"{stem}.psd has {len(rasters)} raster layer(s), expected Original + Copy")
    W, H = info["width"], info["height"]
    orig, copy = (on_canvas(L["png"], W, H, L["left"], L["top"]) for L in (rasters[0], rasters[-1]))
    return orig, copy, [L for L in info["layers"] if L["kind"] == "text"]


def write_spread_psd(stem, original, copy_img, texts, out_dir, work):
    """Original + Copy + text layers -> <out_dir>/<stem>.psd (and the Copy PNG, for previews)."""
    os.makedirs(work, exist_ok=True); os.makedirs(out_dir, exist_ok=True)
    spec = {"out": os.path.join(out_dir, stem + ".psd"), "width": original.shape[1], "height": original.shape[0], "layers": []}
    for name, im in (("Original", original), ("Copy", copy_img)):
        png = os.path.join(work, f"{stem}_{name}.png"); cv2.imwrite(png, im)
        spec["layers"].append({"kind": "raster", "name": name, "png": png, "left": 0, "top": 0})
    spec["layers"] += texts
    cv2.imwrite(os.path.join(work, stem + "_copy.png"), copy_img)
    spec_path = os.path.join(work, stem + "_spec.json"); json.dump(spec, open(spec_path, "w"))
    subprocess.run([NODE, "--max-old-space-size=4096", PSD_IO, "build", spec_path], check=True, capture_output=True)


def join_old_pages(stem, pages_dir, out_dir, work, first_is_left):
    """Fallback when the scan cannot be re-cut (dark art to the paper edge): the two
    old pages side by side, Original|Original and Copy|Copy, text boxes slid by the
    left page's width. Returns a log line."""
    names = stem.split("-")
    pages = [old_page(pages_dir, n, os.path.join(work, stem)) for n in names]
    order = [0, 1] if first_is_left else [1, 0]            # indices of the left and right page
    height = max(pages[i][0].shape[0] for i in order)
    pad = lambda im: np.vstack([im, np.full((height - im.shape[0], im.shape[1], 3), 255, np.uint8)]) if im.shape[0] < height else im
    orig, copy = (np.hstack([pad(pages[i][k]) for i in order]) for k in (0, 1))
    shift = {order[1]: float(pages[order[0]][0].shape[1]), order[0]: 0.0}
    texts = []
    for i in (0, 1):
        M = np.array([[1, 0, shift[i]], [0, 1, 0]], np.float64)
        texts += [moved_text_layer(L, M, f"Text {len(texts) + j + 1}") for j, L in enumerate(pages[i][2])]
    write_spread_psd(stem, orig, copy, texts, out_dir, os.path.join(work, stem))
    return f"{stem}: {orig.shape[1]}x{orig.shape[0]}, {len(texts)} text layer(s); JOINED from the old pages (left page {names[order[0]]})"


def build_spread(stem, split_dir, pages_dir, out_dir, work):
    """One spread PSD; returns a log line."""
    fixed = cv2.imread(os.path.join(split_dir, "images", stem + ".png"), cv2.IMREAD_COLOR)
    scanned = cv2.imread(os.path.join(split_dir, "originals", stem + ".png"), cv2.IMREAD_COLOR)
    names = stem.split("-")
    pages = [old_page(pages_dir, n, work) for n in names]
    regs = [register(p[0], scanned) for p in pages]
    mean_scale = float(np.mean([scale_of(m) for m, _ in regs]))
    k = 1.0 / mean_scale
    size = (int(round(fixed.shape[1] * k)), int(round(fixed.shape[0] * k)))
    fixed, scanned = (cv2.resize(im, size, interpolation=cv2.INTER_CUBIC) for im in (fixed, scanned))
    copy_img = fixed.copy()
    texts, notes = [], []
    for (orig, copy, tl), (M, inl) in zip(pages, regs):
        Mk = M * k
        notes.append(f"{quality_text(inl)}, scale {scale_of(Mk):.3f}, angle {np.degrees(np.arctan2(Mk[1, 0], Mk[0, 0])):.2f}")
        carry_clean_pixels(copy_img, orig, copy, Mk, size)
        texts += [moved_text_layer(L, Mk, f"Text {len(texts) + i + 1}") for i, L in enumerate(tl)]
    write_spread_psd(stem, scanned, copy_img, texts, out_dir, work)
    return f"{stem}: {size[0]}x{size[1]}, {len(texts)} text layer(s); {names[0]}: {notes[0]} | {names[1]}: {notes[1]}"


def exact_offset(M):
    """M with the identity matrix and whole-pixel offsets when it is only a translation (the old pages are
    exact crops of the raw scan), so pasted pixels are copied, not resampled."""
    ang = np.degrees(np.arctan2(M[1, 0], M[0, 0]))
    if abs(scale_of(M) - 1) < 0.002 and abs(ang) < 0.05:
        out = np.array([[1.0, 0.0, round(M[0, 2])], [0.0, 1.0, round(M[1, 2])]])
        return out
    return M


def build_from_raw_scan(stem, scans_dir, pages_dir, out_dir, work, use_cleaned=False):
    """The spread IS the raw scan: the old pages are exact crops of it (scale 1, no rotation), so the
    scan stays untouched as Original, the translated boxes are moved by the offset each old page sits
    at, and Copy = the scan with the text-erased pixels of the old pages' Copy layers pasted at their
    offsets (their cleaning is kept as it was). With use_cleaned, Copy is instead
    `<out>/final/<stem>_cleaned.png` made by clean_blocks.py on `<out>/final/<stem>_detect.json`
    (written here: text blocks = the moved boxes)."""
    scan_path = next(os.path.join(scans_dir, f) for f in sorted(os.listdir(scans_dir)) if f.startswith(stem + "."))
    scan = cv2.imread(scan_path, cv2.IMREAD_COLOR)
    names = stem.split("-")
    pages = [old_page(pages_dir, n, work) for n in names]
    regs = [register(p[0], scan) for p in pages]
    size = (scan.shape[1], scan.shape[0])
    copy_img = scan.copy()
    texts, notes = [], []
    for (orig, copy, tl), (M, q) in zip(pages, regs):
        M = exact_offset(M)
        notes.append(f"{quality_text(q)}, scale {scale_of(M):.3f}, offset {M[0, 2]:.0f},{M[1, 2]:.0f}")
        if not use_cleaned:
            carry_clean_pixels(copy_img, orig, copy, M, size)
        texts += [moved_text_layer(L, M, f"Text {len(texts) + i + 1}") for i, L in enumerate(tl)]
    state = "PSD written"
    if use_cleaned:
        final = os.path.join(out_dir, "final"); os.makedirs(final, exist_ok=True)
        boxes = [[t["left"], t["top"], t["right"] - t["left"], t["bottom"] - t["top"]] for t in texts]
        json.dump({"source": scan_path, "text_blocks": boxes}, open(os.path.join(final, stem + "_detect.json"), "w"))
        cleaned = os.path.join(final, stem + "_cleaned.png")
        if not os.path.exists(cleaned):
            return f"{stem}: boxes written, run clean_blocks.py"
        copy_img = cv2.imread(cleaned, cv2.IMREAD_COLOR)
    write_spread_psd(stem, scan, copy_img, texts, out_dir, work)
    return f"{stem}: {size[0]}x{size[1]}, {len(texts)} text layer(s), {state}; {names[0]}: {notes[0]} | {names[1]}: {notes[1]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("split_dir"); ap.add_argument("pages_dir"); ap.add_argument("out_dir")
    ap.add_argument("--only", nargs="*", help="spread stems to build (default: every images/*.png)")
    ap.add_argument("--raw-scans", metavar="DIR", help="folder of the raw scans named NNN-MMM.jpg: build on the untouched scan (see build_from_raw_scan); split_dir is then ignored")
    ap.add_argument("--use-cleaned", action="store_true", help="with --raw-scans: Copy from clean_blocks.py (final/<stem>_cleaned.png) instead of the old pages' erased pixels")
    ap.add_argument("--join", nargs="*", default=[], metavar="STEM", help="stems built by joining the old pages instead of re-cutting the scan")
    ap.add_argument("--first-is-left", action="store_true", help="with --join: the first number is the LEFT page (default: right, as in manga)")
    ap.add_argument("--work", help="scratch folder for the intermediate PNGs (kept; default <out>/work)")
    a = ap.parse_args()
    listing = a.raw_scans if a.raw_scans else os.path.join(a.split_dir, "images")
    stems = a.only or sorted(os.path.splitext(f)[0] for f in os.listdir(listing) if f.lower().endswith((".png", ".jpg", ".jpeg")))
    work = a.work or os.path.join(a.out_dir, "work")
    for stem in stems:
        try:
            line = (build_from_raw_scan(stem, a.raw_scans, a.pages_dir, a.out_dir, os.path.join(work, stem), a.use_cleaned) if a.raw_scans else
                    join_old_pages(stem, a.pages_dir, a.out_dir, work, a.first_is_left) if stem in a.join
                    else build_spread(stem, a.split_dir, a.pages_dir, a.out_dir, os.path.join(work, stem)))
            print(line, flush=True)
        except Exception as e:
            print(f"FAIL {stem}: {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
