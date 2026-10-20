#!/usr/bin/env python3
"""Empty speech balloons / caption boxes of a generated comic page (OpenCV).

Used by make_layout.py (one text box per balloon) and separate_layers.py
(the balloon pixels become their own PSD layer). A balloon is a near-white
connected component, holes filled, solid enough (clouds, white clothes and
highlights are ragged) and big enough to hold a line of text.

    balloons = detect_balloons(img_bgr)        # reading order, ids from 1
    body = body_at(img_bgr, text_box, panel_box)   # page-sized bool: the white body under a text box
    mask = outline_mask(img_bgr, body)         # body + ink outline + tail
"""
import cv2
import numpy as np


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Component mask with its holes (stray marks, leftover letters) filled."""
    padded = np.pad(mask, 1).astype(np.uint8)
    flood = padded.copy()
    cv2.floodFill(flood, None, (0, 0), 2)
    return (flood != 2)[1:-1, 1:-1]


def inner_box(filled: np.ndarray) -> tuple[int, int, int, int]:
    """Largest centred rectangle that stays inside the balloon body (tail removed)."""
    h, w = filled.shape
    k = max(3, int(min(w, h) * 0.3)) | 1
    core = cv2.morphologyEx(filled.astype(np.uint8), cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    ys, xs = np.nonzero(core if core.any() else filled)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    cx, cy, cw, ch = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    for s in np.arange(1.0, 0.35, -0.03):
        rx0, rx1 = int(cx - cw * s / 2), int(cx + cw * s / 2)
        ry0, ry1 = int(cy - ch * s / 2), int(cy + ch * s / 2)
        if filled[ry0:ry1, rx0:rx1].mean() >= 0.985:
            break
    return rx0, ry0, max(1, rx1 - rx0), max(1, ry1 - ry0)


def white_mask(img: np.ndarray) -> np.ndarray:
    """Near-white pixels (uint8 0/1), hairline leaks between white areas cut."""
    W = img.shape[1]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 2] >= 225) & (hsv[..., 1] <= 40)).astype(np.uint8)
    k = max(3, round(W * 0.004)) | 1
    return cv2.morphologyEx(white, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))


def white_components(img: np.ndarray) -> tuple[int, np.ndarray, np.ndarray]:
    """Connected components of the near-white pixels."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white_mask(img), connectivity=4)
    return n, labels, stats


def _candidate(labels: np.ndarray, i: int, stats: np.ndarray, W: int, H: int) -> dict | None:
    x, y, w, h, area = stats[i]
    if x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2:        # page margin / gutters
        return None
    if not (0.0012 * W * H <= w * h and area <= 0.30 * W * H) or min(w, h) < 0.025 * W:
        return None
    filled = fill_holes(labels[y:y + h, x:x + w] == i)
    hull = cv2.convexHull(cv2.findNonZero(filled.astype(np.uint8)))
    solidity = filled.sum() / max(cv2.contourArea(hull), 1)
    if solidity < 0.80 or filled.sum() < 0.0010 * W * H:             # ragged = clouds, clothes, highlights
        return None
    bx, by, bw, bh = inner_box(filled)
    if bw * bh < 0.0025 * W * H or min(bw, bh) < 0.035 * W:          # no room for a line of text
        return None
    return {"bbox": [int(x), int(y), int(w), int(h)],
            "text_box": [int(x + bx), int(y + by), int(bw), int(bh)],
            "shape": "box" if filled.sum() / (w * h) > 0.92 else "balloon",
            "solidity": round(float(solidity), 2), "mask": filled}


def reading_order(found: list[dict], H: int) -> list[dict]:
    """Rows of balloons whose centres are within 7% of the page height, left to right."""
    found.sort(key=lambda b: b["bbox"][1] + b["bbox"][3] / 2)
    rows: list[list[dict]] = []
    for b in found:
        cy = b["bbox"][1] + b["bbox"][3] / 2
        if rows and cy - (rows[-1][0]["bbox"][1] + rows[-1][0]["bbox"][3] / 2) < 0.07 * H:
            rows[-1].append(b)
        else:
            rows.append([b])
    ordered = [b for row in rows for b in sorted(row, key=lambda b: b["bbox"][0])]
    for idx, b in enumerate(ordered, 1):
        b["id"] = idx
    return ordered


def detect_balloons(img: np.ndarray) -> list[dict]:
    """Every empty balloon of the page: bbox, text_box, shape, id (reading
    order) and "mask" (bool array the size of the bbox: the white body)."""
    H, W = img.shape[:2]
    n, labels, stats = white_components(img)
    found = [c for i in range(1, n) if (c := _candidate(labels, i, stats, W, H))]
    return reading_order(found, H)


def body_at(img: np.ndarray, text_box: list[int], clip: list[int]) -> np.ndarray | None:
    """Page-sized bool mask of the white balloon body under a text box (the
    layout's box, so hand-added balloons count and deleted false positives do
    not): the white area flooded from the box, kept inside `clip` (the panel,
    so a balloon leaking into the gutter stops at the panel edge). None when
    there is no white under the box."""
    H, W = img.shape[:2]
    white = white_mask(img)
    cx, cy, cw, ch = clip
    window = np.zeros((H, W), np.uint8)
    window[cy:cy + ch, cx:cx + cw] = 1
    white &= window
    x, y, w, h = text_box
    ys, xs = np.nonzero(white[y:y + h, x:x + w])
    if len(xs) == 0:
        return None
    centre = np.argmin((xs - w / 2) ** 2 + (ys - h / 2) ** 2)
    cv2.floodFill(white, None, (int(x + xs[centre]), int(y + ys[centre])), 2)
    return fill_holes(white == 2)


def outline_mask(img: np.ndarray, body: np.ndarray) -> np.ndarray:
    """The body (page-sized bool) plus its ink outline and tail: grown ring by
    ring while the ring is still mostly dark."""
    W = img.shape[1]
    dark = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) < 128).astype(np.uint8)
    ring_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    grown = body.astype(np.uint8)
    for _ in range(max(4, round(W * 0.012))):
        bigger = cv2.dilate(grown, ring_k)
        ring = bigger & ~grown
        if ring.sum() == 0 or (dark & ring).sum() < 0.25 * ring.sum():
            break
        grown = bigger
    return cv2.dilate(grown, ring_k).astype(bool)            # one more px: anti-aliased edge
