---
name: psd-to-cbr
description: Turn a folder of Photoshop PSD/PSB pages (or a folder of chapter folders of PSDs) straight into .cbr comic archives saved in ~/Downloads - flattens every PSD to JPG, then packs the pages in filename order. Use when Diego asks for "psd to cbr", "make a cbr from these PSDs", "pack the PSDs as a comic file", "converte os PSDs pra cbr", or has lettered/translated PSD pages ready to read on a comic reader.
---

# psd-to-cbr — PSD pages → .cbr

One script, `psd-to-cbr/scripts/psd_to_cbr.py`, that chains
`pdf-psd-convert/psd_to_jpg.py` and `comic-archive/images_to_cbr.py`
(both folders must stay side by side). Run it with the repo venv
(`<repo>/venv/bin/python`).

**Where results go:** `~/Downloads` (`COMIC_OUTPUT_DIR` replaces that root;
`--output <dir>` names an exact folder). The PSD folder is never written to;
the intermediate JPGs live in a temp folder that is deleted at the end.

| Source folder holds... | Result |
| --- | --- |
| PSDs directly | `~/Downloads/<folder name>.cbr` |
| one sub-folder of PSDs per chapter | `~/Downloads/<folder name>/<chapter>.cbr` each |

Pages are packed in sorted filename order: if names don't sort in reading
order (`1.psd, 10.psd, 2.psd`), tell Diego or zero-pad copies first.

## Flags

| The user says... | Flags |
| --- | --- |
| "psd to cbr" | none — full-size JPG pages, quality 100 from the PSD composite |
| "smaller", "for tablet/phone", "max height N" | `--max-height <px>` (2048 tablets, 2500–3000 keeps detail), `--quality <1-100>` (default 90) |
| names a destination | `--output <dir>` |
| "redo / replace the cbr" | `--overwrite` (an existing `.cbr` is otherwise skipped) |
| "hidden layers too", "composite looks stale" | `--show-all-layers` (slower) |
| a matte colour for transparency | `--background <color>` |
| wants `.cbz` | rename the result: it is a plain ZIP either way |

If any PSD cannot be flattened the script packs nothing and says which one.

## Example

```bash
<repo>/venv/bin/python psd-to-cbr/scripts/psd_to_cbr.py "/path/to/Golden Age Cap.11.5" --max-height 2500
```

## Report

Full path of each `.cbr` written, page count, and anything skipped or failed.
