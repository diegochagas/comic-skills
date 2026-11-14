---
name: pdf-psd-convert
description: Turn PDFs into per-page JPG images (any DPI, one folder per PDF or one shared folder) and flatten Photoshop .psd/.psb files into JPGs (recursive, matte color, optional force-all-layers-visible render). Use when the user asks to "extract the pages of this pdf", "convert pdf to images", "rasterize", "export these PSDs as jpg", "flatten the psd files", or wants images out of PDF/PSD sources — e.g. before packing them with comic-archive. Chooses DPI, output folder, overwrite and layer-visibility flags from the request.
---

# pdf-psd-convert — PDF pages and PSD files → JPG

Two scripts in `pdf-psd-convert/scripts/`. Run them with the repo venv
(`<repo>/venv/bin/python`, created by `<repo>/setup.sh`; it has PyMuPDF,
Pillow and psd-tools). `<repo>` is the image-skills checkout.

**Where results go:** `~/Downloads` (`COMIC_OUTPUT_DIR` replaces that root;
`--output <dir>` names an exact destination when the user asks for one).
The source folder is never written to.

| Script | What it does | Needs |
| --- | --- | --- |
| `pdf_to_images.py` | every PDF in a folder → JPG per page, then checks the page count matches | PyMuPDF (`pymupdf`) |
| `psd_to_jpg.py` | every `.psd`/`.psb` under a folder (recursive) → JPG, same pixel size, folder structure preserved | Pillow; `psd-tools[composite]` only for `--show-all-layers` |

## Choosing the flags

**PDF → images** (`pdf_to_images.py <folder> [flags]`; prompts for the
folder if omitted, so always pass it):

| The user says... | Flags |
| --- | --- |
| "extract the pages" | none — `~/Downloads/<folder name>/<PdfName>/0001.jpg ...`, one folder per PDF, 150 DPI |
| "high resolution", "for print", "sharp", "big scans" | `--dpi 300` (or the number they give) |
| "quick preview", "small", "thumbnails" | `--dpi 72` |
| "everything in one folder", "single folder", "merge all PDFs" | `--single-folder` → `~/Downloads/<folder name>/<PdfName>_0001.jpg` |
| names a destination | `--output <dir>` (both modes; with `--single-folder --overwrite` that folder is emptied first) |
| "redo", "replace", "overwrite" | `--overwrite` (otherwise existing output folders are skipped) |

Duplicate PDFs whose names differ only by extra `.pdf` suffixes are
de-duplicated to the shortest name.

**PSD → JPG** (`psd_to_jpg.py <source> [flags]`):

| The user says... | Flags |
| --- | --- |
| "convert / flatten these PSDs" | none — output to `~/Downloads/<source folder name> JPG/`, saved composite, white matte |
| names an output folder | `--output <dir>` |
| "black background", a matte color | `--background <color>` (any Pillow color name / hex) |
| "hidden layers too", "everything visible", "show all layers", "the composite is stale" | `--show-all-layers` (recomposites with every layer/group on; slower) |
| "redo", "overwrite" | `--overwrite` (otherwise a JPG newer than its PSD is skipped) |
| "test on a few first" | `--limit <N>` |

Uses the flattened composite Photoshop baked into the file (fast, exact) unless
`--show-all-layers` is given; layers are never exported individually. CMYK
files keep their ICC profile; RGB profiles are converted to sRGB.

## Examples

```bash
<repo>/venv/bin/python pdf-psd-convert/scripts/pdf_to_images.py "/path/to/pdfs" --dpi 300                # -> ~/Downloads/pdfs/<PdfName>/
<repo>/venv/bin/python pdf-psd-convert/scripts/pdf_to_images.py "/path/to/pdfs" --single-folder --output "/path/to/all-images"
<repo>/venv/bin/python pdf-psd-convert/scripts/psd_to_jpg.py "/path/to/psd-files" --show-all-layers
<repo>/venv/bin/python pdf-psd-convert/scripts/psd_to_jpg.py "/path/to/psd-files" --background black --limit 5
```

To pack the resulting image folders into comic archives, hand off to the
`comic-archive` skill (`images_to_cbr.py` on the folder that contains them).

## Report

List the output folder(s), page/file counts, DPI used, and any PDF whose
rendered page count did not match (the script prints a warning per file).
