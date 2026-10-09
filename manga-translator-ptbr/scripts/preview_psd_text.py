#!/usr/bin/env python3
"""Approximate visual preview of a translated PSD: draws every text layer's
box and its text (wrapped, at the layer's font size/color/rotation) over the
source image, downscaled. For QC only — Photoshop renders the real thing.

Usage: python preview_psd_text.py <psd> <source_image> <out.jpg> [--max 2000]
Requires node + list_text_layers.mjs (same folder as this script).
"""
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_font_files = {}


def font_file(ps_name):
    """The installed file for a Photoshop font name (fontconfig), else DejaVu."""
    if ps_name not in _font_files:
        path = FONT
        family = (ps_name or "").split("-")[0]
        for pattern, want in ((f":postscriptname={ps_name}", lambda g, f: g.lower() == (ps_name or "").lower()),
                              (f":family={family}:style=Regular", lambda g, f: f.replace(" ", "").lower() == family.lower())):
            try:
                out = subprocess.check_output(["fc-match", "-f", "%{file}|%{postscriptname}|%{family}", pattern], text=True).strip()
                file, got, fam = (out.split("|") + ["", ""])[:3]
                if want(got, fam):
                    path = file
                    break
            except Exception:
                pass
        _font_files[ps_name] = path
    return _font_files[ps_name]


def wrap(draw, text, font, width):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = (cur + " " + word).strip()
            if draw.textlength(cand, font=font) <= width or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def main():
    psd, src, out = sys.argv[1:4]
    mx = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 2000
    layers = json.loads(subprocess.check_output(["node", os.path.join(HERE, "list_text_layers.mjs"), psd]))
    im = Image.open(src)
    try:
        exif = im.getexif().get(274, 1)
    except Exception:
        exif = 1
    im = im.convert("RGB")
    s = min(1.0, mx / max(im.size))
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    draw = ImageDraw.Draw(im, "RGBA")
    for L in layers:
        x, y, w, h = [v * s for v in (L["left"], L["top"], L["width"], L["height"])]
        draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 0, 200), width=2)
        rot = L.get("rotate") or 0
        bw, bh = (h, w) if abs(rot) == 90 else (w, h)
        size = max(6, int((L.get("fontSize") or 12) * s))
        font = ImageFont.truetype(font_file(L.get("font")), size)
        c = L.get("color") or {"r": 0, "g": 0, "b": 0}
        col = (int(c["r"]), int(c["g"]), int(c["b"]), 255)
        lines = wrap(draw, L["text"], font, bw)
        # render into its own canvas, then rotate & paste
        layer = Image.new("RGBA", (max(1, int(bw)), max(1, int(bh))), (0, 0, 0, 0))
        d2 = ImageDraw.Draw(layer)
        ty = 0
        for ln in lines:
            tw = d2.textlength(ln, font=font)
            al = L.get("align") or "center"
            tx = 0 if al == "left" else (bw - tw if al == "right" else (bw - tw) / 2)
            d2.text((tx, ty), ln, font=font, fill=col)
            ty += size * 1.2      # Photoshop auto leading
        if rot == 90:
            layer = layer.rotate(-90, expand=True)
        elif rot == -90:
            layer = layer.rotate(90, expand=True)
        elif rot == 180:
            layer = layer.rotate(180, expand=True)
        im.paste(layer, (int(x), int(y)), layer)
    im.save(out, quality=85)
    print(f"{out}: {len(layers)} text layers drawn ({exif=})")


if __name__ == "__main__":
    main()
