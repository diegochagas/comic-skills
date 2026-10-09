#!/usr/bin/env python3
"""Split flatbed scans of an open book into one file per page, each page
flattened (perspective + skew), cropped to the paper and with the gutter
shadow turned back into paper. Works on JPG/PNG/TIF/WEBP images and on
layered PSDs (every raster layer is warped the same way; text layers travel
to the page their centre lands on, keeping their fonts and boxes).

Usage:
  split_scans.py <file-or-folder> [--output DIR] [--rtl] [--spread auto|yes|no]
                 [--pages auto|1|2] [--align perspective|rotate|none]
                 [--gutter inpaint|white|flatten|none] [--only STEM ...]
                 [--recursive] [--dry-run] [--review-only] [--force]

Naming: a stem with two consecutive numbers (082-083, 030-31) is a scan of
two pages and becomes 082 + 083 (first number = left page; --rtl makes it
the right page, for scans numbered in reading order); a stem with one
number (000, 264, 000_spine)
is one page: the scanner border is cut, the page flattened, and if the scan
also shows a blank facing page only the page with ink is kept. A two-page
scan whose artwork continues across the spine (a spread) is kept as ONE
file (082-083) with the two halves aligned and the middle rebuilt, unless
--spread no.

Output: ALWAYS a layered PSD per page, <--output>/<page>.psd (default
~/Downloads/<source folder name> split/, COMIC_OUTPUT_DIR replaces
~/Downloads): layer "Original" = the page cut out of the scan, flattened and
cropped but with its gutter shadow as scanned; above it the fixed page
("Copy": shadow flattened + core rebuilt). A PSD input keeps all its layers:
the bottom raster stays the untouched Original, every other raster (the
text-erased Copy) gets the gutter fix, and its text layers move with the
page they sit on. --also-images additionally writes images/<page>.png (the
fixed page) and originals/<page>.png for pipelines that want flat images.
Review sheets in <out>/review/ (source with the detected quads + gutter, and
the resulting pages) and one JSON line per input in <out>/report.jsonl.
Inputs are never modified. JPEGs are processed in their displayed (EXIF)
orientation; --rotate 90|-90|180 turns a scan first.

Gutter: the shadow band is divided by its estimated illumination (paper
comes back white, lines stay); the band's unrecoverable core is inpainted
with LaMa (manga-translator-ptbr's model) or, with --gutter white, filled
white; --gutter flatten skips the core fix, none leaves the shadow.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import page_geometry as G  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
PSD_EXT = {".psd", ".psb"}
NODE = "node"
PSD_IO = os.path.join(HERE, "psd_io.mjs")

_inpaint_mod = None


def inpaint_core(img, mask, mode):
    """Rebuild the masked pixels: LaMa when available (mode 'inpaint'), else white."""
    global _inpaint_mod
    if mode == "inpaint":
        if _inpaint_mod is None:
            sys.path.insert(0, os.path.join(REPO, "manga-translator-ptbr", "scripts"))
            try:
                import inpaint_lama
                _inpaint_mod = inpaint_lama if inpaint_lama.available() else False
            except Exception as e:  # pragma: no cover
                print(f"  inpaint unavailable ({e}); filling white", file=sys.stderr)
                _inpaint_mod = False
        if _inpaint_mod:
            bgr = img[..., :3].copy()
            out = _inpaint_mod.inpaint(bgr, mask)
            res = img.copy(); res[..., :3] = out
            return res, "lama"
    res = img.copy()
    res[mask > 0, :3] = 255
    return res, "white"


def parse_stem(stem):
    """-> (kind, names): ('double', [a, b]) for 'NNN-MMM' with MMM = NNN+1, else ('single', [stem])."""
    m = re.fullmatch(r"(\d+)-(\d+)(.*)", stem)
    if m:
        a, b, rest = m.group(1), m.group(2), m.group(3)
        if int(b) == int(a) + 1:
            if len(b) < len(a):
                b = b.zfill(len(a))
            return "double", [a + rest, b + rest]
    return "single", [stem]


def default_out(src):
    root = os.environ.get("COMIC_OUTPUT_DIR") or os.path.join(os.path.expanduser("~"), "Downloads")
    name = os.path.basename(os.path.abspath(src if os.path.isdir(src) else os.path.dirname(os.path.abspath(src))))
    return os.path.join(root, f"{name} split")


def list_inputs(src, recursive, only):
    files = []
    if os.path.isdir(src):
        for root, dirs, names in os.walk(src):
            dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")) and d not in ("preview", "review", "split"))
            for n in sorted(names):
                ext = os.path.splitext(n)[1].lower()
                if ext in IMG_EXT | PSD_EXT and not n.startswith("."):
                    files.append(os.path.join(root, n))
            if not recursive:
                break
    else:
        files = [src]
    if only:
        files = [f for f in files if os.path.splitext(os.path.basename(f))[0] in only]
    return files


# ---------------------------------------------------------------- analysis

def analyse(img, kind, opts):
    """Plan the output pages of one scan. Returns dict with 'pages': list of
    {name, quad, w, h, inner ('left'|'right'|'middle'|None), band (cols in page
    space), core, spine_x}, 'spread' bool, 'gutter', 'notes'."""
    gray = G.luminance(img)
    H, W = gray.shape
    hsv_s = cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2HSV)[..., 1]
    bg_col, bg, white = G.estimate_background(img[..., :3], gray)
    notes = []
    debug = {}
    no_border = bg > 0.85 * white
    if no_border:
        mask = np.ones((H, W), np.uint8)
        bbox = (0, 0, W, H)
        notes.append("no scanner border (frame as bright as paper): whole image used")
    else:
        mask = G.paper_mask(img[..., :3], gray, bg_col, bg, white)
        bbox = G.bbox_of(mask)
        if bbox is None:
            mask = np.ones((H, W), np.uint8); bbox = (0, 0, W, H)
            notes.append("paper mask empty: whole image used")
    bx0, by0, bx1, by1 = bbox
    bw, bh = bx1 - bx0, by1 - by0
    gutter = None
    want_pages = opts.pages
    if want_pages == "auto":
        landscape = bw > 1.15 * bh
        if kind == "double" or landscape:
            gutter = G.find_gutter(gray, hsv_s, bbox, debug)
    else:
        gutter = G.find_gutter(gray, hsv_s, bbox, debug)
    if gutter is None and want_pages != "1" and (kind == "double" or (want_pages == "auto" and bw > 1.15 * bh)):
        # two pages (by name, or a landscape sheet of paper) with no visible shadow: divide at the middle
        notes.append("no gutter shadow found: divided at the middle of the paper")
        xm = bx0 + bw / 2
        gutter = {"x_top": xm, "x_bot": xm, "x_mid": xm, "depth": 0.0, "band_l": int(xm), "band_r": int(xm),
                  "core_l": int(xm), "core_r": int(xm), "paper": float(np.percentile(gray[by0:by1, bx0:bx1], 95)), "tilt_deg": 0.0}
    two = (kind == "double" and want_pages != "1") or want_pages == "2"
    plan = {"bg": bg, "white": white, "bbox": list(bbox), "gutter": gutter, "notes": notes, "pages": [], "spread": False,
            "debug": debug, "align": opts.align}

    def line_of(g):
        a = (g["x_bot"] - g["x_top"]) / max(1.0, (by1 - by0))
        b = g["x_top"] - a * by0
        return (a, b)

    def mk_page(side, name):
        if opts.align == "none":
            if side == "single":
                q = np.array([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]], np.float32); info = {}
            else:
                xl = gutter["x_mid"]
                if side == "left":
                    q = np.array([[bx0, by0], [xl, by0], [xl, by1], [bx0, by1]], np.float32)
                else:
                    q = np.array([[xl, by0], [bx1, by0], [bx1, by1], [xl, by1]], np.float32)
                info = {}
        else:
            q, info = G.page_quad(mask, bbox, line_of(gutter) if gutter else None, side)
            if q is None:
                return None
            if opts.align == "rotate":
                # keep only a rotation + crop: average the two vertical and horizontal edge angles
                pass  # the homography of a near-rectangle IS a rotation+crop; perspective adds the keystone fix
        w, h = G.quad_size(q)
        return {"name": name, "side": side, "quad": q, "w": w, "h": h, "info": info}

    if two and gutter:
        names = ["L", "R"]
        pl = mk_page("left", names[0]); pr = mk_page("right", names[1])
        if pl is None or pr is None:
            notes.append("one side empty: treated as a single page")
            two = False
        else:
            # same physical page size on both sides
            w = round((pl["w"] + pr["w"]) / 2); h = round((pl["h"] + pr["h"]) / 2)
            for p in (pl, pr):
                p["w"], p["h"] = int(w), int(h)
            plan["pages"] = [pl, pr]
    if not two:
        side = "single"
        if gutter and want_pages != "2":
            # a single-numbered scan that still shows two pages: keep the one with ink
            a, b = line_of(gutter)
            yy = np.arange(H); xl = a * yy + b
            cols = np.arange(W)[None, :]
            paper = gutter["paper"]
            ink = (gray < G.INK_LUM * paper) & (mask > 0)
            core = (cols >= gutter["band_l"]) & (cols <= gutter["band_r"])
            left_ink = (ink & (cols < xl[:, None]) & ~core).sum() / max(1, ((mask > 0) & (cols < xl[:, None])).sum())
            right_ink = (ink & (cols > xl[:, None]) & ~core).sum() / max(1, ((mask > 0) & (cols > xl[:, None])).sum())
            plan["side_ink"] = [float(left_ink), float(right_ink)]
            notes.append(f"side ink L={left_ink:.3f} R={right_ink:.3f}")
            if left_ink < 0.004 and right_ink >= 0.004:
                side = "right"; notes.append("left page blank: kept the right page")
            elif right_ink < 0.004 and left_ink >= 0.004:
                side = "left"; notes.append("right page blank: kept the left page")
        p = mk_page(side, "single")
        if p is None:
            raise RuntimeError("could not find the page")
        p["w"], p["h"] = int(round(p["w"])), int(round(p["h"]))
        plan["pages"] = [p]

    # per page: homography and gutter band / core in page columns
    for p in plan["pages"]:
        Hm = G.homography(p["quad"], p["w"], p["h"])
        p["H"] = Hm
        p["band"] = None; p["core"] = None; p["spine_x"] = None
        if gutter and gutter["depth"] > 0 and opts.gutter != "none":
            ym = (by0 + by1) / 2
            pts = G.map_points(Hm, [[gutter["band_l"], ym], [gutter["band_r"], ym], [gutter["core_l"], ym], [gutter["core_r"], ym], [gutter["x_mid"], ym]])
            bl, br, cl, cr, sx = [float(v[0]) for v in pts]
            if p["side"] == "left":
                p["band"] = (int(max(0, bl)), p["w"]); p["core"] = (int(max(0, min(cl, p["w"]))), p["w"]); p["spine_x"] = p["w"]
            elif p["side"] == "right":
                p["band"] = (0, int(min(p["w"], br))); p["core"] = (0, int(max(0, min(cr, p["w"])))); p["spine_x"] = 0
            else:
                p["band"] = (int(max(0, bl)), int(min(p["w"], br))); p["core"] = (int(max(0, cl)), int(min(p["w"], cr))); p["spine_x"] = sx
    return plan


def fix_gutter(page_bgr, p, paper, gutter_mode, is_art_layer=True):
    """Flatten the shadow band and rebuild the core of one page raster.
    Returns (image, core_mask or None, method)."""
    if not p["band"] or gutter_mode == "none":
        return page_bgr, None, "none"
    gray = G.luminance(page_bgr[..., :3])
    x0, x1 = p["band"]
    S = G.shading_map(gray, x0, x1, p["spine_x"], paper)
    out = G.apply_shading(page_bgr, S)
    if gutter_mode == "flatten" or not p["core"]:
        return out, None, "flatten"
    c0, c1 = p["core"]
    if c1 - c0 <= 0:
        return out, None, "flatten"
    mask = np.zeros(gray.shape, np.uint8)
    mask[:, max(0, c0 - 2):min(gray.shape[1], c1 + 2)] = 255
    out, m = inpaint_core(out, mask, gutter_mode)
    return out, mask, m


def warp_page(img, p, border):
    return G.warp(img, p["H"], p["w"], p["h"], border)


def join_spread(left, right):
    h = max(left.shape[0], right.shape[0])
    def pad(a):
        if a.shape[0] == h:
            return a
        out = np.full((h, a.shape[1]) + a.shape[2:], 255, a.dtype); out[:a.shape[0]] = a; return out
    return np.concatenate([pad(left), pad(right)], axis=1)


def decide_spread(plan, warped_gray, opts):
    """Fill plan['spread'] from the two warped grey pages (after flattening)."""
    if len(plan["pages"]) != 2:
        return
    if opts.spread in ("yes", "no"):
        plan["spread"] = opts.spread == "yes"; plan["spread_reason"] = f"--spread {opts.spread}"
        return
    pl, pr = plan["pages"]
    paper = plan["gutter"]["paper"] if plan["gutter"] else plan["white"]
    # the shadow band is flattened by now, so only the unrecoverable core is skipped
    cl = (pl["core"][1] - pl["core"][0]) if pl["core"] else 0
    cr = (pr["core"][1] - pr["core"][0]) if pr["core"] else 0
    el, er = G.spread_score(warped_gray[0], warped_gray[1], paper, cl, cr)
    plan["spread_score"] = {"left": el, "right": er}
    # conservative: only a drawing that runs into the spine on both pages, with
    # no margin and no panel border, is called a spread on its own; facing
    # pages that share one picture but keep their white margins look exactly
    # like two pages here and are marked by the agent with --spread yes
    tiny = max(10, int(0.01 * pl["w"]))
    plan["spread"] = (el["inked"] and er["inked"] and not el["border"] and not er["border"]
                      and el["margin"] < tiny and er["margin"] < tiny and el["corr"] > 0.4)

    def fmt(e):
        return f"margin {e['margin']}px" + (", art" if e["inked"] else ", blank") + (", border" if e["border"] else "")
    plan["spread_reason"] = f"inner edges L: {fmt(el)} | R: {fmt(er)} | corr {el['corr']}"


# ---------------------------------------------------------------- outputs

def output_names(plan, names, opts):
    if len(plan["pages"]) == 2:
        if plan["spread"]:
            return ["-".join(names)]
        a, b = names
        return ([b, a] if opts.rtl else [a, b])   # [left page name, right page name]
    return [names[0]]


def review_sheet(img, plan, results, out_path, label):
    """Source with the quads + gutter drawn, and the resulting page(s) below."""
    H, W = img.shape[:2]
    s = 1000 / W
    top = cv2.resize(img[..., :3], (1000, int(H * s)), interpolation=cv2.INTER_AREA)
    bx0, by0, bx1, by1 = plan["bbox"]
    cv2.rectangle(top, (int(bx0 * s), int(by0 * s)), (int(bx1 * s), int(by1 * s)), (255, 160, 0), 1)
    g = plan["gutter"]
    if g:
        cv2.line(top, (int(g["x_top"] * s), int(by0 * s)), (int(g["x_bot"] * s), int(by1 * s)), (0, 0, 255), 2)
        for x in (g["band_l"], g["band_r"]):
            cv2.line(top, (int(x * s), int(by0 * s)), (int(x * s), int(by1 * s)), (0, 200, 255), 1)
    for p in plan["pages"]:
        q = (p["quad"] * s).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(top, [q], True, (0, 220, 0), 2)
    cv2.putText(top, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    tiles = []
    for r in results:
        rs = min(1.0, (1000 / len(results) - 8) / r.shape[1], 700 / r.shape[0])
        tiles.append(cv2.resize(r[..., :3], (max(1, int(r.shape[1] * rs)), max(1, int(r.shape[0] * rs))), interpolation=cv2.INTER_AREA))
    th = max(t.shape[0] for t in tiles) if tiles else 0
    bottom = np.full((th + 8, 1000, 3), 128, np.uint8)
    x = 0
    for t in tiles:
        bottom[4:4 + t.shape[0], x:x + t.shape[1]] = t; x += t.shape[1] + 8
    sheet = np.concatenate([top, bottom], axis=0)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])


def process_rasters(layers, plan, opts, paper):
    """layers: list of (name, BGRA full-canvas image, fix) - fix=False keeps the
    gutter as scanned (the Original), fix=True flattens it and rebuilds the
    core. Returns, per output page, the list of warped images in layer order."""
    pages = plan["pages"]
    per_page = [[] for _ in pages]
    for name, img, fix in layers:
        opaque = img.shape[2] == 3 or img[..., 3].min() == 255
        border = (255, 255, 255, 255) if opaque else (0, 0, 0, 0)
        for i, p in enumerate(pages):
            wp = warp_page(img, p, border)
            if opaque and fix:
                wp, _, _ = fix_gutter(wp, p, paper, opts.gutter)
            per_page[i].append(wp)
    if plan["spread"]:
        joined = []
        for li in range(len(layers)):
            left, right = per_page[0][li], per_page[1][li]
            j = join_spread(left, right)
            # rebuild the seam once more as one region across both cores
            if layers[li][2] and opts.gutter in ("inpaint", "white") and (pages[0]["core"] or pages[1]["core"]):
                w0 = left.shape[1]
                c0 = pages[0]["core"][0] if pages[0]["core"] else w0
                c1 = w0 + (pages[1]["core"][1] if pages[1]["core"] else 0)
                mask = np.zeros(j.shape[:2], np.uint8); mask[:, max(0, c0 - 3):min(j.shape[1], c1 + 3)] = 255
                j, _ = inpaint_core(j, mask, opts.gutter)
            joined.append(j)
        outs = [joined]
    else:
        outs = per_page
    return outs


def save_image(path, img):
    ext = os.path.splitext(path)[1].lower()
    bgr = img[..., :3] if img.shape[2] == 4 and img[..., 3].min() == 255 else img
    if ext in (".jpg", ".jpeg"):
        cv2.imwrite(path, bgr[..., :3], [cv2.IMWRITE_JPEG_QUALITY, 95])
    else:
        cv2.imwrite(path, bgr)


EXIF_ROT = {3: cv2.ROTATE_180, 6: cv2.ROTATE_90_CLOCKWISE, 8: cv2.ROTATE_90_COUNTERCLOCKWISE}


def load_bgra(path, apply_exif=True):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError(f"cannot read {path}")
    if apply_exif:
        try:
            from PIL import Image
            o = Image.open(path).getexif().get(274, 1)
            if o in EXIF_ROT:
                img = cv2.rotate(img, EXIF_ROT[o])
        except Exception:
            pass
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


def place_on_canvas(png, W, H, left, top):
    """A layer PNG (its own bounds) -> full-canvas BGRA."""
    lay = load_bgra(png)
    canvas = np.zeros((H, W, 4), np.uint8)
    h, w = lay.shape[:2]
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(W, left + w), min(H, top + h)
    if x1 > x0 and y1 > y0:
        canvas[y0:y1, x0:x1] = lay[y0 - top:y1 - top, x0 - left:x1 - left]
    return canvas


def text_center(t, layer):
    tr = t.get("transform") or [1, 0, 0, 1, layer.get("left", 0), layer.get("top", 0)]
    a, b, c, d, tx, ty = tr
    bb = t.get("boxBounds") or [0, 0, layer.get("right", 0) - layer.get("left", 0), layer.get("bottom", 0) - layer.get("top", 0)]
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    return a * cx + c * cy + tx, b * cx + d * cy + ty


def process_psd(path, plan, out_paths, opts, paper, work, dump):
    """From the PSD's dumped layers: warp every raster, move the text layers, build one PSD per output page."""
    info = json.load(open(os.path.join(dump, "layers.json")))
    W, H = info["width"], info["height"]
    rasters = [(L["name"], place_on_canvas(L["png"], W, H, L["left"], L["top"]), i > 0)
               for i, L in enumerate([L for L in info["layers"] if L["kind"] == "raster"])]
    added_copy = False
    if len(rasters) == 1:
        rasters.append(("Copy", rasters[0][1], True)); added_copy = True
    outs = process_rasters(rasters, plan, opts, paper)
    pages = plan["pages"]
    # text layers -> page by centre
    assigned = [[] for _ in out_paths]
    dropped = []
    for L in info["layers"]:
        if L["kind"] != "text":
            if L["kind"] != "raster":
                dropped.append(L["name"])
            continue
        cx, cy = text_center(L["text"], L)
        best, bestd, bestpt = None, None, None
        for i, p in enumerate(pages):
            x, y = G.map_points(p["H"], [[cx, cy]])[0]
            inside = 0 <= x < p["w"] and 0 <= y < p["h"]
            d = 0 if inside else min(abs(x), abs(x - p["w"])) + min(abs(y), abs(y - p["h"]))
            if best is None or d < bestd:
                best, bestd, bestpt = i, d, (x, y)
        x, y = bestpt
        if plan["spread"]:
            if best == 1:
                x += pages[0]["w"]
            best = 0
        dx, dy = x - cx, y - cy
        # a box that straddled the gutter lands partly off its page: slide it in
        pw = outs[best][0].shape[1]; ph = outs[best][0].shape[0]
        bw_, bh_ = L["right"] - L["left"], L["bottom"] - L["top"]
        nx0, ny0 = L["left"] + dx, L["top"] + dy
        if bw_ < pw:
            nx0 = min(max(0, nx0), pw - bw_)
        if bh_ < ph:
            ny0 = min(max(0, ny0), ph - bh_)
        dx, dy = nx0 - L["left"], ny0 - L["top"]
        nl = dict(L)
        t = dict(L["text"])
        tr = [float(v) for v in (t.get("transform") or [1, 0, 0, 1, L["left"], L["top"]])]
        tr[4] += float(dx); tr[5] += float(dy)
        t["transform"] = tr
        nl["text"] = t
        for k in ("left", "right"):
            nl[k] = int(round(L[k] + float(dx)))
        for k in ("top", "bottom"):
            nl[k] = int(round(L[k] + float(dy)))
        assigned[best].append(nl)
    results = []
    for i, out_path in enumerate(out_paths):
        pw, ph = outs[i][0].shape[1], outs[i][0].shape[0]
        spec = {"out": out_path, "width": pw, "height": ph, "layers": []}
        ri = 0
        raster_meta = [L for L in info["layers"] if L["kind"] == "raster"]
        if added_copy:
            raster_meta.append({"name": "Copy", "opacity": 1, "hidden": False, "blendMode": "normal"})
        for L in raster_meta:
            png = os.path.join(work, f"p{i}_{ri}.png")
            cv2.imwrite(png, outs[i][ri]); ri += 1
            spec["layers"].append({"kind": "raster", "name": L["name"], "png": png, "left": 0, "top": 0,
                                   "opacity": L.get("opacity", 1), "hidden": L.get("hidden", False), "blendMode": L.get("blendMode", "normal")})
        # text layers after rasters (same relative order as in the source)
        for nl in assigned[i]:
            spec["layers"].append({"kind": "text", **{k: nl[k] for k in ("name", "left", "top", "right", "bottom", "opacity", "hidden", "blendMode") if k in nl}, "text": nl["text"]})
        spec_path = os.path.join(work, f"spec{i}.json")
        json.dump(spec, open(spec_path, "w"))
        subprocess.run([NODE, "--max-old-space-size=4096", PSD_IO, "build", spec_path], check=True)
        write_side_images(opts, out_path, outs[i][0], outs[i][-1])
        results.append(outs[i][-1])
    return results, [len(a) for a in assigned], dropped


def write_side_images(opts, psd_path, original, fixed):
    if not opts.also_images:
        return
    out = os.path.dirname(psd_path); stem = os.path.splitext(os.path.basename(psd_path))[0]
    for sub, im in (("images", fixed), ("originals", original)):
        d = os.path.join(out, sub); os.makedirs(d, exist_ok=True)
        save_image(os.path.join(d, stem + ".png"), im)


def build_image_psd(out_path, original, fixed, work, idx):
    """A PSD with "Original" (warped page as scanned) and "Copy" (gutter fixed)."""
    spec = {"out": out_path, "width": fixed.shape[1], "height": fixed.shape[0], "layers": []}
    for name, im in (("Original", original), ("Copy", fixed)):
        png = os.path.join(work, f"img{idx}_{name}.png"); cv2.imwrite(png, im)
        spec["layers"].append({"kind": "raster", "name": name, "png": png, "left": 0, "top": 0})
    spec_path = os.path.join(work, f"imgspec{idx}.json"); json.dump(spec, open(spec_path, "w"))
    subprocess.run([NODE, "--max-old-space-size=4096", PSD_IO, "build", spec_path], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--output", "-o")
    ap.add_argument("--rtl", action="store_true", help="first number of NNN-MMM is the RIGHT page (manga reading order)")
    ap.add_argument("--spread", choices=["auto", "yes", "no"], default="auto")
    ap.add_argument("--pages", choices=["auto", "1", "2"], default="auto")
    ap.add_argument("--align", choices=["perspective", "none"], default="perspective")
    ap.add_argument("--gutter", choices=["inpaint", "white", "flatten", "none"], default="inpaint")
    ap.add_argument("--only", nargs="*", help="stems to process")
    ap.add_argument("--rotate", type=int, choices=[0, 90, -90, 180], default=0, help="turn every scan first (degrees clockwise)")
    ap.add_argument("--also-images", action="store_true", help="also write images/<page>.png (fixed) and originals/<page>.png")
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="analyse + review sheets, write no pages")
    ap.add_argument("--force", action="store_true", help="redo pages whose outputs exist")
    opts = ap.parse_args()

    src = os.path.abspath(opts.src)
    out = os.path.abspath(opts.output) if opts.output else default_out(src)
    os.makedirs(out, exist_ok=True)
    files = list_inputs(src, opts.recursive, set(opts.only or []))
    if not files:
        print("no input images/PSDs found"); return 1
    report = open(os.path.join(out, "report.jsonl"), "a")
    print(f"{len(files)} file(s) -> {out}")
    for f in files:
        t0 = time.time()
        stem, ext = os.path.splitext(os.path.basename(f))
        ext = ext.lower()
        kind, names = parse_stem(stem)
        try:
            with tempfile.TemporaryDirectory(prefix="split_", dir=os.environ.get("SPLIT_TMP")) as work:
                if ext in PSD_EXT:
                    dump = os.path.join(work, "dump"); os.makedirs(dump)
                    subprocess.run([NODE, "--max-old-space-size=4096", PSD_IO, "dump", f, dump], check=True)
                    info = json.load(open(os.path.join(dump, "layers.json")))
                    base = next((L for L in info["layers"] if L["kind"] == "raster"), None)
                    if base is None:
                        raise RuntimeError("no raster layer")
                    img = place_on_canvas(base["png"], info["width"], info["height"], base["left"], base["top"])
                    # analyse on an opaque version
                    a = img[..., 3:4].astype(np.float32) / 255
                    ana = (img[..., :3].astype(np.float32) * a + 255 * (1 - a)).astype(np.uint8)
                else:
                    img = load_bgra(f)
                    ana = img[..., :3]
                if opts.rotate:
                    rot = {90: cv2.ROTATE_90_CLOCKWISE, -90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180}[opts.rotate]
                    img = cv2.rotate(img, rot); ana = cv2.rotate(ana, rot)
                plan = analyse(ana, kind, opts)
                if kind == "double" and len(plan["pages"]) == 2:
                    bx0, by0, bx1, by1 = plan["bbox"]
                    if (by1 - by0) > 1.15 * (bx1 - bx0):
                        plan["notes"].append("two-page scan is PORTRAIT: the pages are probably stacked - rerun with --rotate 90 or -90")
                paper = plan["gutter"]["paper"] if plan["gutter"] else plan["white"]
                # spread decision needs the flattened pages: warp the analysis image
                if len(plan["pages"]) == 2:
                    wg = []
                    for p in plan["pages"]:
                        wp = warp_page(ana, p, (255, 255, 255))
                        wp, _, _ = fix_gutter(wp, p, paper, "flatten")
                        wg.append(G.luminance(wp))
                    decide_spread(plan, wg, opts)
                onames = output_names(plan, names, opts)
                out_paths = [os.path.join(out, n + ".psd") for n in onames]
                g = plan["gutter"]
                desc = (f"{stem}: {len(plan['pages'])} page(s)"
                        + (f", gutter x={g['x_mid']:.0f} tilt {g['tilt_deg']:.2f}° depth {g['depth']:.2f} band {g['band_r'] - g['band_l']}px core {g['core_r'] - g['core_l']}px" if g else ", no gutter")
                        + (f", SPREAD ({plan.get('spread_reason')})" if plan["spread"] else (f" ({plan.get('spread_reason')})" if len(plan["pages"]) == 2 else ""))
                        + " -> " + ", ".join(os.path.basename(p) for p in out_paths))
                for p in plan["pages"]:
                    ang = {k: v["angle"] for k, v in p.get("info", {}).get("lines", {}).items()}
                    desc += f" | {p['side']} {p['w']}x{p['h']} edges {ang}"
                if plan["notes"]:
                    desc += " | " + "; ".join(plan["notes"])
                print(desc, flush=True)
                rec = {"file": f, "outputs": out_paths, "pages": len(plan["pages"]), "spread": plan["spread"],
                       "spread_score": plan.get("spread_score"), "gutter": g, "notes": plan["notes"],
                       "edges": [p.get("info", {}).get("lines", {}) for p in plan["pages"]]}
                if opts.dry_run:
                    results = [warp_page(ana, p, (255, 255, 255)) for p in plan["pages"]]
                    results = [fix_gutter(r, p, paper, "flatten")[0] for r, p in zip(results, plan["pages"])]
                    if plan["spread"]:
                        results = [join_spread(*results)]
                elif all(os.path.exists(p) for p in out_paths) and not opts.force:
                    print(f"  exists, skipped (--force to redo)"); continue
                elif ext in PSD_EXT:
                    results, ntext, dropped = process_psd(f, plan, out_paths, opts, paper, work, dump)
                    rec["text_layers"] = ntext
                    if dropped:
                        rec["dropped_layers"] = dropped; print(f"  WARNING: layers not carried over: {dropped}")
                else:
                    outs = process_rasters([("Original", img, False), ("Copy", img, True)], plan, opts, paper)
                    results = [o[1] for o in outs]
                    for i, (pth, o) in enumerate(zip(out_paths, outs)):
                        build_image_psd(pth, o[0], o[1], work, i)
                        write_side_images(opts, pth, o[0], o[1])
                review_sheet(ana, plan, results, os.path.join(out, "review", stem + ".jpg"), desc[:120])
                rec["seconds"] = round(time.time() - t0, 1)
                report.write(json.dumps(rec, ensure_ascii=False, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)) + "\n"); report.flush()
        except Exception as e:
            print(f"FAIL {stem}: {e}", flush=True)
            import traceback; traceback.print_exc()
            report.write(json.dumps({"file": f, "error": str(e)}) + "\n"); report.flush()
    print("ALL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
