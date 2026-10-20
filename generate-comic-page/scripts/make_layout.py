#!/usr/bin/env python3
"""Draft the lettering layout of a page: find the EMPTY balloons/caption boxes
of the generated art and pair them with the page's exact text lines, ready
for build_psd.py to turn into editable Photoshop text boxes; and find the
panels, so build_psd.py can split the art into one layer group per panel.

Writes, in <project>/work/<issue>/layout/:
  page_NN.balloons.jpg   the art with every detected balloon numbered (red =
                         the text box that will be used) - LOOK at it
  page_NN.panels.jpg     the panel boxes (green, "P1 b5" = panel 1, border 5 px)
                         - exact from the panels.json sidecar of a page
                         assembled from panels, detected otherwise - LOOK at it
  page_NN.layout.json    draft layout: "panels" (box + border per panel) and
                         one text layer "Balloon K" per detected balloon, in
                         reading order, pre-filled with the job's
                         dialogue_exact lines in script order

The pairing is only a first guess (reading order vs script order). The agent
fixes the layout JSON after looking at the numbered overlay: move texts to
the right balloon, delete false positives (white shapes that are not
balloons), add a box by hand for a balloon the detector missed (one that
leaks into the white gutter, or is not white). Texts are copied from the job
programmatically - never retype them.

Cover / editorial pages have no balloons and no panels (the art is one
full-bleed layer group): the draft just lists their text lines stacked in
placeholder boxes to be positioned over the art.

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
from balloons import detect_balloons
from common import load_job, page_kind, resolve_project
from panels import draw_panel_overlay, page_panels

DEFAULT_FONT = "CCWildWords Regular"


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


def text_layer(name: str, text: str, box: list[int], font: str) -> dict:
    return {"name": name, "text": text, "box": box, "font": font, "color": "#000000", "align": "center", "valign": "middle"}


def draft_story(img: np.ndarray, art: Path, ldir: Path, stem: str, texts: list[str], font: str,
                cfg: dict) -> tuple[list[dict], list[dict]]:
    """Panels + one text layer per detected balloon, paired with the lines (prints the detection)."""
    H, W = img.shape[:2]
    panels = page_panels(art, img, int(cfg.get("panel_border", 5)))
    draw_panel_overlay(img, panels, ldir / f"{stem}.panels.jpg")
    print(f"{len(panels)} panel(s): " + ", ".join(f"P{p['id']} box={p['box']} border={p['border']}" for p in panels))
    print(f"panels overlay: {ldir / f'{stem}.panels.jpg'}")
    balloons = detect_balloons(img)
    scale = draw_overlay(img, balloons, ldir / f"{stem}.balloons.jpg")
    paired = pair_by_panel(art, balloons)
    if paired is not None:
        print("panel-mode page: lines paired per panel (panels.json sidecar)")
        texts = [s for p in json.loads(art.with_suffix(".panels.json").read_text(encoding="utf-8"))["panels"]
                 for s in p["dialogue"]] or texts
    layers = []
    for b in balloons:
        text = paired.get(b["id"], "") if paired is not None else (texts[b["id"] - 1] if b["id"] <= len(texts) else "")
        layers.append(text_layer(f"Balloon {b['id']:02d}", text or "TODO", b["text_box"], font))
    report_detection(balloons, texts, paired, ldir / f"{stem}.balloons.jpg", W, H, scale)
    return panels, layers


def report_detection(balloons: list[dict], texts: list[str], paired: dict | None, overlay: Path,
                     W: int, H: int, scale: float) -> None:
    print(f"{len(balloons)} balloon(s) detected, {len(texts)} text line(s) in the script"
          + ("" if len(balloons) == len(texts) else "  <-- COUNT MISMATCH: fix the layout by hand"))
    for b in balloons:
        print(f"  {b['id']:>2}  {b['shape']:<7} text_box={b['text_box']}")
    print(f"overlay: {overlay}  (art is {W}x{H}; overlay px / {scale:.3f} = art px)")
    placed = set(paired.values()) if paired is not None else set(texts[:len(balloons)])
    for extra in texts:
        if extra not in placed:
            print(f"  UNPLACED: {extra!r}")


def draft_display(kind: str, texts: list[str], font: str, W: int, H: int) -> list[dict]:
    """Cover / editorial: the text lines stacked in placeholder boxes to be positioned over the art."""
    step = int(H * 0.8 / max(len(texts), 1))
    layers = [text_layer(f"Text {i + 1:02d}", text, [int(W * 0.08), int(H * 0.1) + i * step, int(W * 0.84), max(20, step - 10)], font)
              for i, text in enumerate(texts)]
    print(f"{kind}: {len(texts)} text line(s) stacked in placeholder boxes — position each one over the art")
    return layers


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

    if kind == "story":
        panels, layers = draft_story(img, art, ldir, stem, texts, font, cfg)
    else:
        panels, layers = [], draft_display(kind, texts, font, W, H)

    layout_path = ldir / f"{stem}.layout.json"
    if layout_path.exists() and not a.force:
        print(f"kept existing {layout_path} (use --force to overwrite it with a fresh draft)")
        return
    layout = {
        "art": str(art),
        "out": str(pdir / "out" / "psd" / a.issue / f"{stem}.psd"),
        "preview": str(ldir / f"{stem}_preview.jpg"),
        "units": "px",
        "kind": kind,
        "panels": panels,
        "layers": layers,
    }
    if kind == "story":
        layout.update(uniform_size=True, max_size=round(H * 0.016))
    layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"layout draft: {layout_path}")


if __name__ == "__main__":
    main()
