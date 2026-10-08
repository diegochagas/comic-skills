#!/usr/bin/env python3
"""Re-save JPEG files with a broken data stream or a truncated tail so that
every viewer opens them cleanly, keeping the EXIF (exiftool) and the pixels
that are there. A truncated file's missing rows come out as a flat grey
strip; --crop-strip cuts those rows off instead of keeping them.

    fix_broken.py <image>... [--output DIR] [--crop-strip] [--quality 95]

Results: <output>/<name>.jpg (default ~/Downloads/photo-restore/restored,
$PHOTO_RESTORE_OUT replaces the root). Sources are never modified.
"""
import argparse
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True


def crop_strip(im):
    """Drop trailing rows that are one flat grey value (what a decoder fills
    in for the missing bytes)."""
    a = np.asarray(im.convert("RGB"))
    h = a.shape[0]
    y = h
    while y > 0:
        row = a[y - 1]
        if row.std() < 1.0 and abs(int(row.mean()) - 128) < 3:
            y -= 1
        else:
            break
    return im.crop((0, 0, a.shape[1], y)) if y < h else im


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--output")
    ap.add_argument("--crop-strip", action="store_true")
    ap.add_argument("--quality", type=int, default=95)
    a = ap.parse_args()
    root = os.path.expanduser(os.environ.get("PHOTO_RESTORE_OUT") or "~/Downloads/photo-restore")
    out_dir = os.path.abspath(os.path.expanduser(a.output or os.path.join(root, "restored")))
    os.makedirs(out_dir, exist_ok=True)
    have_exiftool = shutil.which("exiftool") is not None
    for src in a.images:
        try:
            im = Image.open(src)
            im.load()
        except OSError as e:
            print(f"cannot decode {src}: {e}")
            continue
        h0 = im.size[1]
        if a.crop_strip:
            im = crop_strip(im)
        dst = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + ".jpg")
        im.convert("RGB").save(dst, quality=a.quality, subsampling=0)
        if have_exiftool:
            subprocess.run(["exiftool", "-q", "-m", "-overwrite_original", "-TagsFromFile", src, "-all:all",
                            "-ImageHeight=", "-ImageWidth=", dst], check=False)
        note = f", {h0 - im.size[1]} grey rows cropped" if im.size[1] != h0 else ""
        print(f"-> {dst}{note}")
    if not have_exiftool:
        print("exiftool not found: EXIF (date, camera) was NOT copied", file=sys.stderr)


if __name__ == "__main__":
    main()
