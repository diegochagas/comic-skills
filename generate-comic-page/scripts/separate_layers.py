#!/usr/bin/env python3
"""Split the flat art of a page into the layers of the PSD, one set per panel:

  background   the panel art with its balloons, border and characters
               removed (LaMa inpaint, INPAINT=qwen for the local Qwen model)
  characters   the characters cut out by BiRefNet (local ComfyUI), soft alpha
  balloons     the empty balloons (white body + ink outline + tail), alpha
  border       the drawn frame of the panel, alpha

Flattened in that order the layers give back the approved art (the matte is
soft, so edges can differ by a pixel). Free: no credits, local models only.

Writes <project>/work/<issue>/layers/page_NN/: panel_KK_<layer>.png,
layers.json (what build_psd.mjs reads) and contact.jpg - LOOK at the contact
sheet: a panel whose "characters" cut is wrong (no character in it, half a
body) gets "characters": false in that panel of the layout JSON.

The balloons cut out are the ones under the layout's text boxes (so a false
positive the agent deleted stays in the background, and a balloon added by
hand is cut too); cover/editorial layouts ("kind") cut no balloons.

Usage: separate_layers.py <layout.json> [--no-characters] [--force]
  --no-characters   skip the BiRefNet matte everywhere (no ComfyUI needed)
  --force           redo a set that is newer than the art
build_psd.py runs this itself when the set is missing or older than the art.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comfyui
from balloons import body_at, outline_mask
from common import ROOT

sys.path.insert(0, str(ROOT / "manga-translator-ptbr" / "scripts"))
from inpaint_lama import inpaint, method_name  # noqa: E402

BIREFNET_MODEL = "General.safetensors"
LAYER_ORDER = ("background", "characters", "balloons", "border")


def birefnet_alpha(crop: np.ndarray) -> np.ndarray:
    """Foreground matte (float32 0..1, crop size) of the salient subject — the
    characters of a panel — by BiRefNet General in ComfyUI."""
    name = comfyui.upload_image(cv2.imencode(".png", crop)[1].tobytes(), "separate_layers_panel.png")
    n = comfyui.node
    entry = comfyui.run({
        "1": n("LoadImage", image=name),
        "2": n("LoadRembgByBiRefNetModel", model=BIREFNET_MODEL, device="AUTO"),
        "3": n("GetMaskByBiRefNet", model=["2", 0], images=["1", 0], width=1024, height=1024,
               upscale_method="bilinear", mask_threshold=0.0),
        "4": n("MaskToImage", mask=["3", 0]),
        "5": n("PreviewImage", images=["4", 0]),
    })
    mask = comfyui.result_image(entry, cv2.IMREAD_GRAYSCALE)
    if mask.shape != crop.shape[:2]:
        mask = cv2.resize(mask, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR)
    return mask.astype(np.float32) / 255.0


def ensure_birefnet() -> None:
    comfyui.ensure_server()
    if BIREFNET_MODEL not in comfyui.model_files("birefnet"):
        sys.exit(f"ERROR ComfyUI has no models/BiRefNet/{BIREFNET_MODEL} (ComfyUI_BiRefNet_ll) — "
                 "install it with local-ai-setup or pass --no-characters")


def ring_mask(w: int, h: int, border: int) -> np.ndarray:
    ring = np.zeros((h, w), bool)
    if border > 0:
        ring[:] = True
        ring[border:h - border, border:w - border] = False
    return ring


def balloons_in_panel(text_boxes: list[list[int]], img: np.ndarray, box: list[int]) -> np.ndarray:
    """Union of the balloons (body + outline) under the text boxes whose centre
    is inside the panel box (crop-sized bool)."""
    x, y, w, h = box
    inside = np.zeros((h, w), bool)
    for tb in text_boxes:
        tx, ty, tw, th = tb
        if not (x <= tx + tw / 2 < x + w and y <= ty + th / 2 < y + h):
            continue
        body = body_at(img, tb, box)
        if body is not None:
            inside |= outline_mask(img, body)[y:y + h, x:x + w]
    return inside


def with_alpha(bgr: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """BGRA image: the pixels of bgr under a float (0..1) or bool alpha."""
    a = (alpha.astype(np.float32) * 255).clip(0, 255).astype(np.uint8)
    return np.dstack([bgr, a])


def harden(alpha: np.ndarray) -> np.ndarray:
    """More contrast on the matte: half-transparent speed lines and halos
    (0.25..0.75 in BiRefNet's output) drop out, the body stays soft-edged."""
    return np.clip((alpha - 0.25) / 0.5, 0.0, 1.0)


def character_layers(plate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(characters BGRA, background with the characters inpainted away)."""
    alpha = harden(birefnet_alpha(plate))
    k = max(3, round(plate.shape[1] * 0.01)) | 1
    hole = cv2.dilate((alpha > 0.5).astype(np.uint8), np.ones((k, k), np.uint8)) * 255
    return with_alpha(plate, alpha), inpaint(plate, hole)


def panel_layers(img: np.ndarray, panel: dict, text_boxes: list[list[int]], characters: bool) -> dict[str, np.ndarray]:
    """Every layer of one panel (BGR for background, BGRA for the cut-outs)."""
    x, y, w, h = panel["box"]
    crop = img[y:y + h, x:x + w]
    ring = ring_mask(w, h, panel["border"])
    balloons = balloons_in_panel(text_boxes, img, panel["box"])
    plate = inpaint(crop, ((ring | balloons) * 255).astype(np.uint8)) if (ring | balloons).any() else crop.copy()
    out: dict[str, np.ndarray] = {}
    if characters:
        out["characters"], plate = character_layers(plate)
    out["background"] = plate
    if balloons.any():
        out["balloons"] = with_alpha(crop, balloons)
    if ring.any():
        out["border"] = with_alpha(crop, ring)
    return out


def contact_sheet(rows: list[list[np.ndarray]], dst: Path) -> None:
    """One row per panel: background | characters | balloons | border, cut-outs over magenta."""
    tiles = []
    for row in rows:
        cells = []
        for layer in row:
            cell = layer[..., :3] if layer.shape[2] == 3 else _over_magenta(layer)
            scale = 300 / cell.shape[0]
            cells.append(cv2.resize(cell, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
        tiles.append(cv2.hconcat(cells))
    width = max(t.shape[1] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, 4, 0, width - t.shape[1], cv2.BORDER_CONSTANT, value=(80, 80, 80)) for t in tiles]
    cv2.imwrite(str(dst), cv2.vconcat(tiles), [cv2.IMWRITE_JPEG_QUALITY, 85])


def _over_magenta(bgra: np.ndarray) -> np.ndarray:
    a = bgra[..., 3:4].astype(np.float32) / 255
    magenta = np.full_like(bgra[..., :3], (255, 0, 255))
    return (bgra[..., :3] * a + magenta * (1 - a)).astype(np.uint8)


def separate(art: Path, panels: list[dict], text_boxes: list[list[int]], out_dir: Path, characters: bool) -> dict:
    """Write every panel's layer PNGs + layers.json; returns the manifest.
    text_boxes: the layout's balloon text boxes (px) — the balloons to cut."""
    img = cv2.imread(str(art))
    if img is None:
        sys.exit(f"Cannot read {art}")
    if characters and any(p.get("characters", True) for p in panels):
        ensure_birefnet()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"art": str(art), "size": [img.shape[1], img.shape[0]], "inpaint": method_name(), "panels": []}
    rows = []
    for p in panels:
        want = characters and p.get("characters", True)
        layers = panel_layers(img, p, text_boxes, want)
        files = {name: f"panel_{p['id']:02d}_{name}.png" for name in LAYER_ORDER if name in layers}
        for name, fn in files.items():
            cv2.imwrite(str(out_dir / fn), layers[name])
        manifest["panels"].append({"id": p["id"], "box": p["box"], "border": p["border"], "files": files})
        rows.append([layers[n] for n in LAYER_ORDER if n in layers])
        print(f"  panel {p['id']:02d}: {', '.join(files)}")
    contact_sheet(rows, out_dir / "contact.jpg")
    (out_dir / "layers.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def balloon_boxes(layout: dict) -> list[list[int]]:
    """The text boxes that sit in balloons: every text layer of a story page
    (boxes already in px — build_psd.py resolves fraction layouts first)."""
    if layout.get("kind", "story") != "story":
        return []
    return [list(l["box"]) for l in layout.get("layers", []) if "text" in l]


def layers_dir_for(layout_path: Path, layout: dict, mkdir: bool = False) -> Path:
    """work/<issue>/layers/page_NN/ next to the layout file's work/<issue>/layout/."""
    out = layout_path.parent.parent / "layers" / Path(layout["out"]).stem
    if mkdir:
        out.mkdir(parents=True, exist_ok=True)
    return out


def up_to_date(out_dir: Path, art: Path) -> bool:
    manifest = out_dir / "layers.json"
    return manifest.exists() and manifest.stat().st_mtime >= art.stat().st_mtime


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("layout")
    ap.add_argument("--no-characters", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    path = Path(a.layout).expanduser().resolve()
    layout = json.loads(path.read_text(encoding="utf-8"))
    if not layout.get("art"):
        sys.exit("this layout has a flat background, nothing to separate")
    art = (path.parent / layout["art"]).resolve()
    out_dir = layers_dir_for(path, layout)
    if up_to_date(out_dir, art) and not a.force:
        print(f"layers up to date: {out_dir} (--force to redo)")
        return
    panels = layout.get("panels") or [{"id": 1, "box": [0, 0, *cv2.imread(str(art)).shape[1::-1]], "border": 0}]
    separate(art, panels, balloon_boxes(layout), out_dir, not a.no_characters)
    print(f"contact sheet: {out_dir / 'contact.jpg'}")


if __name__ == "__main__":
    main()
