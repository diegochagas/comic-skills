#!/usr/bin/env python3
"""Fonts and text sizes for the PSD text boxes of build_psd.py.

Fonts are named the GIMP way in layouts ("CCWildWords Regular", "Impact
Regular") and resolved through fontconfig to the file (Pillow measures with
it) and the PostScript name Photoshop stores ("CCWildWords-Regular"). A
missing font is measured with DejaVu Sans Bold and written under a guessed
PostScript name, and the caller reports it.

Measuring follows manga-translator-ptbr's text_metrics.mjs, calibrated on
boxes Diego sized by hand in Photoshop: greedy word wrap at 96% of the box
width, line height = 1.2 × size (auto leading) plus the layout's
line_spacing, box height = ascent + (lines − 1) × leading + 1 px.

    font = resolve_font("CCWildWords Regular")      # FontInfo(file, ps_name, found)
    plan_sizes(items, uniform=True, max_size=33)    # fills item["size"] where it is None
    hug_box(item, font)                             # [x, y, w, h] the Type layer gets
"""
from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from functools import lru_cache

from PIL import ImageFont

MIN_FONT = 8
SAFETY_W = 0.96
LEADING = 1.2
FALLBACK_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@dataclass(frozen=True)
class FontInfo:
    file: str
    ps_name: str
    found: bool


@lru_cache(maxsize=1)
def installed_fonts() -> list[tuple[list[str], list[str], str, str]]:
    """(families, styles, postscript name, file) for every face fontconfig
    knows; families and styles lower-cased, first entry of each in its own case too (see list_names)."""
    out = subprocess.run(["fc-list", "-f", "%{family}\t%{style}\t%{postscriptname}\t%{file}\n"],
                         capture_output=True, text=True, check=False).stdout
    faces = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[2]:
            faces.append(([f.strip().lower() for f in parts[0].split(",")],
                          [s.strip().lower() for s in parts[1].split(",")], parts[2], parts[3]))
    return faces


@lru_cache(maxsize=1)
def list_names() -> list[tuple[str, str]]:
    """("Family Style" as GIMP shows it, PostScript name) for every installed face, sorted."""
    out = subprocess.run(["fc-list", "-f", "%{family[0]} %{style[0]}\t%{postscriptname}\n"],
                         capture_output=True, text=True, check=False).stdout
    pairs = {tuple(line.split("\t")) for line in out.splitlines() if "\t" in line}
    return sorted(p for p in pairs if len(p) == 2 and p[1])


STYLE_WORDS = {"regular", "bold", "italic", "oblique", "black", "heavy", "light", "medium", "thin",
               "semibold", "extrabold", "condensed", "book"}


def _faces_named(family: str, style: str) -> list[tuple[list[str], list[str], str, str]]:
    """Installed faces matching family + style, the ones whose first family
    name is exactly `family` first ("Impact" before "Impacted,Impact")."""
    hits = [f for f in installed_fonts() if family in f[0] and style in f[1]]
    return sorted(hits, key=lambda f: f[0][0] != family)


def split_gimp_name(name: str) -> tuple[str, str]:
    """"CCWildWords Bold Italic" -> ("ccwildwords", "bold italic"); a bare family means Regular.
    An uninstalled name is split before its trailing style words ("Foo Bold" -> foo, bold)."""
    words = name.strip().lower().split()
    for n in range(len(words) - 1, 0, -1):
        if _faces_named(" ".join(words[:n]), " ".join(words[n:])):
            return " ".join(words[:n]), " ".join(words[n:])
    n = len(words)
    while n > 1 and words[n - 1] in STYLE_WORDS:
        n -= 1
    return " ".join(words[:n]), " ".join(words[n:]) or "regular"


def resolve_font(gimp_name: str) -> FontInfo:
    family, style = split_gimp_name(gimp_name)
    for _fams, _sts, ps, file in _faces_named(family, style):
        return FontInfo(file, ps.split(",")[0], True)
    guess = "".join(w.capitalize() for w in family.split()) + ("" if style == "regular" else "-" + "".join(w.capitalize() for w in style.split()))
    return FontInfo(FALLBACK_FILE, guess, False)


@lru_cache(maxsize=256)
def _face(file: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(file, size)


def wrap_lines(text: str, font: FontInfo, size: int, width: float) -> tuple[list[str], float]:
    """Greedy word wrap; returns (lines, widest word) in px."""
    face = _face(font.file, size)
    usable = width * (SAFETY_W if font.found else SAFETY_W - 0.06)
    lines, longest = [], 0.0
    for para in text.split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        cur = ""
        for w in words:
            longest = max(longest, face.getlength(w))
            cand = f"{cur} {w}" if cur else w
            if face.getlength(cand) <= usable or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines, longest


def leading_px(size: float, line_spacing: float) -> float:
    return LEADING * size + line_spacing


def needed_height(lines: int, font: FontInfo, size: int, line_spacing: float) -> float:
    ascent = _face(font.file, size).getmetrics()[0]
    return ascent + (lines - 1) * leading_px(size, line_spacing) + 1


def fits(text: str, font: FontInfo, size: int, box_w: float, box_h: float, line_spacing: float) -> bool:
    lines, longest = wrap_lines(text, font, size, box_w)
    return longest <= box_w * SAFETY_W and needed_height(len(lines), font, size, line_spacing) <= box_h


def fit_size(text: str, font: FontInfo, box_w: float, box_h: float, line_spacing: float) -> int:
    """Largest size (px) at which the text wraps into the box."""
    lo, hi, best = MIN_FONT, max(MIN_FONT, int(box_h)), MIN_FONT
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(text, font, mid, box_w, box_h, line_spacing):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def plan_sizes(items: list[dict], uniform: bool, max_size: float | None) -> None:
    """Give every text item without a "size" the largest size that fits its
    box (capped by max_size); with `uniform` they all drop to one common size
    (25th percentile of the fits) so the lettering of a page looks even.
    Items carry "font_info" (FontInfo) and "line_spacing"."""
    auto = [i for i in items if not i.get("size")]
    for item in auto:
        item["size"] = fit_size(item["text"], item["font_info"], item["box"][2], item["box"][3],
                                float(item.get("line_spacing") or 0))
        if max_size:
            item["size"] = min(item["size"], int(max_size))
    if uniform and auto:
        common = sorted(i["size"] for i in auto)[len(auto) // 4]
        for item in auto:
            item["size"] = min(item["size"], common)


def hug_box(item: dict) -> tuple[list[int], bool]:
    """The Type layer's box: as wide as the layout box, as tall as its text
    (Photoshop starts the text at the top of a paragraph box, so "middle" /
    "bottom" valign is the box moved down). Returns (box, overflows)."""
    x, y, w, h = item["box"]
    size, spacing = int(item["size"]), float(item.get("line_spacing") or 0)
    lines, _longest = wrap_lines(item["text"], item["font_info"], size, w)
    need = math.ceil(needed_height(len(lines), item["font_info"], size, spacing))
    valign = item.get("valign", "middle")
    box_h = h if valign == "top" else min(h, need + 2)
    off = {"top": 0, "middle": (h - box_h) // 2, "bottom": h - box_h}[valign]
    return [x, y + off, w, max(1, box_h)], need > h
