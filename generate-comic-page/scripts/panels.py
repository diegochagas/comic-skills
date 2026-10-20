#!/usr/bin/env python3
"""Panel boxes of a generated page, for the layered PSD (one group per panel).

A page assembled from panels (assemble_page.py) has an exact
page_NN_tryK.panels.json sidecar; a page drawn in one generation gets its
panels detected: the white gutter that touches the page margin is flooded,
what remains in big solid pieces is a panel. The agent checks the overlay
(page_NN.panels.jpg) and edits "panels" in the layout JSON when the
detection is wrong (diagonal gutters, bleed panels).

    panels = page_panels(art_path, img_bgr, border_default=5)
    # [{"id": 1, "box": [x, y, w, h], "border": 5}, ...]  reading order
"""
import json
from pathlib import Path

import cv2
import numpy as np

MIN_PANEL_FRAC = 0.015          # a panel covers at least 1.5% of the page
MAX_BORDER_FRAC = 0.03          # a border is never thicker than 3% of the panel's short side


def panels_from_sidecar(art: Path, border_default: int) -> list[dict] | None:
    sidecar = art.with_suffix(".panels.json")
    if not sidecar.exists():
        return None
    panels = json.loads(sidecar.read_text(encoding="utf-8"))["panels"]
    return [{"id": p["id"], "box": list(p["box"]), "border": int(p.get("border", border_default))} for p in panels]


def gutter_mask(img: np.ndarray) -> np.ndarray:
    """Near-white pixels connected to the page margin (uint8 0/1)."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    white = (grey >= 235).astype(np.uint8)
    H, W = white.shape
    flood = np.pad(white, 1, constant_values=1)
    cv2.floodFill(flood, None, (0, 0), 2)
    return (flood == 2)[1:H + 1, 1:W + 1].astype(np.uint8)


def _panel_boxes(img: np.ndarray) -> list[list[int]]:
    H, W = img.shape[:2]
    k = max(3, round(W * 0.006)) | 1
    solid = cv2.morphologyEx(1 - gutter_mask(img), cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=4)
    boxes = [[int(x), int(y), int(w), int(h)] for x, y, w, h, _a in stats[1:] if w * h >= MIN_PANEL_FRAC * W * H]
    return boxes or [[0, 0, W, H]]


def _reading_order(boxes: list[list[int]], H: int) -> list[list[int]]:
    boxes.sort(key=lambda b: b[1])
    rows: list[list[list[int]]] = []
    for b in boxes:
        if rows and b[1] - rows[-1][0][1] < 0.05 * H:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [b for row in rows for b in sorted(row, key=lambda b: b[0])]


def _edge_run(dark: np.ndarray, axis: int, reverse: bool, limit: int) -> int:
    """Consecutive rows (axis 0) or columns (axis 1) from one edge that are mostly dark."""
    lines = dark.mean(axis=1 - axis)
    if reverse:
        lines = lines[::-1]
    run = 0
    for v in lines[:limit]:
        if v < 0.6:
            break
        run += 1
    return run


def border_width(img: np.ndarray, box: list[int]) -> int:
    """Thickness of the drawn frame just inside the box (median of the 4 sides; 0 = no frame)."""
    x, y, w, h = box
    dark = (cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY) < 100).astype(np.float32)
    limit = max(1, int(min(w, h) * MAX_BORDER_FRAC))
    mx, my = int(w * 0.1), int(h * 0.1)
    runs = [_edge_run(dark[my:h - my], 1, False, limit), _edge_run(dark[my:h - my], 1, True, limit),
            _edge_run(dark[:, mx:w - mx], 0, False, limit), _edge_run(dark[:, mx:w - mx], 0, True, limit)]
    return int(np.median(runs))


def detect_panels(img: np.ndarray) -> list[dict]:
    H = img.shape[0]
    boxes = _reading_order(_panel_boxes(img), H)
    return [{"id": i, "box": b, "border": border_width(img, b)} for i, b in enumerate(boxes, 1)]


def page_panels(art: Path, img: np.ndarray, border_default: int) -> list[dict]:
    """Sidecar panels when the page was assembled from panels, else detected ones."""
    return panels_from_sidecar(art, border_default) or detect_panels(img)


def draw_panel_overlay(img: np.ndarray, panels: list[dict], dst: Path) -> None:
    out = img.copy()
    t = max(2, round(img.shape[1] * 0.003))
    for p in panels:
        x, y, w, h = p["box"]
        cv2.rectangle(out, (x, y), (x + w - 1, y + h - 1), (0, 200, 0), t)
        cv2.putText(out, f"P{p['id']} b{p['border']}", (x + 8, y + 36), cv2.FONT_HERSHEY_SIMPLEX,
                    img.shape[1] / 1200, (0, 200, 0), max(2, t), cv2.LINE_AA)
    scale = min(1.0, 1600 / max(out.shape[:2]))
    if scale < 1:
        out = cv2.resize(out, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dst), out, [cv2.IMWRITE_JPEG_QUALITY, 85])
