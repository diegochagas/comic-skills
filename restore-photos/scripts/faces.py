#!/usr/bin/env python3
"""Keep faces the original's.

The model redraws faces it touches - a mustache disappears, a child gets
another face. Inside a face the model may therefore only fill pixels that are
really missing - the white of lost emulsion, and flakes that are clearly
lighter than the model's repair; eyes, mouth, hair and the shape of the face
stay the scan's.

Faces are found with the Haar cascades that ship with OpenCV, run on the
model's output (where the face is already clean and easy to find) and on the
scan, and kept only when the box holds enough skin tone - that drops the
cascades' hits on floors, chairs and toys. Nothing is downloaded.

    faces.py <image> <out.jpg>     draws the faces found (for a quick check)
"""
import argparse

import cv2
import numpy as np

CASCADES = (("haarcascade_frontalface_alt2.xml", 3),
            ("haarcascade_frontalface_default.xml", 8),
            ("haarcascade_profileface.xml", 5))
SKIN_MIN = 0.25    # share of skin-toned pixels a face box must hold
GROW = 0.15        # the protected ellipse is this much larger than the box
_loaded = None


def _cascades():
    global _loaded
    if _loaded is None:
        _loaded = [(cv2.CascadeClassifier(cv2.data.haarcascades + f), n, "profile" in f) for f, n in CASCADES]
    return _loaded


def skin(bgr):
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    return (ycc[:, :, 1] >= 133) & (ycc[:, :, 1] <= 175) & (ycc[:, :, 2] >= 75) & (ycc[:, :, 2] <= 130)


def _detect(bgr):
    g = cv2.equalizeHist(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    ms = max(14, min(g.shape) // 30)
    out = []
    for c, neighbours, profile in _cascades():
        for img, flip in ((g, False), (cv2.flip(g, 1), True)) if profile else ((g, False),):
            for (x, y, w, h) in c.detectMultiScale(img, 1.06, neighbours, minSize=(ms, ms)):
                out.append((int(g.shape[1] - x - w) if flip else int(x), int(y), int(w), int(h)))
    return out


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy / float(min(aw * ah, bw * bh))


def find(orig, gen=None):
    """Face boxes (x, y, w, h) in orig's coordinates; gen must be aligned to orig."""
    cands = _detect(orig) + (_detect(gen) if gen is not None else [])
    ref = gen if gen is not None else orig
    sk = skin(ref)
    kept = []
    for b in sorted(cands, key=lambda b: -b[2] * b[3]):
        x, y, w, h = b
        if sk[y:y + h, x:x + w].mean() < SKIN_MIN:
            continue
        if any(_overlap(b, k) > 0.4 for k in kept):
            continue
        kept.append(b)
    return kept


FLAKE_LIGHTER = 10   # a flake is at least this much lighter (LAB L) than the model's repair


def missing(orig, gen=None):
    """Pixels where the print's picture is gone: bright and colourless, or -
    with the model's aligned output - clearly lighter than the repair (white
    and gold flakes of lost emulsion are always lighter than the picture
    under them; a mustache, an eyebrow or a shadow the model would lighten
    is darker in the scan and is kept)."""
    hsv = cv2.cvtColor(orig, cv2.COLOR_BGR2HSV)
    m = (hsv[:, :, 2] >= 190) & (hsv[:, :, 1] <= 50)
    if gen is not None:
        lo = cv2.cvtColor(orig, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.int16)
        lg = cv2.cvtColor(gen, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.int16)
        m |= (lo - lg) > FLAKE_LIGHTER
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return cv2.dilate(m, np.ones((5, 5), np.uint8)) > 0


def area(shape, boxes):
    """Boolean map of the protected face ellipses."""
    face = np.zeros(shape, np.uint8)
    for x, y, w, h in boxes:
        cv2.ellipse(face, (x + w // 2, y + h // 2), (int(w * (0.5 + GROW)), int(h * (0.6 + GROW))),
                    0, 0, 360, 255, -1)
    return face > 0


def guard(mask, orig, boxes, gen=None):
    """mask with every face limited to its missing pixels."""
    if not boxes:
        return mask
    out = mask.copy()
    out[area(mask.shape, boxes) & ~missing(orig, gen)] = 0
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("out")
    a = ap.parse_args()
    im = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if im is None:
        raise SystemExit(f"cannot read {a.image}")
    boxes = find(im)
    for x, y, w, h in boxes:
        cv2.rectangle(im, (x, y), (x + w, y + h), (255, 128, 0), 2)
    cv2.imwrite(a.out, im)
    print(f"{len(boxes)} face(s) -> {a.out}")


if __name__ == "__main__":
    main()
