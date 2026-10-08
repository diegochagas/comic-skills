#!/usr/bin/env python3
"""Straighten a scanned print and cut away the white around it.

A print scanned at a slight angle leaves a white wedge along its edges, and
a print with a white border, a torn edge or a cut corner leaves white strips.
Those are not part of the photo: they are cut, not repainted.

  1. background  light, colourless, flat pixels connected to the scan's
                 edge (scanner lid, bare paper), never white inside the photo;
  2. straight    along each side, the background strip counts as a border
                 only when it runs along the whole side with a straight
                 inner edge - a clipped sky, a white wall or a shirt at the
                 edge has an irregular one and is kept;
  3. deskew      the borders' slope gives the angle (0.3 to --max-angle
                 degrees are corrected, the new corners are cut in step 4);
  4. trim        each straight border is cut, wedge included, up to
                 --max-trim of that side.

    crop.py <image> <out> [--max-angle 6] [--max-trim 0.15]
"""
import argparse

import cv2
import numpy as np

BG_MIN, BG_SPREAD, BG_GRAIN = 195, 32, 6.0   # scanner lid / bare paper: light, colourless, flat


def white_mask(bgr):
    """Scanner bed / bare paper connected to the scan's edge: light, no
    colour, no grain. White inside the photo (a dress, a wall) is not
    connected to the edge through flat background, so it never counts; a
    clipped sky at the edge keeps film grain and a little colour."""
    lo, hi = bgr.min(axis=2).astype(np.int16), bgr.max(axis=2).astype(np.int16)
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    grain = cv2.blur(np.abs(cv2.Laplacian(g, cv2.CV_32F)), (7, 7))
    m = ((lo >= BG_MIN) & (hi - lo <= BG_SPREAD) & (grain < BG_GRAIN)).astype(np.uint8)
    m = cv2.medianBlur(m * 255, 5)
    H, W = m.shape
    n, lab = cv2.connectedComponents(m, connectivity=8)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return np.isin(lab, list(edge)) if edge else np.zeros((H, W), bool)


COVER_MIN = 0.6        # a border runs along (almost) the whole side
INLIER_MIN = 0.7       # and its inner edge is a straight line for most of it


def _side_widths(wm, lim):
    """Leading background run of every line, for each side (top, bottom,
    left, right), capped at lim[k]."""
    sides = [wm.T, wm.T[:, ::-1], wm, wm[:, ::-1]]   # per column from top/bottom, per row from left/right
    out = []
    for m, L in zip(sides, lim):
        head = m[:, :L]
        out.append(np.where(head.all(axis=1), L, np.argmin(head, axis=1)))
    return out


def _straight(width):
    """(slope, cut) of a straight border strip along one side, or None.
    A sky, a wall or a white shirt at the edge has an irregular inner edge
    (or does not run along the whole side) and is left alone."""
    n = len(width)
    if (width > 0).mean() < COVER_MIN:
        return None
    # lines past the print's corner are background all the way (capped):
    # they carry no edge position
    idx = np.nonzero((width > 0) & (width < width.max()))[0] if (width == width.max()).mean() < 0.5 \
        else np.nonzero(width > 0)[0]
    if len(idx) < 0.5 * n:
        return None
    k, m = np.polyfit(idx, width[idx].astype(np.float64), 1)
    fit = k * np.arange(n) + m
    tol = max(4.0, 0.012 * n)
    if (np.abs(width[idx] - fit[idx]) <= tol).mean() < INLIER_MIN:
        return None
    # cover the strip everywhere except outliers (a torn bit, a blotch)
    cut = int(np.ceil(max(np.percentile(width[idx], 97), fit.max())))
    return k, cut


def skew_angle(bgr):
    """Degrees to rotate (cv2 convention) so the print is level, from the
    slope of the straight border strips; 0 when there are none."""
    wm = white_mask(bgr)
    H, W = wm.shape
    ws = _side_widths(wm, [int(H * 0.2), int(H * 0.2), int(W * 0.2), int(W * 0.2)])
    slopes = []
    for k, w in enumerate(ws):
        r = _straight(w)
        if r is None or r[1] < 3:
            continue
        # top / left strips widen one way along the side, bottom / right mirrored
        slopes.append((r[0] if k in (1, 2) else -r[0], len(w)))
    if not slopes:
        return 0.0
    s = sum(v * n for v, n in slopes) / sum(n for _, n in slopes)
    return float(-np.degrees(np.arctan(s)))


def rotate(bgr, angle):
    h, w = bgr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(bgr, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=(255, 255, 255))


def trim_box(bgr, max_trim=0.15):
    """(x, y, w, h) of the photo inside its straight border strips."""
    wm = white_mask(bgr)
    H, W = wm.shape
    lim = [int(H * max_trim)] * 2 + [int(W * max_trim)] * 2
    cut = [0, 0, 0, 0]          # top, bottom, left, right
    for k, w in enumerate(_side_widths(wm, lim)):
        r = _straight(w)
        if r is not None and r[1] >= 1:
            cut[k] = min(lim[k], r[1] + 2)
    t, b, l, r = cut
    return l, t, W - l - r, H - t - b


def shave(bgr, max_px=None):
    """Remove the last thin light lines at the edges (a 1-3 px seam of paper
    or scanner lid left after cutting or rotating): an outermost row or
    column is dropped while it is light and much lighter than the picture
    just inside it."""
    lo = bgr.min(axis=2).astype(np.float32)
    H, W = lo.shape
    lim = max_px or max(6, int(0.025 * min(H, W)))
    cut = [0, 0, 0, 0]
    lines = [lambda i: np.median(lo[i]), lambda i: np.median(lo[H - 1 - i]),
             lambda i: np.median(lo[:, i]), lambda i: np.median(lo[:, W - 1 - i])]
    for k, line in enumerate(lines):
        ref = np.median([line(j) for j in range(lim + 2, lim + 12)])
        i = 0
        while i < lim and line(i) >= 150 and line(i) - ref > 60:
            i += 1
        cut[k] = i
    t, b, l, r = cut
    return bgr[t:H - b, l:W - r]


def crop(bgr, max_angle=8.0, max_trim=0.15):
    """Returns (cropped image, info dict)."""
    angle = skew_angle(bgr)
    if abs(angle) >= 0.3:     # a second look at the straightened scan takes the rest
        angle += skew_angle(rotate(bgr, angle))
    out = bgr
    if 0.3 <= abs(angle) <= max_angle:
        out = rotate(bgr, angle)
    else:
        angle = 0.0
    x, y, w, h = trim_box(out, max_trim)
    if w < 0.5 * out.shape[1] or h < 0.5 * out.shape[0]:
        return bgr, dict(angle=0.0, box=(0, 0, bgr.shape[1], bgr.shape[0]), changed=False)
    out = shave(out[y:y + h, x:x + w])
    changed = bool(angle) or out.shape[:2] != bgr.shape[:2]
    return out, dict(angle=round(angle, 2), box=(x, y, w, h), changed=changed)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--max-angle", type=float, default=8.0)
    ap.add_argument("--max-trim", type=float, default=0.15)
    a = ap.parse_args()
    im = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if im is None:
        raise SystemExit(f"cannot read {a.image}")
    out, info = crop(im, a.max_angle, a.max_trim)
    cv2.imwrite(a.out, out, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"-> {a.out}  angle {info['angle']}°, box {info['box']} of {im.shape[1]}x{im.shape[0]}")


if __name__ == "__main__":
    main()
