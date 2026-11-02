#!/usr/bin/env python3
"""Flatten a folder of PSD/PSB pages and pack them into .cbr comic archives.

Glue over two sibling skills, so their behaviour stays in one place:
  pdf-psd-convert/scripts/psd_to_jpg.py   PSD composite -> JPG
  comic-archive/scripts/images_to_cbr.py  folder of images -> .cbr

Where the archives go (never next to the PSDs; $COMIC_OUTPUT_DIR replaces
~/Downloads, --output <dir> names the exact folder):
  folder with PSDs directly in it   -> ~/Downloads/<folder>.cbr
  folder with one sub-folder per chapter -> ~/Downloads/<folder>/<chapter>.cbr

Example:
  venv/bin/python psd-to-cbr/scripts/psd_to_cbr.py "/path/to/Cap.11" --max-height 2500
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS_ROOT / "pdf-psd-convert" / "scripts"))
sys.path.insert(0, str(SKILLS_ROOT / "comic-archive" / "scripts"))

import images_to_cbr  # noqa: E402
import psd_to_jpg  # noqa: E402


def flatten_pages(source: Path, jpg_root: Path, background: str, show_all_layers: bool) -> int:
    """Write one JPG per PSD under jpg_root, keeping the folder structure. Returns the failure count."""
    psd_files = psd_to_jpg.iter_psd_files(source)
    failed = 0
    for index, psd_path in enumerate(psd_files, start=1):
        ok, message = psd_to_jpg.convert_one(
            psd_path, source, jpg_root,
            background=background, overwrite=True, show_all_layers=show_all_layers,
        )
        print(f"[{index}/{len(psd_files)}] {message}")
        failed += 0 if ok else 1
    return failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flatten PSD pages and pack them into .cbr files.")
    parser.add_argument("folder", type=Path, help="Folder of PSDs, or of chapter folders holding PSDs")
    parser.add_argument("--output", type=Path, default=None, help="Folder for the .cbr file(s) (default: ~/Downloads)")
    parser.add_argument("--max-height", type=int, default=None, help="Shrink pages taller than this many pixels")
    parser.add_argument("--quality", type=int, default=90, help="JPEG quality when resizing (default 90)")
    parser.add_argument("--background", default="white", help="Matte colour for transparent areas")
    parser.add_argument("--show-all-layers", action="store_true", help="Recomposite with every layer visible")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing .cbr")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.folder.expanduser().resolve()
    if not source.is_dir():
        print(f"Error: '{source}' is not a folder.", file=sys.stderr)
        return 2
    if not 1 <= args.quality <= 100:
        print(f"Error: --quality must be 1-100, got {args.quality}.", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="psd-to-cbr-"))
    try:
        jpg_root = work / source.name  # the archive is named after this folder
        failed = flatten_pages(source, jpg_root, args.background, args.show_all_layers)
        if failed:
            print(f"Error: {failed} PSD(s) could not be flattened; nothing was packed.", file=sys.stderr)
            return 1
        if not jpg_root.is_dir():
            print(f"No PSD/PSB files found under: {source}")
            return 0
        options = images_to_cbr.ImageOptions(
            convert_jpeg=False, max_height=args.max_height,
            quality=args.quality, overwrite=args.overwrite,
        )
        output = args.output.expanduser() if args.output else None
        images_to_cbr.process(jpg_root, options, output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
