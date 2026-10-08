#!/usr/bin/env python3
"""Before/after contact sheets for QC: each row is <original> | <restored>,
matched by file name (any extension), 6 pairs per sheet.

    contact_sheet.py <originals dir> <restored dir> [--out DIR] [--per-sheet 6] [--width 560]

Writes <out>/sheet_01.jpg ... (default: <restored dir>/../sheets).
"""
import argparse
import glob
import os

from PIL import Image, ImageDraw

EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")


def find(d):
    return {os.path.splitext(os.path.basename(p))[0]: p for p in sorted(glob.glob(os.path.join(d, "*")))
            if p.lower().endswith(EXT)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("originals")
    ap.add_argument("restored")
    ap.add_argument("--out")
    ap.add_argument("--per-sheet", type=int, default=6)
    ap.add_argument("--width", type=int, default=560, help="max width/height of each thumbnail")
    a = ap.parse_args()
    orig, rest = find(a.originals), find(a.restored)
    names = [n for n in orig if n in rest]
    if not names:
        raise SystemExit("no matching file names between the two folders")
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.restored)), "sheets")
    os.makedirs(out, exist_ok=True)
    for s in range(0, len(names), a.per_sheet):
        chunk = names[s:s + a.per_sheet]
        rows = []
        for n in chunk:
            im1, im2 = Image.open(orig[n]).convert("RGB"), Image.open(rest[n]).convert("RGB")
            im1.thumbnail((a.width, a.width))
            im2.thumbnail((a.width, a.width))
            rows.append((n, im1, im2))
        W = 2 * a.width + 30
        H = sum(max(r[1].size[1], r[2].size[1]) + 34 for r in rows) + 10
        sheet = Image.new("RGB", (W, H), (60, 60, 60))
        d = ImageDraw.Draw(sheet)
        y = 10
        for n, im1, im2 in rows:
            d.text((10, y), n[:90], fill=(255, 255, 255))
            y += 20
            sheet.paste(im1, (10, y))
            sheet.paste(im2, (20 + a.width, y))
            y += max(im1.size[1], im2.size[1]) + 14
        p = os.path.join(out, f"sheet_{s // a.per_sheet + 1:02d}.jpg")
        sheet.save(p, quality=82)
        print(f"-> {p}  ({', '.join(chunk)})")


if __name__ == "__main__":
    main()
