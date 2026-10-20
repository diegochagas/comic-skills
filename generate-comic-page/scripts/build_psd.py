#!/usr/bin/env python3
"""Build the Photoshop file of a page: every part of the page on its own
layer and every piece of text as a NATIVE Photoshop paragraph text box
Diego can retype, restyle and move. Used for EVERY page: story pages (one
CCWildWords box per empty balloon, layout drafted by make_layout.py), covers
and editorials. Free: no credits, no GIMP; local models only.

Layer stack (bottom → top):
  Art (flat)          the approved art as generated, HIDDEN (reference)
  Paper               the page colour
  Panel 01 (group)    Background / Characters / Balloons / Border — the art
  Panel 02 ...        split by separate_layers.py (run here when missing or
                      older than the art; BiRefNet matte in local ComfyUI,
                      LaMa inpaint — INPAINT=qwen for the local Qwen model)
  <image layers>      logo, fan art (cover/editorial)
  Text (group)        one Type layer per text item

Usage:
  build_psd.py <layout.json> [--no-characters] [--force-layers]
  build_psd.py --list-fonts [filter]     fonts fontconfig knows: "Family Style" -> PostScript name

layout.json (paths relative to the layout file; see SKILL.md for the workflow):
  {
    "art": "../gen/page_01_try2.png",          the approved textless art, OR
    "background": {"color": "#efe6cf", "size": [1365, 2048]},   a flat page
    "out": "page_01.psd", "preview": "page_01_preview.jpg",
    "kind": "story" | "cover" | "editorial",     story = balloons cut out under the text boxes
    "units": "px" | "fraction",                  boxes in pixels or 0..1 of the page (default)
    "panels": [{"id": 1, "box": [x, y, w, h], "border": 5, "characters": true}, ...],
                                                 from make_layout.py; omitted = the whole art is one panel
    "characters": true,                          false = no Characters layers (no ComfyUI needed)
    "uniform_size": false, "max_size": null,     comic lettering: one text size per page, px cap
    "layers": [                                  bottom -> top
      {"name": "Logo", "text": "NOVAS AVENTURAS\\nDE MEGAMAN", "box": [0.05, 0.02, 0.90, 0.17],
       "font": "Impact Regular",                 GIMP-style name (see --list-fonts)
       "size": null,                             px; null = largest size that fits the box
       "color": "#ffd400", "align": "center",    left | center | right
       "valign": "middle",                       top | middle | bottom inside the box
       "line_spacing": -8, "letter_spacing": 0,
       "outline": {"color": "#000000", "width": 6}},
      {"name": "Fan art", "image": "fanart.png", "box": [0.62, 0.70, 0.30, 0.22]}
    ]
  }

Exit status 0 when the saved PSD was read back with every layer in place and
every text layer holding the exact text. Printed notes to act on: font not
installed, "may OVERFLOW". Needs node + generate-comic-page/node_modules
(setup.sh) and the fonts installed in the system.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import separate_layers
from page_preview import render
from psd_text_fit import FontInfo, hug_box, list_names, plan_sizes, resolve_font

WRITER = Path(__file__).resolve().parent / "build_psd.mjs"
DEFAULT_FONT = "CCWildWords Regular"
RESERVED = {"Art (flat)", "Paper", "Text"}


def canvas_size(layout: dict, base: Path) -> tuple[int, int]:
    if layout.get("art"):
        with Image.open(base / layout["art"]) as im:
            return im.size
    return tuple(layout["background"]["size"])


def px_box(box: list[float], cw: int, ch: int, px: bool, name: str) -> list[int]:
    x, y, w, h = box
    if not px:
        if max(x, y, w, h) > 1.5:
            sys.exit(f'layer {name!r}: box {box} looks like pixels — set "units": "px"')
        x, y, w, h = x * cw, y * ch, w * cw, h * ch
    return [round(x), round(y), max(1, round(w)), max(1, round(h))]


def resolve_layers(layout: dict, base: Path, cw: int, ch: int) -> list[dict]:
    px = layout.get("units", "fraction") == "px"
    names: set[str] = set()
    out = []
    for item in layout.get("layers", []):
        if ("text" in item) == ("image" in item):
            sys.exit(f'layer {item.get("name")!r}: needs exactly one of "text" or "image"')
        if not item.get("name") or item["name"] in names | RESERVED:
            sys.exit(f'layer name {item.get("name")!r} is missing, duplicated or reserved')
        names.add(item["name"])
        resolved = {**item, "box": px_box(item["box"], cw, ch, px, item["name"])}
        if "image" in item:
            resolved["image"] = str((base / item["image"]).resolve())
            if not Path(resolved["image"]).exists():
                sys.exit(f'layer {item["name"]!r}: image not found: {resolved["image"]}')
        else:
            resolved.setdefault("font", layout.get("font", DEFAULT_FONT))
        out.append(resolved)
    if not any("text" in l for l in out):
        sys.exit("layout has no text layers")
    return out


def resolve_panels(layout: dict, cw: int, ch: int) -> list[dict]:
    px = layout.get("units", "fraction") == "px"
    panels = layout.get("panels") or [{"id": 1, "box": [0, 0, cw, ch] if px else [0, 0, 1, 1], "border": 0}]
    return [{**p, "box": px_box(p["box"], cw, ch, px, f"panel {p.get('id')}")} for p in panels]


def panel_groups(manifest: dict, layers_dir: Path) -> list[dict]:
    return [{"name": f"Panel {p['id']:02d}", "box": p["box"],
             "layers": [{"name": name.capitalize(), "file": str(layers_dir / fn)} for name, fn in p["files"].items()]}
            for p in manifest["panels"]]


def ensure_layers(layout: dict, art: Path, panels: list[dict], layers_dir: Path, a: argparse.Namespace) -> dict:
    """The per-panel layer PNGs (separate_layers.py), reused when newer than the art."""
    if separate_layers.up_to_date(layers_dir, art) and not a.force_layers:
        return json.loads((layers_dir / "layers.json").read_text(encoding="utf-8"))
    print("separating the art into layers ...")
    want = not a.no_characters and layout.get("characters", True)
    return separate_layers.separate(art, panels, separate_layers.balloon_boxes(layout), layers_dir, want)


def image_layer(item: dict, layers_dir: Path) -> dict:
    """The image scaled to fit its box (kept aspect), written as a PNG for the writer."""
    x, y, w, h = item["box"]
    with Image.open(item["image"]) as im:
        img = im.convert("RGBA")
        scale = min(w / img.width, h / img.height)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    file = layers_dir / f"image_{item['name']}.png"
    img.save(file)
    return {"name": item["name"], "file": str(file), "box": [x + (w - img.width) // 2, y + (h - img.height) // 2, img.width, img.height]}


def text_items(layers: list[dict], layout: dict) -> tuple[list[dict], list[str]]:
    """Text layers with their font, final size and hugging box; plus the notes to print."""
    items = [{**l, "font_info": resolve_font(l["font"])} for l in layers if "text" in l]
    plan_sizes(items, bool(layout.get("uniform_size")), layout.get("max_size"))
    notes = []
    for item in items:
        item["box"], overflow = hug_box(item)
        font: FontInfo = item["font_info"]
        notes.append(f"  text '{item['name']}': {item['size']}px {font.ps_name}"
                     + ("" if font.found else f"  [font '{item['font']}' NOT installed -> DejaVu metrics]")
                     + ("  [may OVERFLOW its box: shrink size or grow the box]" if overflow else ""))
    return items, notes


def writer_text(item: dict) -> dict:
    return {"name": item["name"], "text": item["text"], "box": item["box"], "font": item["font_info"].ps_name,
            "size": int(item["size"]), "color": item.get("color") or "#000000", "align": item.get("align") or "center",
            "line_spacing": float(item.get("line_spacing") or 0), "letter_spacing": float(item.get("letter_spacing") or 0),
            "outline": item.get("outline")}


def build_job(path: Path, a: argparse.Namespace) -> tuple[dict, list[str]]:
    layout = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    if bool(layout.get("art")) == bool(layout.get("background")):
        sys.exit('layout needs exactly one of "art" (image path) or "background" ({"color", "size"})')
    cw, ch = canvas_size(layout, base)
    layers = resolve_layers(layout, base, cw, ch)
    layers_dir = separate_layers.layers_dir_for(path, layout, mkdir=True)
    job = {"out": str((base / layout["out"]).resolve()), "size": [cw, ch], "panels": [], "images": [],
           "paper": layout["background"]["color"] if layout.get("background") else layout.get("paper", "#ffffff"),
           "art": str((base / layout["art"]).resolve()) if layout.get("art") else None,
           "composite": str(layers_dir / "preview.png"),
           "preview": str((base / layout["preview"]).resolve()) if layout.get("preview") else None}
    if job["art"]:
        panels = resolve_panels({**layout, "units": layout.get("units", "fraction")}, cw, ch)
        manifest = ensure_layers({**layout, "layers": layers, "units": "px"}, Path(job["art"]), panels, layers_dir, a)
        job["panels"] = panel_groups(manifest, layers_dir)
    job["images"] = [image_layer(l, layers_dir) for l in layers if "image" in l]
    items, notes = text_items(layers, layout)
    job["texts"] = items
    return job, notes


def write_previews(job: dict) -> None:
    page = render(job)
    page.save(job["composite"])
    if job["preview"]:
        Path(job["preview"]).parent.mkdir(parents=True, exist_ok=True)
        page.convert("RGB").save(job["preview"], quality=90)


def run_writer(job: dict) -> int:
    job_path = Path(job["composite"]).with_name("psd_job.json")
    job_path.write_text(json.dumps({**job, "texts": [writer_text(t) for t in job["texts"]]}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["node", str(WRITER), str(job_path)], capture_output=True, text=True, check=False)
    print(proc.stdout.rstrip())
    if proc.returncode:
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def list_fonts(needle: str) -> None:
    for name, ps in list_names():
        if needle.lower() in name.lower():
            print(f"{name}  ->  {ps}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("layout", nargs="?")
    ap.add_argument("--list-fonts", nargs="?", const="", default=None, metavar="FILTER")
    ap.add_argument("--no-characters", action="store_true", help="no BiRefNet matte (no ComfyUI needed)")
    ap.add_argument("--force-layers", action="store_true", help="redo the layer separation")
    a = ap.parse_args()
    if a.list_fonts is not None:
        list_fonts(a.list_fonts)
        return 0
    if not a.layout:
        ap.error("layout.json or --list-fonts required")
    job, notes = build_job(Path(a.layout).expanduser().resolve(), a)
    write_previews(job)
    code = run_writer(job)
    print("\n".join(notes))
    if code == 0 and job["preview"]:
        print(f"preview: {job['preview']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
