#!/usr/bin/env python3
"""Alignment of the two halves of a spread (split-scans' spread_seam.py).

From the straight lines that cross the fold (speed lines, constellation
lines, panel borders, rays): each one must continue on the other page at
the same angle and height (fit_lines). Without 3 such lines, from the two
edges' grey profiles: shift only (_profile_alignment).

    from spread_align import find_alignment
    find_alignment(left_bgr, right_bgr)
    # {'theta': 0.18, 'gap': 0, 'dy': 0, 'pairs': 7, 'lines': 7, 'method': 'lines'}

The model: the right page is turned by theta degrees about the middle of
its inner edge (point (0, h/2)), then placed gap px right of the left page
and dy px lower. Measured on Soul of Gold 056-057 (two pages scanned
separately): theta 0.08-0.32 deg as the search bounds change (0.395 in the
spread Diego assembled by hand in Photoshop), gap 0, dy within 1 px, every
line within 2.5 px at the fold.
"""
import cv2
import numpy as np

# sizes as shares of the page height (measured on 2512 px pages, so they also
# hold for a small or low-resolution scan)
STRIP = 0.28         # searched for lines from the inner edge (700 px)
SKIP = (0.006, 0.036)  # next to the fold, not searched (left, right page): blur, curved paper (15, 90 px)
MIN_LEN = 0.064      # shortest segment taken as a line (160 px)
MAX_SLOPE = 2.5      # steeper lines run along the fold and say nothing about the height
SAME_LINE_DEG = 0.35  # max angle difference between the two halves of one line
THETA_RANGE = 1.5    # degrees of rotation searched either way
THETA_STEP = 0.02
FIT_TOL = 2.5        # px, a pair agrees with the fit within this
GAP_COST = 3.0       # score a gap must earn per px (line length units)
MIN_PAIRS = 3
EDGE_SKIP = 14       # profiles: px of each inner edge not trusted (shadow, blur)
MAX_GAP = 90         # profiles: px, the widest strip searched
MAX_DY_FRAC = 0.09   # profiles: vertical shift searched, as a share of the page height
PROFILE_MAX_SLOPE = 2.5  # profiles: steeper edges say nothing about the gap
PROFILE_GAP_COST = 0.0006  # profiles: score a gap must earn per px
MIN_GAIN = 0.03      # profiles: how much better than gap 0 a gap must score
REFINE_PX = 3        # px either side of a Hough segment whose pixels re-fit the line
FOLD_SEARCH = 0.15   # share of the width either side of the middle where a scan's fold is looked for
FOLD_EDGE = 12.0     # grey levels/px a column must step by, on a row, to count as the fold there


def straight_lines(img, x0, x1, min_len):
    """[(angle deg, slope, intercept, length)] of the long straight lines in
    columns x0..x1: thin light lines on dark ground and ink lines on paper,
    each line once (its two edges merged)."""
    gray = cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2GRAY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    light = (cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel) > 55) & (gray > 150)
    ink = (cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel) > 55) & (gray < 110)
    found = []
    for mask in (light, ink):
        m = mask.astype(np.uint8) * 255
        m[:, :max(0, x0)] = 0
        m[:, x1:] = 0
        segs = cv2.HoughLinesP(m, 1, np.pi / 720, int(min_len / 2), minLineLength=int(min_len), maxLineGap=12)
        found += [] if segs is None else [_refine(m, *s) for s in segs[:, 0] if s[2] != s[0]]
    return _dedupe([f for f in found if f and abs(f[1]) <= MAX_SLOPE])


def _refine(mask, x1, y1, x2, y2):
    """(angle, slope, intercept, length) of a Hough segment, re-fitted by
    least squares to the line's own pixels: Hough's angle steps (0.25 deg)
    are as big as the turn being measured."""
    a = (y2 - y1) / (x2 - x1)
    b = y1 - a * x1
    lo, hi = min(x1, x2), max(x1, x2)
    ys, xs = np.nonzero(mask[:, lo:hi + 1])
    xs = xs + lo
    near = np.abs(ys - (a * xs + b)) <= REFINE_PX * np.hypot(1, a)
    if near.sum() < 20:
        return None
    a, b = np.polyfit(xs[near], ys[near], 1)
    return float(np.degrees(np.arctan(a))), float(a), float(b), float(np.hypot(x2 - x1, y2 - y1))


def _dedupe(lines):
    kept = []
    for ln in sorted(lines, key=lambda t: -t[3]):
        if all(abs(ln[0] - k[0]) > 0.4 or abs(ln[2] - k[2]) > 8 * np.hypot(1, ln[1]) for k in kept):
            kept.append(ln)
    return kept


def _gray(img):
    return cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2GRAY).astype(np.float32)


def _right_height(line, theta_deg, cy):
    """Height at x=0 of a right-page line after the page is turned about (0, cy)."""
    _, a, b, _ = line
    th = np.radians(theta_deg)
    x, y = -(b - cy) * np.sin(th), cy + (b - cy) * np.cos(th)
    return y - np.tan(np.arctan(a) + th) * x


def _residuals(left_lines, right_lines, theta, gap, lw, cy):
    """(height differences, weights) of each left line against its best
    right partner, for the right page turned by theta and placed gap px away."""
    res, weight = [], []
    for ang_l, a_l, b_l, len_l in left_lines:
        y_l = a_l * (lw + gap) + b_l
        partners = [(y_l - _right_height(r, theta, cy), min(len_l, r[3]))
                    for r in right_lines if abs(r[0] + theta - ang_l) <= SAME_LINE_DEG]
        if partners:
            r, w = min(partners, key=lambda t: abs(t[0]))
            res.append(r)
            weight.append(w)
    return np.array(res), np.array(weight)


def fit_lines(left, right, max_gap=60):
    """{'theta', 'gap', 'dy', 'pairs', 'lines'} or None (fewer than MIN_PAIRS
    lines cross the fold). dy = px the right page moves down."""
    lw, h, cy = left.shape[1], right.shape[0], right.shape[0] / 2
    strip, min_len = int(STRIP * h), max(30.0, MIN_LEN * h)
    left_lines = straight_lines(left, lw - strip, lw - int(SKIP[0] * h) - 1, min_len)
    right_lines = straight_lines(right, int(SKIP[1] * h), strip, min_len)
    best = None
    for theta in np.arange(-THETA_RANGE, THETA_RANGE + 1e-9, THETA_STEP):
        for gap in range(max_gap + 1):
            res, weight = _residuals(left_lines, right_lines, theta, gap, lw, cy)
            if len(res) < MIN_PAIRS:
                continue
            dy = float(np.median(res))
            agree = np.abs(res - dy) < FIT_TOL
            score = weight[agree].sum() - GAP_COST * gap
            if agree.sum() >= MIN_PAIRS and (best is None or score > best[0]):
                best = (score, theta, gap, dy, int(agree.sum()), len(res))
    if best is None:
        return None
    _, theta, gap, dy, pairs, lines = best
    theta, dy = _polish(left_lines, right_lines, theta, gap, dy, lw, cy)
    return {"theta": round(float(theta), 3), "gap": int(gap), "dy": round(dy, 1), "pairs": pairs, "lines": lines}


def _polish(left_lines, right_lines, theta, gap, dy, lw, cy):
    """(theta, dy) from the pairs that agree with the coarse fit: theta =
    the length-weighted median of their angle differences (heights at the
    fold barely depend on a turn this small: 2 px for 0.25 deg on 056-057),
    dy = the median height difference at that theta."""
    diffs, weights = [], []
    for ang_l, a_l, b_l, len_l in left_lines:
        y_l = a_l * (lw + gap) + b_l
        for r in right_lines:
            if abs(r[0] + theta - ang_l) <= SAME_LINE_DEG and abs(y_l - _right_height(r, theta, cy) - dy) < FIT_TOL:
                diffs.append(ang_l - r[0])
                weights.append(min(len_l, r[3]))
    if len(diffs) < MIN_PAIRS:
        return theta, dy
    order = np.argsort(diffs)
    cum = np.cumsum(np.array(weights)[order])
    theta = float(np.array(diffs)[order][np.searchsorted(cum, cum[-1] / 2)])
    res, _ = _residuals(left_lines, right_lines, theta, gap, lw, cy)
    near = res[np.abs(res - dy) < 2 * FIT_TOL]
    return theta, float(np.median(near)) if len(near) else dy


def _edge_profile(gray, x):
    """(intensity, slope dy/dx, weight) per row for the lines crossing column x."""
    lo, hi = max(0, x - 24), min(gray.shape[1], x + 25)
    band = cv2.GaussianBlur(gray[:, lo:hi], (0, 0), 1.2)
    gx, gy = cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(band, cv2.CV_32F, 0, 1, ksize=3)
    jxx, jxy, jyy = (cv2.GaussianBlur(j, (0, 0), 6)[:, x - lo] for j in (gx * gx, gx * gy, gy * gy))
    theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy)          # gradient direction
    sin = np.sin(theta)
    slope = np.where(np.abs(sin) > 1e-3, -np.cos(theta) / np.where(np.abs(sin) > 1e-3, sin, 1), 1e6)
    coherence = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-3)
    usable = np.abs(slope) <= PROFILE_MAX_SLOPE
    weight = np.where(usable, coherence * np.sqrt(jxx + jyy), 0.0)
    return band[:, x - lo], np.where(usable, slope, 0.0), weight / (weight.max() + 1e-6)


def _match(src, slope, weight, dst, reach, dy):
    """Weighted correlation of src rows with the dst rows their lines reach."""
    rows = np.arange(len(src), dtype=np.float32)
    target = rows + slope * reach + dy
    ok = (target >= 0) & (target <= len(dst) - 1) & (weight > 0.05)
    if ok.sum() < 200:
        return -1.0
    a, w = src[ok], weight[ok]
    b = np.interp(target[ok], np.arange(len(dst)), dst)
    a, b = a - np.average(a, weights=w), b - np.average(b, weights=w)
    den = np.sqrt(np.average(a * a, weights=w) * np.average(b * b, weights=w))
    return float(np.average(a * b, weights=w) / den) if den > 1e-6 else -1.0


def find_alignment(left, right, max_gap=MAX_GAP, max_dy=None):
    """{'theta', 'dy', 'gap', 'method', ...}: theta = degrees the right page
    is turned about the middle of its inner edge, dy = rows it moves down.
    Straight lines across the fold first (fit_lines), else the
    edges' grey profiles. max_dy (px) narrows the search, e.g. for two
    halves cut from one scan.
    Example: find_alignment(p57, p56) -> {'theta': 0.38, 'dy': -1, 'gap': 0, ...}"""
    fit = fit_lines(left, right)
    if fit and (max_dy is None or abs(fit["dy"]) <= max_dy):
        return {**fit, "dy": int(round(fit["dy"])), "method": "lines"}
    # no lines: the grey profiles give the shift, but their gap is not trusted
    # (084-085: 22 px claimed where the strap line already met with none)
    found = _profile_alignment(left, right, max_gap, max_dy)
    return {**found, "gap": 0, "profile_gap": found["gap"], "theta": 0.0, "method": "profile"}


def _profile_alignment(left, right, max_gap, max_dy):
    """{'dy', 'gap', 'score', 'score_gap0'} from the two edges' grey profiles."""
    gl, gr = _gray(left), _gray(right)
    pl, sl, wl = _edge_profile(gl, gl.shape[1] - 1 - EDGE_SKIP)
    pr, sr, wr = _edge_profile(gr, EDGE_SKIP)
    max_dy = int(MAX_DY_FRAC * max(len(pl), len(pr))) if max_dy is None else max_dy
    best, flat = (-2.0, 0, 0), {}
    for gap in range(0, max_gap + 1, 2):
        reach = 2 * EDGE_SKIP + gap
        for dy in range(-max_dy, max_dy + 1, 2):
            # dy moves the right page down: left row y meets right row y - dy
            score = 0.5 * (_match(pl, sl, wl, pr, reach, -dy) + _match(pr, sr, wr, pl, -reach, dy))
            flat[gap] = max(flat.get(gap, -2.0), score)
            if score - PROFILE_GAP_COST * gap > best[0]:
                best = (score - PROFILE_GAP_COST * gap, dy, gap)
    _, dy, gap = best
    if flat[gap] - flat[0] < MIN_GAIN:
        gap = 0
        dy = max(range(-max_dy, max_dy + 1, 2), key=lambda d: _match(pl, sl, wl, pr, 2 * EDGE_SKIP, -d)
                 + _match(pr, sr, wr, pl, -2 * EDGE_SKIP, d))
    return {"dy": int(dy), "gap": int(gap), "score": round(flat[gap], 3), "score_gap0": round(flat[0], 3)}


def fold_column(img):
    """Column of the fold in one scan that holds both pages: the column near
    the middle where the most rows have a vertical edge (the paper bends
    there, so lines on the two sides do not meet).
    Example: fold_column(cv2.imread('084-085.png')) -> 1863"""
    h, w = img.shape[:2]
    gray = cv2.GaussianBlur(_gray(img), (0, 0), 1.5)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    x0, x1 = int(w * (0.5 - FOLD_SEARCH)), int(w * (0.5 + FOLD_SEARCH))
    rows = slice(int(h * 0.05), int(h * 0.95))
    vertical = (gx[rows, x0:x1] > FOLD_EDGE) & (gx[rows, x0:x1] > 2 * gy[rows, x0:x1])
    return x0 + int(np.argmax(vertical.mean(axis=0)))
