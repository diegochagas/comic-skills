---
name: comic-archive
description: Pack a folder of page images into a .cbr/.cbz comic archive, or unpack .cbr/.cbz/.zip archives back into image folders (optionally just the cover). Use when the user asks to "make a cbr/cbz", "pack these pages", "turn this folder into a comic file", "extract/unpack this cbr", "get the covers out of these archives", or mentions comic-reader files. Picks the right script and flags (JPEG conversion, max height, quality, overwrite, dry-run, first-only) from what the user says.
---

# comic-archive — pack / unpack comic archives

Two scripts in `comic-archive/scripts/`. Run them with the repo venv
(`<repo>/venv/bin/python`, created by `<repo>/setup.sh`) or any Python 3.10+
that has Pillow when conversion flags are used. `<repo>` is the image-skills
checkout.

**Where results go:** `~/Downloads` (`COMIC_OUTPUT_DIR` replaces that root;
`--output <dir>` names an exact destination when the user asks for one).
The source folder is never written to.

| Script | Direction | Needs |
| --- | --- | --- |
| `images_to_cbr.py` | folder(s) of images → `.cbr` (a ZIP renamed for comic readers) | stdlib; Pillow only for `--convert-jpeg` / `--max-height` |
| `cbr_to_images.py` | `.cbr` / `.cbz` / `.zip` → image folders | stdlib for ZIP-based files; `unrar`, `rar` or `7z` on PATH for RAR-based `.cbr` |

## Choosing the script and flags

Decide from the request, then run once. Ask only when the target path is
missing.

**Packing** (`images_to_cbr.py <folder> [flags]`):

| The user says... | Flags |
| --- | --- |
| "pack / make a cbr / cbz of this folder" | none — images are stored as-is |
| a folder that contains one sub-folder per chapter/issue | none — each sub-folder becomes its own `.cbr` in `~/Downloads/<folder name>/` |
| a folder that contains the images directly | none — one `~/Downloads/<folder name>.cbr` |
| names a destination folder | `--output <dir>` |
| "convert to jpg", "smaller file", "the PNGs are huge" | `--convert-jpeg` |
| "resize", "max height N", "shrink tall pages", "for a tablet / phone" | `--max-height <px>` (outputs JPEG; 2048 for tablets, 2500–3000 to keep detail) |
| a JPEG quality, "high quality", "compress more" | `--quality <1-100>` (default 90) |
| "replace / redo / overwrite the existing cbr" | `--overwrite` (otherwise existing `.cbr` files are skipped) |
| wants a `.cbz` extension | pack, then rename the result: the file is a plain ZIP either way |

Supported inputs: `.jpg .jpeg .png .gif .webp .bmp .tiff .tif`. Pages are
added in sorted filename order — if the names don't sort in reading order
(`1.jpg, 10.jpg, 2.jpg`), rename them zero-padded first.

**Unpacking** (`cbr_to_images.py <folder> [flags]`):

| The user says... | Flags |
| --- | --- |
| "extract / unpack these archives" | none — each archive becomes `~/Downloads/<target folder name>/<archive name>/` (`Comic (1)` if it already exists) |
| "just show me what would happen", "preview" | `--dry-run` |
| "get the covers", "first page of each", "thumbnails" | `--first-only` (writes only `~/Downloads/<target folder name>/<archive stem>.<ext>` per archive) |
| names a destination folder | `--output <dir>` |
| both of the above | `--dry-run --first-only` |

Only the given folder itself is scanned (no recursion). A failed extraction
removes its partial output folder. If a RAR-based `.cbr` fails with "Could not
find an extractor", tell the user to install `unrar` or `p7zip-full`.

## Examples

```bash
<repo>/venv/bin/python comic-archive/scripts/images_to_cbr.py "/path/to/comics"                       # ~/Downloads/comics/<sub-folder>.cbr
<repo>/venv/bin/python comic-archive/scripts/images_to_cbr.py "/path/to/chapter1" --convert-jpeg --max-height 2500
<repo>/venv/bin/python comic-archive/scripts/cbr_to_images.py "/path/to/comics" --dry-run
<repo>/venv/bin/python comic-archive/scripts/cbr_to_images.py "/path/to/comics" --first-only         # covers only
```

## Report

Say which archives/folders were written (full paths), how many pages each
holds, and anything skipped (existing files without `--overwrite`, archives
that needed an extractor).
