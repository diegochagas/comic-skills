---
name: image-utils
description: Small batch image transforms on a folder - rotate every image by N degrees, or stretch every PNG to exact WxH pixels (aspect not preserved); the results go to a new folder in ~/Downloads and the originals are never touched. Use when the user asks to "rotate these pages", "turn the scans 90 degrees / upside down", "resize all PNGs to exactly 1920x1080", "stretch to fit", or similar whole-folder image edits. Chooses the script and its positional arguments from the request.
---

# image-utils — rotate / stretch a folder of images

Two scripts in `image-utils/scripts/`. Run them with the repo venv
(`<repo>/venv/bin/python`, created by `<repo>/setup.sh`) or any Python with
Pillow. `<repo>` is the image-skills checkout.

**Where results go:** `~/Downloads` (`COMIC_OUTPUT_DIR` replaces that root;
`--output <dir>` names an exact destination when the user asks for one).
The source folder is never written to.

| Script | What it does | Results |
| --- | --- | --- |
| `rotate_images.py <folder> [degrees] [--output DIR]` | rotates every image (`jpg jpeg png bmp gif tiff webp`) in the folder, clockwise, default 90 | `~/Downloads/<folder name> rotated <degrees>/` (same names) |
| `stretch_pngs.py <folder> <width> <height> [--output DIR]` | resizes every `.png` to exactly `width x height` (LANCZOS, aspect ratio NOT kept) | `~/Downloads/<folder name> <W>x<H>/` (same names, existing files overwritten) |

Neither script recurses into sub-folders.

## Choosing the arguments

| The user says... | Command |
| --- | --- |
| "rotate", "turn 90°", "the scans are sideways" | `rotate_images.py <folder>` (90 clockwise) |
| "counter-clockwise", "the other way", "-90", "270" | `rotate_images.py <folder> 270` |
| "upside down", "180" | `rotate_images.py <folder> 180` |
| "stretch / resize all PNGs to WxH", "exactly 1920 by 1080", "fit the screen" | `stretch_pngs.py <folder> <W> <H>` |
| wants proportional resizing, JPEGs, or a max height rather than exact size | not this skill — `comic-archive` (`images_to_cbr.py --max-height`) or Pillow directly |

The originals are never modified, so a wrong angle costs nothing: run it
again with the right one. If the user explicitly wants the originals
replaced, run the script and then tell them where the rotated copies are —
moving them over the originals is their call.

## Examples

```bash
<repo>/venv/bin/python image-utils/scripts/rotate_images.py "/path/to/images"        # 90° clockwise
<repo>/venv/bin/python image-utils/scripts/rotate_images.py "/path/to/images" 180
<repo>/venv/bin/python image-utils/scripts/stretch_pngs.py "/path/to/images" 1920 1080
```

## Report

Number of files written, the angle or target size, and the full path of the
results folder in `~/Downloads`.
