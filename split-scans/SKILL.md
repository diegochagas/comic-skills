---
name: split-scans
description: Cut flatbed scans of an open book (one image or PSD showing two facing pages, e.g. 082-083.jpg / 082-083.psd) into one PSD per page (082.psd + 083.psd), each page flattened (perspective and skew corrected from its four paper edges), cropped to the paper (scanner background and lid removed) and with the spine shadow turned back into white paper (shadow band divided by its illumination, the unrecoverable core inpainted with LaMa). A two-page drawing (spread) stays one file with the middle rebuilt. Single-page scans (000.jpg, cover, spine) are flattened and cropped the same way. Every result is a PSD with layer "Original" (the page as scanned) under the fixed page; a PSD input keeps all its layers and its text boxes move with the page they sit on. Use when the user has double-page scans to divide, wants pages straightened / the scanner border removed / the gutter shadow fixed, or before manga-translator-ptbr runs on NNN-MMM named scans.
---

# split-scans - double-page scans -> one flattened PSD per page

One script does the whole job, for a file or a folder, images (jpg, png,
tif, webp) or layered PSDs:

```bash
<repo>/venv/bin/python split-scans/scripts/split_scans.py <file-or-folder> [--output DIR] [--rtl] [--spread auto|yes|no] [--pages auto|1|2] [--gutter inpaint|white|flatten|none] [--align perspective|none] [--rotate 0|90|-90|180] [--only STEM ...] [--recursive] [--also-images] [--dry-run] [--force]
```

`<repo>` is the comic-skills checkout; the script needs the shared `venv`
(opencv, numpy, pillow) and this skill's `node_modules` (ag-psd + canvas,
installed by `split-scans/setup.sh`, which the root `setup.sh` runs). The
gutter core is inpainted with manga-translator-ptbr's LaMa model
(`manga-translator-ptbr/models/lama_fp32.onnx`, downloaded by that skill's
`setup.sh`); without it the core is filled white.

## What one scan becomes

| Input stem | Pages found | Output |
| --- | --- | --- |
| `082-083` (two consecutive numbers), `030-31` | two pages | `082.psd` + `083.psd` (second number zero-padded to the first's width) |
| `082-083` and the drawing runs across the spine (a spread) | two pages, kept together | `082-083.psd`: both halves flattened, aligned, joined, the middle rebuilt |
| `000`, `264`, `000_spine`, `cover` | one page | `<stem>.psd`; a blank facing page in the scan is dropped (only the page with ink is kept) |

**Which number is which page**: by default the first number is the LEFT
page - this is how Diego names his scans (by position on the scanner, even
for manga that reads right-to-left), so leave the default for his folders.
`--rtl` makes the first number the RIGHT page, for scans numbered in
reading order; when in doubt ask, and say which you used in the report.

Every output is a PSD:

- image input -> layer **Original** (the page cut out of the scan: flattened
  and cropped, shadow as scanned) + layer **Copy** (the fixed page: shadow
  flattened, core rebuilt);
- PSD input -> every raster layer warped and cropped the same way. The bottom
  raster (the `Original` of manga-translator-ptbr files) keeps its shadow, every
  raster above it (the text-erased `Copy`) gets the gutter fix; a PSD with a
  single raster gets a fixed `Copy` added. Text layers keep their font, size,
  box and rotation and move to the page their centre lands on (a box that
  straddled the spine is slid fully onto its page). Layer order, opacity,
  blend mode and visibility are kept; groups and adjustment layers are not
  carried over (the log says so).

`--also-images` additionally writes `images/<page>.png` (the fixed page) and
`originals/<page>.png` (as scanned) for pipelines that want flat files -
manga-translator-ptbr's round scripts use them.

**Where results go: `~/Downloads/<source folder name> split/`** (a single
file: its folder's name; `COMIC_OUTPUT_DIR` replaces `~/Downloads`;
`--output` anything else). Inputs are never modified. Besides the PSDs:
`review/<stem>.jpg` (the scan with the paper outline, page quads, gutter
line and shadow band drawn, and the resulting page(s) below) and
`report.jsonl` (one line per input: outputs, gutter, edge angles, spread
decision, text layers per page). Existing outputs are skipped unless
`--force`.

## How it works (so you can read the log)

1. **Background**: the median colour of the outer 2 % of the scan. Paper,
   ink and colour art are what differs from it (black ink on a dark-grey
   lid counts too, but only inside the paper's own footprint, so the lid's
   black edge stays background). A frame as bright as paper = a scan with
   no border: the whole image is the page.
2. **Gutter**: in 8 horizontal bands, the narrow dark valley of the
   per-column paper level (95th percentile - robust to drawings) nearest the
   middle of the book; a line through the band minima gives its tilt. The
   shadow band is where that level drops under 90 % of the paper, the core
   under 35 %. A coloured stripe (a spine) is not a gutter. With no visible
   shadow a two-page name is divided at the middle of the paper.
3. **Page quad**: the outer, top and bottom paper edges are fitted as the
   *envelope* lines of the per-row / per-column extreme paper pixels, so
   black art or a missing margin cannot pull an edge inwards; the gutter
   line is the fourth side. Corners -> homography -> upright rectangle
   (perspective, skew and the keystone of a curved book in one warp). Both
   pages of one scan get the same output size.
4. **Gutter fix** (fixed layers only): the band is divided by its
   illumination - per column the brightest paper level seen along the page,
   forced to fall monotonically towards the spine, so paper comes back white
   and lines stay. Dark art next to the spine keeps a faint shadow (no paper
   there to measure). The core is inpainted with LaMa (`--gutter inpaint`,
   default), filled white (`white`), left after flattening (`flatten`) or
   untouched (`none`).
5. **Spread test** (two-page scans): what each page looks like next to the
   spine - its white margin, whether art reaches it, whether a panel border
   runs along it, how the two sides' ink profiles correlate. On its own the
   script calls a scan a spread only when BOTH pages have art running into
   the spine with no margin and no border and the two sides correlate; a
   picture that spans two pages but keeps the inner margins looks exactly
   like two facing pages and is YOUR call.

The log line per scan: `082-083: 2 page(s), gutter x=2811 tilt 1.04°
depth 0.46 band 106px core 1px (inner edges L: margin 64px, art, border |
R: margin 185px, art | corr -0.0) -> 083.psd, 082.psd | left 2129x3076
edges {...} | right ...`.

## Workflow

1. **Dry run first** on the folder: `--dry-run` writes only `review/` and the
   report (about 1 s per scan). Add `--rtl` for manga/doujinshi.
2. **Read the review sheets** (tile them, 6-8 per contact sheet): the green
   quads must hug each page, the red line must sit in the spine, the
   results below must be upright with white inner margins. Typical fixes:
   - pages stacked vertically (a scan stored sideways): `--rotate 90` or `-90`
     for that file (`--only STEM`); JPEG EXIF orientation is applied already;
   - a dark drawing mistaken for the spine, or no shadow and an off-centre
     book: `--pages 1` / `--pages 2` and check the quad; `--align none` keeps
     the raw crop when an edge fit went wrong on a damaged page;
   - a single drawing over both pages that the test called two pages (or the
     opposite): `--spread yes` / `--spread no` with `--only` those stems;
   - a book whose shadow is light and whose middle comes out fine already:
     `--gutter flatten` (no inpainting).
3. **Real run** with the decided flags (`--spread no` for a whole book that
   has no spreads is fine), per-file corrections afterwards with `--only ...
   --force`. About 30 s per 5104x3504 PSD (two rasters: read, warp, inpaint,
   write two PSDs), 3 s per JPG.
4. **Check**: `report.jsonl` has every input (no `error` lines); for PSD
   inputs the text layer counts per page add up to the source's; open a few
   `review/` sheets of the real run. When the pages go on to
   manga-translator-ptbr, run that skill's `fit_text_boxes.mjs` on the
   results (boxes keep their size here; a box that straddled the spine was
   only slid onto its page).

Report: how many scans became how many pages, the reading order used,
which scans were treated as spreads or single pages and why, pages whose
edges needed a manual flag, and the output folder.

## Inside manga-translator-ptbr

That skill runs this one first when its source folder holds `NNN-MMM`
scans (its `run_letter_round.sh` / `run_detect_round.sh` do it with
`SPLIT=auto`, writing `<OUT>/split/`, and then detect on
`split/images/<page>.png` and build the translation PSD with
`--original split/originals/<page>.png`, so the translated file keeps the
page as scanned under the cleaned, gutter-fixed Copy). Already-translated
`NNN-MMM.psd` files are split directly: their text boxes travel with the
page.

## Scripts

| Script | Role |
| --- | --- |
| `scripts/split_scans.py` | the CLI: inputs, analysis plan, warps, gutter fix, PSD/image outputs, review sheets, report |
| `scripts/page_geometry.py` | background and paper mask, gutter valley, envelope edge fits, homography, shading map, inner-edge / spread features |
| `scripts/psd_io.mjs` | `dump` a PSD's layers (rasters as PNG + text objects as JSON) and `build` a PSD from such a spec (ag-psd) |
| `package.json`, `setup.sh` | ag-psd + canvas for the PSD side |
