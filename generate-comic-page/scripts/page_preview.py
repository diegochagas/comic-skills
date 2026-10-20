#!/usr/bin/env python3
"""Flattened render of a build_psd.py job (Pillow): the paper, every panel's
layers in order, the image layers and the text boxes drawn with their real
fonts. It is the preview the agent and Diego look at AND the composite
image stored in the PSD (so file browsers show the page, not black).
Photoshop re-renders the text itself on open; this is a close approximation
(same wrap and leading rules as psd_text_fit.py).

    render(job).save("preview.png")
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from psd_text_fit import FontInfo, leading_px, wrap_lines

PX_ALIGN = {"left": "left", "center": "center", "right": "right", "fill": "left"}


def _paste(page: Image.Image, file: str, x: int, y: int) -> None:
    with Image.open(file) as im:
        page.alpha_composite(im.convert("RGBA"), (x, y))


def _draw_text(draw: ImageDraw.ImageDraw, item: dict) -> None:
    x, y, w, _h = item["box"]
    size, spacing = int(item["size"]), float(item.get("line_spacing") or 0)
    font_info: FontInfo = item["font_info"]
    face = ImageFont.truetype(font_info.file, size)
    lines, _longest = wrap_lines(item["text"], font_info, size, w)
    outline = item.get("outline") or {}
    stroke = int(outline.get("width") or 0)
    align = PX_ALIGN.get(item.get("align") or "center", "center")
    line_y = y
    for line in lines:
        width = face.getlength(line)
        line_x = {"left": x, "center": x + (w - width) / 2, "right": x + w - width}[align]
        draw.text((line_x, line_y), line, font=face, fill=item.get("color") or "#000000",
                  stroke_width=stroke, stroke_fill=outline.get("color") or "#000000")
        line_y += leading_px(size, spacing)


def render(job: dict) -> Image.Image:
    """RGBA page: paper, panels (bottom layer first), images, texts."""
    W, H = job["size"]
    page = Image.new("RGBA", (W, H), job["paper"])
    for panel in job["panels"]:
        x, y, _w, _h = panel["box"]
        for layer in panel["layers"]:
            _paste(page, layer["file"], x, y)
    for image in job["images"]:
        _paste(page, image["file"], image["box"][0], image["box"][1])
    draw = ImageDraw.Draw(page)
    for item in job["texts"]:
        _draw_text(draw, item)
    return page
