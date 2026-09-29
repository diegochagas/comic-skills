#!/usr/bin/env python3
"""Draft the lettering layout of a page: find the EMPTY balloons/caption boxes
of the generated art and pair them with the page's exact text lines, ready
for build_xcf.py to turn into editable GIMP text boxes.

Writes, in <project>/work/<issue>/layout/:
  page_NN.balloons.jpg   the art with every detected balloon numbered (red =
                         the text box that will be used) - LOOK at it
  page_NN.layout.json    draft layout: one text layer "Balloon K" per detected
                         balloon, in reading order, pre-filled with the
                         job's dialogue_exact lines in script order

The pairing is only a first guess (reading order vs script order). The agent
fixes the layout JSON after looking at the numbered overlay: move texts to
the right balloon, delete false positives (white shapes that are not
balloons), add a box by hand for a balloon the detector missed (one that
leaks into the white gutter, or is not white). Texts are copied from the job
programmatically - never retype them.

Cover / editorial pages have no balloons: the draft just lists their text
lines stacked in placeholder boxes to be positioned over the art.

Usage: make_layout.py -p <project> <issue> <page> [--art IMG] [--force]
  --art    attempt to letter (default: the latest work/<issue>/gen/page_NN_tryK.png)
  --force  overwrite an existing page_NN.layout.json (default: keep the
           agent's edits, only refresh the overlay + print the detection)
"""
import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_job, page_kind, resolve_project

DEFAULT_FONT = "CCWildWords Regular"


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


def detect_balloons(img: np.ndarray) -> list[dict]:
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 2] >= 225) & (hsv[..., 1] <= 40)).astype(np.uint8)
    k = max(3, round(W * 0.004)) | 1                       # cut hairline leaks between white areas
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=4)
    found = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2:   # page margin / gutters
            continue
        if not (0.0012 * W * H <= w * h and area <= 0.30 * W * H) or min(w, h) < 0.025 * W:
            continue
        filled = fill_holes(labels[y:y + h, x:x + w] == i)
        hull = cv2.convexHull(cv2.findNonZero(filled.astype(np.uint8)))
        solidity = filled.sum() / max(cv2.contourArea(hull), 1)
        if solidity < 0.80 or filled.sum() < 0.0010 * W * H:        # ragged = clouds, clothes, highlights
            continue
        bx, by, bw, bh = inner_box(filled)
        if bw * bh < 0.0025 * W * H or min(bw, bh) < 0.035 * W:     # no room for a line of text
            continue
        found.append({"bbox": [int(x), int(y), int(w), int(h)],
                      "text_box": [int(x + bx), int(y + by), int(bw), int(bh)],
                      "shape": "box" if filled.sum() / (w * h) > 0.92 else "balloon",
                      "solidity": round(float(solidity), 2)})
    # reading order: rows of balloons whose centres are within 7% of the page height
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


def draw_overlay(img: np.ndarray, balloons: list[dict], dst: Path) -> float:
    """Returns overlay px / art px."""
    out = img.copy()
    t = max(2, round(img.shape[1] * 0.003))
    for b in balloons:
        x, y, w, h = b["bbox"]
        cv2.rectangle(out, (x, y), (x + w, y + h), (255, 160, 0), t)
        tx, ty, tw, th = b["text_box"]
        cv2.rectangle(out, (tx, ty), (tx + tw, ty + th), (0, 0, 255), t)
        scale = img.shape[1] / 900
        cv2.putText(out, str(b["id"]), (tx + 4, ty + int(30 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, (0, 0, 255), max(2, t), cv2.LINE_AA)
    scale = min(1.0, 1600 / max(out.shape[:2]))
    if scale < 1:
        out = cv2.resize(out, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dst), out, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return scale


def pair_by_panel(art: Path, balloons: list[dict]) -> dict[int, str] | None:
    """Page assembled from panels (assemble_page.py sidecar): each balloon gets
    the next line of the panel it sits in, in reading order."""
    sidecar = art.with_suffix(".panels.json")
    if not sidecar.exists():
        return None
    panels = json.loads(sidecar.read_text(encoding="utf-8"))["panels"]
    queue = {p["id"]: list(p["dialogue"]) for p in panels}
    out: dict[int, str] = {}
    for b in balloons:
        cx, cy = b["bbox"][0] + b["bbox"][2] / 2, b["bbox"][1] + b["bbox"][3] / 2
        for p in panels:
            x, y, w, h = p["box"]
            if x <= cx < x + w and y <= cy < y + h:
                if queue[p["id"]]:
                    out[b["id"]] = queue[p["id"]].pop(0)
                break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", "-p", default=None)
    ap.add_argument("issue")
    ap.add_argument("page", type=int)
    ap.add_argument("--art")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    _name, cfg, pdir = resolve_project(a.project)
    job = load_job(pdir, a.issue, a.page)
    kind = job.get("kind") or page_kind(cfg, a.page, job.get("title", ""))
    if a.art:
        art = Path(a.art).expanduser().resolve()
    else:
        gens = [(int(m.group(1)), f) for f in (pdir / "work" / a.issue / "gen").glob(f"page_{a.page:02d}_try*.png")
                if (m := re.search(r"_try(\d+)\.png$", f.name))]
        if not gens:
            sys.exit("No generated art for this page yet — run gen_page.py first")
        art = max(gens)[1]
    img = cv2.imread(str(art))
    if img is None:
        sys.exit(f"Cannot read {art}")
    H, W = img.shape[:2]

    ldir = pdir / "work" / a.issue / "layout"
    ldir.mkdir(parents=True, exist_ok=True)
    stem = f"page_{a.page:02d}"
    font = cfg.get("lettering_font", DEFAULT_FONT)
    texts = job.get("dialogue_exact", [])

    layers = []
    if kind == "story":
        balloons = detect_balloons(img)
        scale = draw_overlay(img, balloons, ldir / f"{stem}.balloons.jpg")
        paired = pair_by_panel(art, balloons)
        if paired is not None:
            print("panel-mode page: lines paired per panel (panels.json sidecar)")
            texts = [s for p in json.loads(art.with_suffix(".panels.json").read_text(encoding="utf-8"))["panels"]
                     for s in p["dialogue"]] or texts
        for b in balloons:
            if paired is not None:
                text = paired.get(b["id"], "")
            else:
                text = texts[b["id"] - 1] if b["id"] <= len(texts) else ""
            layers.append({"name": f"Balloon {b['id']:02d}", "text": text or "TODO", "box": b["text_box"],
                           "font": font, "color": "#000000", "align": "center", "valign": "middle"})
        print(f"{len(balloons)} balloon(s) detected, {len(texts)} text line(s) in the script"
              + ("" if len(balloons) == len(texts) else "  <-- COUNT MISMATCH: fix the layout by hand"))
        for b in balloons:
            print(f"  {b['id']:>2}  {b['shape']:<7} text_box={b['text_box']}")
        print(f"overlay: {ldir / f'{stem}.balloons.jpg'}  (art is {W}x{H}; overlay px / {scale:.3f} = art px)")
        placed = set(paired.values()) if paired is not None else set(texts[:len(balloons)])
        for extra in texts:
            if extra not in placed:
                print(f"  UNPLACED: {extra!r}")
    else:
        step = int(H * 0.8 / max(len(texts), 1))
        for i, text in enumerate(texts):
            layers.append({"name": f"Text {i + 1:02d}", "text": text,
                           "box": [int(W * 0.08), int(H * 0.1) + i * step, int(W * 0.84), max(20, step - 10)],
                           "font": font, "color": "#000000", "align": "center", "valign": "middle"})
        print(f"{kind}: {len(texts)} text line(s) stacked in placeholder boxes — position each one over the art")

    layout_path = ldir / f"{stem}.layout.json"
    if layout_path.exists() and not a.force:
        print(f"kept existing {layout_path} (use --force to overwrite it with a fresh draft)")
        return
    layout = {
        "art": str(art),
        "out": str(pdir / "out" / "xcf" / a.issue / f"{stem}.xcf"),
        "preview": str(ldir / f"{stem}_preview.jpg"),
        "units": "px",
        "layers": layers,
    }
    if kind == "story":
        layout.update(uniform_size=True, max_size=round(H * 0.016))
    layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"layout draft: {layout_path}")


if __name__ == "__main__":
    main()
