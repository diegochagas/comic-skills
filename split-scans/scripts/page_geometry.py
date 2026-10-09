#!/usr/bin/env python3
"""Geometry of a flatbed scan of an open book (or a single page): where the
scanner background ends, where the gutter (the spine shadow) runs, the four
corners of each page and the perspective warp that flattens it, and the
shading correction that turns the gutter shadow back into paper.

Everything here is plain numpy/OpenCV on a BGR image; split_scans.py is the
CLI that applies the results to JPG/PNG files and to every layer of a PSD.

Pipeline (analyse()):
  1. background: median colour of the outer 2 % frame of the scan. A frame
     as bright as paper means the scan has no border (already cropped).
  2. paper mask: pixels brighter than halfway between background and paper
     white, cleaned with open/close, components under 2 % of the scan dropped.
  3. gutter: in horizontal bands, the darkest narrow valley of the per-column
     95th-percentile luminance (the paper level of a column - robust to art)
     near the middle of the book; a line is fitted through the band minima.
     The shadow band is where that paper level is under 90 % of the page's,
     the "core" where it is under 35 % (nothing to recover there).
  4. per page: robust line fits of the outer, top and bottom paper edges
     (per-row / per-column extreme mask pixels, iterative outlier rejection)
     plus the gutter line give a quad -> homography to an upright rectangle.
  5. spread test: ink in the strip next to the gutter on both pages, and the
     correlation of the two strips' row-ink profiles.
"""
import math

import cv2
import numpy as np

FRAME_FRAC = 0.02        # outer frame sampled for the background colour
MIN_COMPONENT = 0.02     # paper components smaller than this fraction of the scan are noise
GUTTER_SEARCH = 0.15     # look for the gutter within +- this fraction of the book width from its centre
GUTTER_MIN_DEPTH = 0.18  # valley must be at least this much darker than the paper level (fraction)
GUTTER_MAX_WIDTH = 0.10  # ...and narrower than this fraction of the book width (at half depth)
SHADOW_LEVEL = 0.90      # columns whose paper level is below this fraction belong to the shadow band
CORE_LEVEL = 0.35        # ...and below this nothing is recoverable: inpaint / fill white
MAX_BAND = 0.06          # shadow band never wider than this fraction of the book width (per side)
EDGE_MARGIN = 0.05       # rows/cols skipped at each end when collecting edge points
MAX_ANGLE = 6.0          # an edge steeper than this (degrees) is not a page edge
INNER_STRIP = 0.04       # width (fraction of page width) of the strip next to the shadow band used by the spread test
INK_LUM = 0.45           # pixel darker than this fraction of the paper level counts as ink
SPREAD_INK = 0.10        # both inner strips with at least this ink fraction -> spread


def luminance(img):
    if img.ndim == 2:
        return img.astype(np.float32)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def estimate_background(img, gray):
    """(bg colour BGR, bg luminance, paper white luminance) from the outer frame / the 99th percentile."""
    h, w = gray.shape
    fy, fx = max(2, int(h * FRAME_FRAC)), max(2, int(w * FRAME_FRAC))
    parts = [img[:fy].reshape(-1, 3), img[-fy:].reshape(-1, 3), img[:, :fx].reshape(-1, 3), img[:, -fx:].reshape(-1, 3)]
    frame = np.concatenate(parts)
    bg_col = np.median(frame, axis=0)
    lum = np.concatenate([gray[:fy].ravel(), gray[-fy:].ravel(), gray[:, :fx].ravel(), gray[:, -fx:].ravel()])
    bg = float(np.median(lum))
    white = float(np.percentile(gray, 99))
    return bg_col, bg, white


def paper_mask(img, gray, bg_col, bg, white):
    """Pixels that differ from the scanner background (paper, ink, colour art):
    Chebyshev colour distance from the background colour above half the
    background-to-paper gap. Components are kept when they hold some paper
    (a black lid edge or a shadow stripe has none) and are not tiny."""
    thr = max(20.0, 0.5 * (white - bg))
    dist = np.max(np.abs(img.astype(np.int16) - bg_col.astype(np.int16)), axis=2)
    # black ink on a dark-grey background is closer to it than paper is: a
    # second, lower threshold on the dark side (impossible on a black background)
    thr_dark = max(20.0, 0.3 * bg)
    bright = (dist > thr)
    bright_u8 = bright.astype(np.uint8)
    bb = bbox_of(cv2.morphologyEx(bright_u8, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)))
    dark = gray < bg - thr_dark
    # grey art on a grey background: the scanner background is flat, art is not
    g32 = gray.astype(np.float32)
    mu = cv2.blur(g32, (15, 15)); var = cv2.blur(g32 * g32, (15, 15)) - mu * mu
    dark |= var > 36.0
    # whatever dark/textured region touches the scan border is the scanner (the
    # lid's black edge, the book's cut edge), never the page
    n_d, lab_d = cv2.connectedComponents(dark.astype(np.uint8), connectivity=8)
    border_ids = np.unique(np.concatenate([lab_d[0], lab_d[-1], lab_d[:, 0], lab_d[:, -1]]))
    border_ids = border_ids[border_ids != 0]
    if len(border_ids):
        dark &= ~np.isin(lab_d, border_ids)
    if bb is not None:
        # black art counts as page only inside the paper's own footprint (+1.5 %):
        # the lid's black edge beside the book stays background
        mx, my = int(0.015 * gray.shape[1]), int(0.015 * gray.shape[0])
        win = np.zeros_like(dark)
        win[max(0, bb[1] - my):bb[3] + my, max(0, bb[0] - mx):bb[2] + mx] = True
        dark &= win
    m = (bright | dark).astype(np.uint8)
    k = max(3, int(min(gray.shape) * 0.006) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k * 3, k * 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros_like(m)
    area = gray.shape[0] * gray.shape[1]
    bright = gray > bg + thr
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < MIN_COMPONENT * area:
            continue
        comp = lab == i
        if bright[comp].mean() < 0.03 and a < 0.3 * area:
            continue        # no paper in it: the lid's black edge, a shadow stripe
        keep[comp] = 1
    return keep


def bbox_of(mask):
    ys, xs = np.where(mask > 0)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def robust_line(xs, ys, iters=4):
    """Fit y = a*x + b (xs = independent axis) with iterative outlier rejection.
    Returns (a, b, inlier_ratio, n_points)."""
    xs = np.asarray(xs, np.float64); ys = np.asarray(ys, np.float64)
    if len(xs) < 10:
        return None
    sel = np.ones(len(xs), bool)
    a = b = 0.0
    for _ in range(iters):
        if sel.sum() < 10:
            break
        a, b = np.polyfit(xs[sel], ys[sel], 1)
        res = np.abs(ys - (a * xs + b))
        mad = np.median(res[sel]) + 1e-6
        sel = res < max(3.0, 3.0 * mad)
    res = np.abs(ys - (a * xs + b))
    inl = float((res < 4.0).mean())
    return a, b, inl, int(len(xs))


def envelope_line(xs, ys, outward, iters=10):
    """Fit the OUTER straight edge of a set of per-row/per-column extreme
    points: y = a*x + b such that (almost) no point lies outside it. outward
    = -1 when the edge is the minimum-y side (top / left edge), +1 for the
    maximum side. Starting from a plain fit, points on the inner side of the
    line are dropped round after round, so the line climbs over the notches
    that black art or missing margins cut into the paper mask; isolated
    points far outside (dust, a lid edge) are ignored.
    Returns (a, b, inlier_ratio, n) or None."""
    xs = np.asarray(xs, np.float64); ys = np.asarray(ys, np.float64)
    n = len(xs)
    if n < 10:
        return None
    sel = np.ones(n, bool)
    a = b = 0.0
    for _ in range(iters):
        if sel.sum() < 5:
            break
        a, b = np.polyfit(xs[sel], ys[sel], 1)
        res = (ys - (a * xs + b)) * outward          # > 0: outside the line
        mad = np.median(np.abs(res[sel])) + 1e-6
        far = max(15.0, 5.0 * mad)
        new = (res >= -4.0) & (res <= far)
        if new.sum() < 5:
            break
        if np.array_equal(new, sel):
            break
        sel = new
    res = np.abs(ys - (a * xs + b))
    inl = float((res < 4.0).mean())
    return a, b, inl, int(n)


def column_paper_level(gray, y0, y1, x0, x1, pct=95):
    band = gray[y0:y1, x0:x1]
    return np.percentile(band, pct, axis=0)


def smooth(v, k):
    k = max(1, int(k) | 1)
    return cv2.blur(v.reshape(1, -1).astype(np.float32), (k, 1)).ravel() if k > 1 else v


def find_gutter(gray, hsv_s, bbox, debug=None):
    """Locate the spine shadow inside the book bbox. Returns a dict
    {x_top, x_bot (page x of the valley line at bbox top/bottom), x_mid, depth,
    band_l, band_r (shadow extent, scan px), core_l, core_r, paper, tilt_deg}
    or None when no narrow grey valley exists near the middle."""
    bx0, by0, bx1, by1 = bbox
    bw, bh = bx1 - bx0, by1 - by0
    if bw < 50 or bh < 50:
        return None
    cx = bx0 + bw / 2
    sx0, sx1 = int(cx - GUTTER_SEARCH * bw) - bx0, int(cx + GUTTER_SEARCH * bw) - bx0
    nb = 8
    rows = np.linspace(by0 + 0.08 * bh, by1 - 0.08 * bh, nb + 1).astype(int)
    profs = []
    pts, depths, widths = [], [], []
    for i in range(nb):
        prof = smooth(column_paper_level(gray, rows[i], rows[i + 1], bx0, bx1), bw * 0.004)
        profs.append(prof)
        win = prof[sx0:sx1]
        paper = np.percentile(win, 90)
        # candidate = the valley with the best depth, discounted by its distance from the centre
        # (a dark drawing off-centre must not beat the spine)
        centre = (sx1 - sx0) / 2
        weight = 1.0 - 0.6 * np.abs(np.arange(len(win)) - centre) / max(centre, 1)
        j = int(np.argmax((paper - win) * weight))
        depth = (paper - win[j]) / max(paper, 1)
        half = paper - 0.5 * (paper - win[j])
        l = j
        while l > 0 and win[l] < half:
            l -= 1
        r = j
        while r < len(win) - 1 and win[r] < half:
            r += 1
        pts.append((bx0 + sx0 + j, (rows[i] + rows[i + 1]) / 2)); depths.append(depth); widths.append(r - l)
    depths = np.array(depths); widths = np.array(widths)
    ok = (depths >= GUTTER_MIN_DEPTH) & (widths <= GUTTER_MAX_WIDTH * bw)
    if debug is not None:
        debug["gutter_bands"] = [(int(p[0]), int(p[1]), round(float(d), 3), int(w)) for p, d, w in zip(pts, depths, widths)]
    if ok.sum() < max(3, nb // 2):
        return None
    xs = np.array([p[0] for p in pts])[ok]; ys = np.array([p[1] for p in pts])[ok]
    med = np.median(xs)
    keep = np.abs(xs - med) < 0.04 * bw
    if keep.sum() < 3:
        return None
    xs, ys = xs[keep], ys[keep]
    a, b = np.polyfit(ys, xs, 1)
    x_top, x_bot = a * by0 + b, a * by1 + b
    x_mid = (x_top + x_bot) / 2
    xm = int(round(x_mid))
    # shadow is grey: a saturated dark stripe (a coloured spine) is not a gutter
    sat = float(np.median(hsv_s[by0:by1, max(0, xm - 5):xm + 6]))
    if sat > 70:
        return None
    # shadow band / core extent from the typical (median over bands) column paper level
    prof = np.median(np.array(profs), axis=0)
    j = int(np.clip(xm - bx0, 0, len(prof) - 1))
    paper = float(np.percentile(prof, 90))
    maxb = int(MAX_BAND * bw)

    def extent(level):
        l = j
        while l > 0 and prof[l] < level * paper and j - l < maxb:
            l -= 1
        r = j
        while r < len(prof) - 1 and prof[r] < level * paper and r - j < maxb:
            r += 1
        return bx0 + l, bx0 + r + 1

    band_l, band_r = extent(SHADOW_LEVEL)
    core_l, core_r = extent(CORE_LEVEL)
    return {"x_top": float(x_top), "x_bot": float(x_bot), "x_mid": float(x_mid),
            "depth": float(np.median(depths[ok])), "band_l": int(band_l), "band_r": int(band_r),
            "core_l": int(core_l), "core_r": int(core_r), "paper": paper, "tilt_deg": math.degrees(math.atan(a))}


def _edge_points(mask, side_slice, axis, first):
    """Extreme mask pixel per row (axis=0 -> x of first/last True in each row)
    or per column. Returns (coords along axis, extreme positions)."""
    sub = mask[side_slice]
    if axis == 0:   # per row -> x
        arr = sub
    else:           # per column -> y
        arr = sub.T
    has = arr.any(axis=1)
    idx = np.where(has)[0]
    if first:
        pos = arr.argmax(axis=1)
    else:
        pos = arr.shape[1] - 1 - arr[:, ::-1].argmax(axis=1)
    return idx, pos[idx]


def page_quad(mask, bbox, x_inner_line, side, gutter=None):
    """Corners of one page as a quad (TL, TR, BR, BL) in scan px, from robust
    fits of its outer/top/bottom paper edges and the gutter line (x_inner_line
    = (a, b) with x = a*y + b, or None for a single page -> both vertical edges
    come from the mask). side: 'left' | 'right' | 'single'.
    Returns (quad, info)."""
    bx0, by0, bx1, by1 = bbox
    H, W = mask.shape
    info = {}
    # restrict the mask to this side (left of / right of the gutter line)
    if side == "single":
        sm = mask.copy()
        sx0, sx1 = bx0, bx1
    else:
        a, b = x_inner_line
        yy = np.arange(H)
        xl = (a * yy + b)
        sm = mask.copy()
        cols = np.arange(W)[None, :]
        if side == "left":
            sm[cols >= xl[:, None]] = 0
            sx0, sx1 = bx0, int(min(xl.min(), bx1))
        else:
            sm[cols <= xl[:, None]] = 0
            sx0, sx1 = int(max(xl.max(), bx0)), bx1
    sb = bbox_of(sm)
    if sb is None:
        return None, {"error": "empty side"}
    sbx0, sby0, sbx1, sby1 = sb
    sw, sh = sbx1 - sbx0, sby1 - sby0
    my, mx = int(sh * EDGE_MARGIN), int(sw * EDGE_MARGIN)
    lines = {}

    # outer vertical edge(s): x = a*y + b
    def vert(first):
        rows, xs = _edge_points(sm[sby0 + my:sby1 - my, sbx0:sbx1], slice(None), 0, first)
        if len(rows) < 10:
            return None
        f = envelope_line(rows + sby0 + my, xs + sbx0, -1 if first else 1)
        if f is None or abs(math.degrees(math.atan(f[0]))) > MAX_ANGLE:
            return None
        return f

    def horiz(first):
        # exclude the shadow band columns on the inner side: the page edge curves there
        if side == "left":
            cols_slice = slice(sbx0 + mx, max(sbx0 + mx + 10, sbx1 - 2 * mx))
        elif side == "right":
            cols_slice = slice(min(sbx1 - mx - 10, sbx0 + 2 * mx), sbx1 - mx)
        else:
            cols_slice = slice(sbx0 + mx, sbx1 - mx)
        cols, ys = _edge_points(sm[sby0:sby1, cols_slice], slice(None), 1, first)
        if len(cols) < 10:
            return None
        f = envelope_line(cols + cols_slice.start, ys + sby0, -1 if first else 1)
        if f is None or abs(math.degrees(math.atan(f[0]))) > MAX_ANGLE:
            return None
        return f

    left = vert(True) if side in ("left", "single") else None
    right = vert(False) if side in ("right", "single") else None
    top, bot = horiz(True), horiz(False)
    for k, f in (("left", left), ("right", right), ("top", top), ("bottom", bot)):
        if f:
            lines[k] = {"a": float(f[0]), "b": float(f[1]), "inliers": round(f[2], 3), "angle": round(math.degrees(math.atan(f[0])), 3)}
    info["lines"] = lines

    # fall back to the bbox for any edge whose fit is poor
    def line_or(f, default_val, vertical):
        if f and f[2] >= 0.15:        # an envelope needs only part of the edge to be visible
            return f[0], f[1]
        return 0.0, float(default_val)

    la = line_or(left, sbx0, True) if side in ("left", "single") else x_inner_line
    ra = line_or(right, sbx1, True) if side in ("right", "single") else x_inner_line
    ta = line_or(top, sby0, False)
    ba = line_or(bot, sby1, False)

    def isect(v, h):
        # x = va*y + vb ; y = ha*x + hb
        va, vb = v; ha, hb = h
        # x = va*(ha*x + hb) + vb -> x (1 - va*ha) = va*hb + vb
        x = (va * hb + vb) / (1 - va * ha)
        y = ha * x + hb
        return [x, y]

    quad = np.array([isect(la, ta), isect(ra, ta), isect(ra, ba), isect(la, ba)], np.float32)
    # a hair inside the paper edge, so no sliver of scanner background survives
    c = quad.mean(axis=0)
    quad = c + (quad - c) * (1 - 0.0025)
    info["bbox"] = [sbx0, sby0, sbx1, sby1]
    return quad, info


def quad_size(quad):
    (tl, tr, br, bl) = quad
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    return float(w), float(h)


def homography(quad, out_w, out_h):
    dst = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], np.float32)
    return cv2.getPerspectiveTransform(quad.astype(np.float32), dst)


def warp(img, Hm, out_w, out_h, border):
    return cv2.warpPerspective(img, Hm, (int(out_w), int(out_h)), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def map_points(Hm, pts):
    p = np.asarray(pts, np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(p, Hm).reshape(-1, 2)


def shading_map(gray_page, x0, x1, spine_x, paper):
    """Illumination estimate (1.0 = paper) for the gutter shadow of an already
    flattened page: inside the shadow band [x0, x1) the per-column paper level
    (95th percentile) is taken per row band, median-smoothed, forced to fall
    monotonically towards the spine column spine_x (dips are art, not shadow)
    and interpolated over the rows. Returns the (h, w) map, 1.0 outside the band."""
    h, w = gray_page.shape
    x0, x1 = int(max(0, x0)), int(min(w, x1))
    S = np.ones((h, w), np.float32)
    bw = x1 - x0
    if bw < 2:
        return S
    sp = int(np.clip(spine_x - x0, 0, bw))
    nb = max(1, h // 96)
    edges = np.linspace(0, h, nb + 1).astype(int)
    centers, profiles = [], []
    for i in range(nb):
        y0, y1 = edges[i], edges[i + 1]
        if y1 - y0 < 4:
            continue
        prof = np.percentile(gray_page[y0:y1, x0:x1], 95, axis=0).astype(np.float32)
        k = max(3, min(31, bw // 6) | 1)
        if k >= 3 and bw > k:
            prof = cv2.medianBlur(np.clip(prof, 0, 255).reshape(1, -1).astype(np.uint8), k).ravel().astype(np.float32)
        # monotone envelope: illumination only falls when moving towards the spine
        if sp > 0:
            prof[:sp] = np.minimum.accumulate(prof[:sp])
        if sp < bw:
            prof[sp:] = np.minimum.accumulate(prof[sp:][::-1])[::-1]
        centers.append((y0 + y1) / 2); profiles.append(prof)
    P = np.array(profiles)                                # (nb, bw)
    if P.shape[1] >= 5:
        P = cv2.blur(P, (5, 1))
    # where art covers the band the paper level of a row band is unknown: use the
    # brightest level seen in that column over the page (never over-brightens art;
    # a spread's dark art near the spine keeps a faint shadow), blended with the
    # row band's own level where that is at least as bright
    col_max = P.max(axis=0, keepdims=True)
    P = np.maximum(P, col_max)
    shade = np.clip(P / max(paper, 1.0), 0.05, 1.0)
    ys = np.arange(h, dtype=np.float32)
    cy = np.array(centers, np.float32)
    band = np.empty((h, bw), np.float32)
    for j in range(bw):
        band[:, j] = np.interp(ys, cy, shade[:, j])
    S[:, x0:x1] = band
    S = cv2.GaussianBlur(S, (0, 0), 3)
    return np.minimum(S, 1.0)


def apply_shading(img, S):
    """Divide the image by the shading map (brightens the shadow back to paper)."""
    f = img.astype(np.float32)
    if f.ndim == 3:
        rgb = f[..., :3] / S[..., None]
        out = f.copy(); out[..., :3] = rgb
    else:
        out = f / S
    return np.clip(out, 0, 255).astype(np.uint8)


def inner_edge(gray_page, inner_side, core_px, paper):
    """What the page looks like next to the spine (the flattened shadow band
    included, the dark core excluded), as {margin, inked, border}:
    margin = px of ink-free columns before the printed area starts; inked =
    True when the strip just inside that margin carries ink (artwork reaches
    the spine side of the page); border = True when a long vertical line (a
    panel border, 1-6 px with clear paper beside it) runs along that edge."""
    h, w = gray_page.shape
    y0, y1 = int(0.05 * h), int(0.95 * h)
    span = max(int(0.15 * w), core_px + 20)
    f = (gray_page[y0:y1] < 0.35 * paper).mean(axis=0)   # real ink only: residual shadow on paper stays above this
    if inner_side == "right":
        order = list(range(w - core_px - 1, max(0, w - span) - 1, -1))   # from the spine outwards
    else:
        order = list(range(core_px, min(w, span)))
    margin = 0
    for x in order:
        if f[x] < 0.15:          # a balloon or SFX poking into the margin is tolerated
            margin += 1
        else:
            break
    strip = order[margin:margin + max(8, int(0.04 * w))]
    inked = bool(strip) and float(np.mean([f[x] for x in strip])) > 0.10
    border = False
    for i, x in enumerate(order[margin:margin + int(0.10 * w)]):
        if f[x] > 0.6:
            lo = [f[v] for v in order[margin + i + 6:margin + i + 12]]
            hi = [f[v] for v in order[max(0, margin + i - 12):max(0, margin + i - 6)]]
            if (lo and np.mean(lo) < 0.3) or (hi and np.mean(hi) < 0.3):
                border = True; break
    prof = gray_page[y0:y1][:, strip].__lt__(0.35 * paper).mean(axis=1) if strip else np.zeros(1)
    return {"margin": int(margin), "inked": inked, "border": border, "profile": prof}


def spread_score(left_gray, right_gray, paper, core_px_l, core_px_r):
    """(left inner edge, right inner edge) dicts; a spread = artwork reaching
    the spine on BOTH pages with NO panel border on either: the drawing is
    meant to run across."""
    el = inner_edge(left_gray, "right", core_px_l, paper)
    er = inner_edge(right_gray, "left", core_px_r, paper)
    a, b = el.pop("profile"), er.pop("profile")
    n = min(len(a), len(b))
    corr = 0.0
    if n > 10 and a[:n].std() > 1e-6 and b[:n].std() > 1e-6:
        corr = float(np.corrcoef(a[:n], b[:n])[0, 1])
    el["corr"] = er["corr"] = round(corr, 2)
    return el, er
