---
name: manga-translator-ptbr
description: Turn comic/manga/art-book page scans into layered, letter-ready Photoshop PSDs or GIMP XCFs (native GIMP text layers), optionally translated to Brazilian Portuguese. Every mode writes a file with an "Original" raster layer, a "Copy" layer with the detected text erased (exact solid fill on plain backgrounds, LaMa AI inpainting over art), and one editable Photoshop paragraph text box per text block. Modes - (C) placeholder: folder of raw pages -> PSDs whose boxes hold "Lorem ipsum" in CCWildWords for a human letterer, fully automatic, no review; (B) translate: raw images of any size (including 5000-10000 px scans with tiny print, covers, colophons, catalogue inserts) -> detect every text block, merge Japanese vertical columns into paragraphs, the agent reviews the boxes and writes the PT-BR, boxes come out pre-filled with the translation; (A) fill the placeholder boxes of already-built PSDs from the English scanlation and/or the Japanese raw embedded in the PSD. Use when the user asks to clean/erase text off manga pages, prepare pages for lettering, "generate PSDs/XCFs with two layers and text boxes", wants GIMP files instead of Photoshop, translate images/PSDs to Portuguese, or add translated text boxes over detected text.
---

# Manga Translator (PT-BR) - and letter-ready PSD prep

Three modes, all producing the same kind of file (raster **Original** at the
bottom, raster **Copy** with the text erased, one native *paragraph* text
layer **Text N** per text block on top). The deliverable is always the
finished file: run the mode's pipeline end to end and hand back **PSD**
files (Photoshop Type layers, ag-psd). **XCF** (native GIMP text layers,
written by headless GIMP) replaces the PSD only when the user explicitly
asks for GIMP/XCF - see "Output format" below.

- **Mode C - placeholder** (section "Mode C" below): a folder of raw pages
  -> letter-ready PSDs whose boxes hold "Lorem ipsum" in CCWildWords-Regular.
  Fully automatic, no box review, no translation: this is what to run when
  the user just wants the text cleaned off and boxes in place for a human
  letterer. (Formerly the separate `manga-letterer` skill.)
- **Mode B - translate** (section at the end): raw images of any size ->
  detect blocks, merge columns, the agent reviews the boxes and writes the
  PT-BR, PSDs come out with the translation in the boxes.
- **Mode A - fill** (right below): PSDs that already have placeholder boxes
  (from Mode C, or hand-made) get their "Lorem ipsum" replaced with real
  Brazilian Portuguese dialogue, sourced from the English scanlation and/or
  the Japanese raw embedded in the PSD.

Mode A works on any folder of page PSDs that follow this convention: one or
more raster layers holding the original-language source page(s), a final
raster layer with the cleaned/lettered art, and native Photoshop paragraph
text layers positioned over each speech balloon, each pre-filled with
placeholder Latin text.

## Division of labor

The scripts below do all the mechanical PSD I/O byte-exactly (no
re-rasterizing, no touching any layer but the text content). Translation
judgment — reading the source art, matching each box to the right line of
dialogue, writing natural PT-BR that fits a balloon — is done by you
(the agent), not by any script.

## Paths

`<repo>` is the comic-skills checkout (the folder holding `setup.sh`). This
skill's scripts live in `<repo>/manga-translator-ptbr/scripts/`; every command
below is written relative to `<repo>`. Python scripts run with
`<repo>/venv/bin/python` (onnxruntime, opencv, numpy, pillow); Node scripts
need `<skill>/node_modules` (ag-psd, canvas, pngjs - `npm install` in
`manga-translator-ptbr/`, done by `setup.sh`); the ONNX models are
`<skill>/models/comictextdetector.pt.onnx` (text detector) and
`<skill>/models/lama_fp32.onnx` (LaMa inpainting, optional - without it
busy-background erases fall back to OpenCV Telea; `INPAINT=qwen` sends those
regions to a local Qwen-Image-Edit instead, see "Local AI inpainting"). The venv comes from
`<repo>/setup.sh`, the rest from `<skill>/setup.sh` (which the root one runs).
PSDs are written by ag-psd (no GIMP). XCF output needs flatpak GIMP 3
(`org.gimp.GIMP`; `GIMP_CMD` env for another launcher).

**Where results go: `~/Downloads/<source folder name>/`** - the `<out_dir>`
/ `OUT` / `OUT_DIR` of every command below (`COMIC_OUTPUT_DIR` replaces
`~/Downloads` as the root; the `run_*_round.sh` scripts default to it).
PSDs/XCFs sit at its top level, with `preview/`, `detect/`, `tr/` or
`translations/`, `final/` and `work/` (scratch: upright PNGs, tiles) beside
them. Never write into the source folder: scans stay untouched, and source
folders usually live in a syncing cloud drive that races fresh writes.
Mode A, which edits PSDs that already exist, works on COPIES: copy the
PSDs into `~/Downloads/<their folder name>/` first (`cp -n`, so a resumed
run keeps its progress) and run every `set_text_layers.mjs` /
`add_and_fill_text_layers.mjs` / `run_apply_round.sh` call on those copies.
Use another location only when the user names one.

## Output format: PSD by default, XCF only on request

**Default = run the whole pipeline of the chosen mode to the end and deliver
PSD files** (detect -> erase -> build -> verify -> preview -> batch
validation -> report). Do not stop after detection or cleaning, and do not
ask which format the user wants: produce PSDs unless the request itself
says GIMP / XCF ("gera em xcf", "quero abrir no GIMP", "arquivos do GIMP").
Only then build XCFs *instead of* PSDs (not both) with the same inputs:

`build_translated_psd.mjs` and `build_translated_xcf.py` take the SAME
positional arguments and flags (`<source> <blocks.json> <out>` +
`--copy-image`, `--no-copy`, `--placeholder [text]`, `--font`), so any
command below that builds a PSD builds an XCF by swapping the script and the
extension. The round scripts take `FORMAT=xcf` (default `psd`).

```bash
python3 manga-translator-ptbr/scripts/build_translated_xcf.py <src> <blocks.json> <out>.xcf --copy-image <cleaned.png> [--placeholder] [--preview <jpg>]
python3 manga-translator-ptbr/scripts/build_translated_xcf.py --job <pages.json>     # batch: list of {source, blocks, out, copy_image, placeholder, preview}
```

PSD specifics: every text box the skill writes or fills
(`build_translated_psd.mjs`, `build_two_source_psd.mjs`,
`add_and_fill_text_layers.mjs`, `set_text_layers.mjs`) has its text language
set to **Portuguese: Brazilian** (Adobe text engine code 11, the same value
Diego's own Photoshop stores), so Photoshop spell-checks and hyphenates the
lettering as PT-BR instead of the default English: USA.

XCF specifics: each box is a GIMP text layer in fixed-box (paragraph) mode
with the block's font, size, colour and justification; `rotate` is applied
as a lossless 90/180-degree transform (GIMP keeps it a text layer but flags
it "modified": editing the text later re-renders it unrotated, rotate again
after). The script reloads the saved file and checks layer names, text count
and that every translation is present, so there is no separate verify step;
`--preview` makes GIMP render a flattened JPG (real font rendering - better
than `preview_psd_text.py`). GIMP starts in ~2-15 s per invocation, so batch
pages with `--job` when building many. Fonts must be visible to the GIMP
flatpak: `CCWildWords-Regular` installed only for Photoshop falls back to the
context font (the log line says so; the letterer changes the font in GIMP).
`list_*_layers.mjs`, `set_text_layers.mjs`, `verify_translated_psd.mjs` and
`validate_psds.mjs` are PSD-only.

## Scripts (in `manga-translator-ptbr/scripts/`, run with `node manga-translator-ptbr/scripts/<script>.mjs ...` from the repo root)

- `list_layers.mjs <psd>` — lists every layer (raster + text) with name,
  position, size; text layers include their current text. Use this first on
  each PSD to see what's there.
- `list_text_layers.mjs <psd>` — same, but text layers only. Faster when you
  already know the raster layout.
- `export_layer.mjs <psd> <layer_index> <out.png>` — exports one raster
  layer to a PNG (and prints its position/size) so you can view the source
  art with the Read tool. Needed because a raw `.psd` isn't directly
  viewable as an image.
- `set_text_layers.mjs <psd> <edits.json> [--output <path>]` — writes new
  text into one or more text layers. `edits.json` is `{ "<layer index or
  name>": "new text", ... }`. Defaults to overwriting the PSD in place;
  everything except the text layers' content is preserved byte-exact.
- `scan_placeholders.mjs <folder>` — lists every text layer still holding
  "Lorem ipsum" across a folder of PSDs; run it at the end of a Mode A pass.
- `add_and_fill_text_layers.mjs <psd> <page.json> [--output]` — adds boxes
  AND sets their final text in one write (`{"blocks": [[x,y,w,h]...],
  "texts": [...]}`) - for flaky network/shared-folder mounts where two
  back-to-back read-modify-writes raced and left duplicate layers.
- `annotate_text_boxes.py <image> <layers.json> <out.png>` — draws numbered
  boxes from a `list_layers.mjs` dump over the page, 2x, to match layer
  indices to balloons by eye.
- `run_apply_round.sh` — `IMG_DIR=<images> [OUT_DIR=~/Downloads/<images dir name>]`; applies
  `<OUT_DIR>/translations/<stem>.json` to `<OUT_DIR>/<stem>.psd` for every
  page not yet marked `.applied`, resumable.

## Layer convention you'll encounter

- The **last raster (non-text) layer** is always the finished/lettered page
  — leave it alone, it's not a translation source.
- Every **other raster layer** is a piece of the original-language source
  page. Usually just one, named e.g. "Japanese"; sometimes several pieces
  (e.g. `Shinzo_011.jpg copy`) that together form a spread or composite —
  their `left`/`top` (from `list_layers.mjs`) tell you how they're
  positioned relative to the final page, so you can tell which piece sits
  under which text box.
- **Text layers** are the boxes to fill. Their `left`/`top`/`width`/`height`
  (in the *final page's* coordinate space) tell you which balloon they
  belong to — match them by position against the source art.

## Workflow per page

0. Work on the copy of the PSD in `~/Downloads/<its folder name>/` (see
   "Paths"), never on the user's original.
1. Run `list_layers.mjs` on the PSD. Note the source raster layer(s) and the
   text layers (position + current placeholder text).
2. Find the original-language source for this page. If you have a manifest
   mapping PSD → source chapter/page (build one with correlation against the
   raw folders if you don't — see "Resolving the source page" below), open:
   - The **English** scanlation page for that same chapter+filename, if the
     project has an English folder — usually the easiest to read, since it's
     already translated prose rather than Japanese script.
   - The **Japanese** raw page (export the PSD's own source raster layer(s)
     with `export_layer.mjs`, or open the raw file directly) when English
     isn't available, doesn't match, or a box's dialogue isn't legible in
     it.
3. For each text layer, using its box position to find the matching balloon
   in the source art, work out the line of dialogue/SFX and translate it to
   natural Brazilian Portuguese — matching register (casual speech, shouts,
   narration boxes read differently), keeping it plausible-length for the
   balloon (a text box that's tight and squarish wants a short line; don't
   pad or drastically shorten just to fit, use judgment).
4. Write all of a page's edits in one `edits.json` and apply with
   `set_text_layers.mjs` in a single call per page.
5. Spot check: re-run `list_text_layers.mjs` and confirm every layer's text
   changed and none were skipped (a "SKIP" line means the key didn't match
   any layer index/name — fix the key and retry).

Batch pages efficiently: there's no need to re-derive the source mapping per
page if you've already built a manifest (step 2) — reuse it across the whole
folder.

## Resolving the source page (when you don't already have a manifest)

Given a raw-page folder (chapters of sequentially-numbered raw pages, e.g.
`Shinzo_000.jpg, Shinzo_001.jpg, ...` restarting per chapter) and a folder of
page PSDs whose filenames don't share that numbering:

1. Export each PSD's source raster layer(s) to PNG (`export_layer.mjs`).
2. Downsample each to a small grayscale vector (e.g. 48×48, normalized) and
   compare via normalized cross-correlation against the same vectors computed
   for every raw page across all chapters (full search, not limited to
   nearby pages — chapter boundaries don't line up with PSD filenames).
   A true match scores close to 1.0; unrelated pages score well under 0.5.
3. For a PSD with multiple source pieces (a stitched spread, or several
   named layers), correlate each piece separately — a two-page spread stored
   as one wide layer should be split into left/right halves first and each
   half correlated independently (remember Japanese manga reads
   right-to-left: in a spread, the LEFT half is usually the chronologically
   LATER raw page).
4. Once you know a piece's chapter + raw filename, the English scanlation
   page (if the project has one) is very often the *same filename* inside
   the corresponding English chapter folder — check for that before assuming
   you need to build any fuzzier English-side matching.

This resolution is the slow/mechanical part — when translating a whole
folder, do it once for every PSD up front (cache the results) rather than
re-deriving it per page.

---

## Mode C - raw images -> letter-ready PSDs with placeholder boxes (no translation)

Output per image: `<out>/<stem>.psd` with **Original** (untouched scan),
**Copy** (every detected text stroke erased - and only text: the model's
text-block boxes and text-line map gate the stroke segmentation, so
stroke-like art such as borders, hatching and screentone stays untouched)
and one paragraph Type layer **Text N** per detected block, pre-filled with
"Lorem ipsum ..." in CCWildWords-Regular (10-32 px, from the box height) so
the letterer sees the lettering font and just selects-and-types. Erased
regions are filled with the predominant surrounding colour (exact solid fill
on plain white/black/grey/coloured backgrounds; LaMa inpainting over art).
Every page gets a PSD even with zero blocks (two identical rasters, no
boxes), so a folder converts completely.

**Restoration is manual by design** (user decision, 2026-08-26): if the
erase damaged something the user wants back (a signature, an SFX crossed by
the gate), they copy that area from the Original layer in Photoshop. Do NOT
chase small imperfections - faint inpaint ghosts and the odd missed glyph are
expected. There is no box-review step in this mode; if the user wants
reviewed/merged boxes, that is Mode B with placeholder text (run
`build_translated_psd.mjs ... --placeholder` on a merged/assembled blocks json).

Inputs: a **source folder** of images (jpg/jpeg/png) - ask if not given;
**output dir**, default `~/Downloads/<source folder name>/` (PSDs there,
previews in `<out>/preview/`, detection artifacts in `<out>/detect/`);
optional page subset.

### C1. Whole folder, resumable (the normal way)

```bash
SRC=<images dir> [OUT=~/Downloads/<images dir name>] [FORMAT=psd|xcf] [BUDGET=500] [PAGES="010 011"] manga-translator-ptbr/scripts/run_letter_round.sh
```

Call it repeatedly until it prints `ALL DONE` (it stops starting new pages
after `BUDGET` seconds so a shell call never hits the tool timeout; pages
with a PSD are skipped). Per page it runs `detect_text.py` then
`build_translated_psd.mjs --placeholder --copy-image` and writes a preview.
A page with many SFX over art takes 1-3 min (LaMa, ~3-4 s per 512 px patch
on 2 CPU cores); plain manga pages ~10-20 s.

### C2. Same thing, step by step (single pages, or when debugging)

```bash
<repo>/venv/bin/python manga-translator-ptbr/scripts/detect_text.py <out_dir> <img1> [<img2> ...]
```

Writes into `<out_dir>/detect/`: `<stem>_cleaned.png` (the future Copy
layer), `<stem>_mask.png` (every touched pixel), `<stem>_overlay.jpg`
(red = solid fill, green = inpainted, yellow = detected strokes outside any
text block - left untouched) and `<stem>_detect.json` (`text_blocks`,
erased components with method + sampled `bg_color`, `inpaint_method`).
Unreadable images are `SKIP`ped, not fatal. Batch several pages per call;
one 1024 px inference is ~1.4 s.

```bash
node --max-old-space-size=3072 manga-translator-ptbr/scripts/build_translated_psd.mjs <img> <out_dir>/detect/<stem>_detect.json <out_dir>/<stem>.psd --copy-image <out_dir>/detect/<stem>_cleaned.png --placeholder
<repo>/venv/bin/python manga-translator-ptbr/scripts/preview_psd_text.py <out_dir>/<stem>.psd <out_dir>/detect/<stem>_cleaned.png <out_dir>/preview/<stem>.jpg --max 1400
```

`--placeholder [text]` fills every block that has no text; the detect json
is used as the blocks json directly. `--copy-image` makes Copy the cleaned
render (without it Copy is a pixel-identical duplicate of Original, for a
letterer who erases by hand). For GIMP output use
`build_translated_xcf.py` with the same arguments plus `--preview
<out_dir>/preview/<stem>.jpg` (it verifies and previews by itself).

### C3. Spot-check and verify

Read each preview (and the overlay when something looks off) at page scale
and confirm nothing is grossly wrong: a missing page, a huge miscoloured
patch, plainly visible text the model caught in the overlay but that is
still on the Copy layer. Then verify the batch:

```bash
node manga-translator-ptbr/scripts/validate_psds.mjs <out_dir> --placeholder
```

(Original + Copy present, one Text layer per detected block, none empty.)
Report pages processed, output location, layer structure, and per page:
text the model left (yellow in the overlay is usually protected art, but
check for real text the gate skipped) and art areas that were inpainted
(green) and may want manual restoration from Original.

Photoshop notes: the font is referenced by PostScript name only (no font
data embedded) - a machine without CCWildWords-Regular substitutes a
fallback with a missing-font warning, editability unaffected. Photoshop
shows a one-time "update text layer" prompt per box the first time it is
touched; normal for programmatically written text layers. The default
`<out_dir>` under `~/Downloads` is local; if the user names one inside a
syncing cloud folder (Nextcloud, Dropbox) the sync client can race a fresh
write and revert it within seconds - verify a moment after writing.

---

## Mode B - raw images -> translated PSDs (Original + cleaned Copy + PT-BR text boxes)

Output per image: `<out>/<stem>.psd` with raster layers **Original** (the
untouched scan, pixel-exact) and **Copy** (same page with the source-language
text erased) plus one native Photoshop *paragraph* Type layer **Text N** per
text block, sized to the block and pre-filled with the Brazilian Portuguese
translation. Works on any size (verified on 7008x10208 scans with 6 pt
catalogue print, and on a 117-page A4 art book at ~2100x3000).

Pass `--copy-image` to make Copy the cleaned render; without it Copy is a
pixel-identical duplicate of Original (the older behaviour, still valid when
the letterer wants to erase by hand).

All commands run from the repo root (`<repo>`) with `<repo>/venv/bin/python`
(in a fresh sandbox `pip install --break-system-packages onnxruntime
opencv-python-headless numpy pillow`). For 7000x10000 pages give node
`--max-old-space-size=4096`.

### Pipeline at a glance (whole-folder run)

```
detect_blocks.py      -> <stem>_detect.json         (raw blocks, one per column/line)
merge_columns.py      -> <stem>_merged.json         (paragraph-sized blocks) +
                         <stem>_merged_overlay.jpg  (numbered, 1000 px - READ THIS to translate)
  [you write]            tr/<stem>.json             (PT-BR keyed by overlay index)
assemble_translation.py -> final/<stem>_blocks.json (boxes + texts + styles, page px)
clean_blocks.py       -> final/<stem>_cleaned.png   (erased using the FINAL boxes)
build_translated_psd.mjs --copy-image -> <stem>.psd
verify_translated_psd.mjs --copy-image
preview_psd_text.py   -> preview/<stem>.jpg
```

Steps B1-B2 below are the older single-page path (hand-authored
`_manual.json` + `merge_translations.py`); it still works and is the right
choice for one-off pages with tricky layout. B1b-B4b are the folder path.

### B1. Detect text blocks (no cleaning)

```bash
python3 manga-translator-ptbr/scripts/detect_blocks.py <out_dir> <img1> [<img2> ...] [--min-tile 2048] [--no-fullpage]
```

Writes `<out_dir>/detect/<stem>_detect.json` (`text_blocks` = `[x,y,w,h]`
page px, reading order), `<stem>_overlay.jpg` (numbered boxes, 2000 px), and
`<stem>_raw.json`. Runs the comic-text-detector at page scale plus
overlapping tiles (`--tile`, default 2048 px source -> 1024 model input) so
tiny print on big scans is found; boxes are merged into blocks with
compactness limits so dense catalogue pages don't fuse into one blob.

- One 1024 px inference is ~1.4 s (with denormal-as-zero, which the scripts
  set - without it the same model takes ~90 s). A 6000 px page with
  `--min-tile 2048` is ~25 s; a 7000x10000 insert ~40 s. Keep each shell
  call under the tool timeout: one or two pages per call.
- Manga-like pages (colophons, captions, catalogue text) detect well. Large
  display logos, low-contrast spine text and stylised titles are usually
  missed or badly boxed - expect to fix those by hand (B2).
- JPEGs with an EXIF rotation tag are processed on the raw pixel grid;
  an EXIF-free `<stem>_upright.png` is written next to the json and
  recorded as `source_for_psd` - use THAT as the PSD source, otherwise
  node-canvas applies the rotation and the boxes land on the wrong grid.
  When the tag is *correct* (a book scanned sideways, e.g. the 7008x10208
  Saint Seiya CLAMP doujinshi spreads tagged orientation 6), pass
  `--apply-exif` so the whole pipeline works in the displayed orientation
  (the PSD then opens upright, like the JPEG does in a viewer). For 71 MP
  scans the upright PNG is ~60-100 MB each: `--upright-dir <out_dir>/work/up`
  keeps them in the scratch folder (they are regenerable; the round
  scripts delete them as they go).
- `merge_columns.py --col-width N`: max width of one vertical text column.
  Default 130 px suits ~2000 px pages; use ~300 for 600 dpi scans.

### B2. Review and fix the blocks

Look at `<stem>_overlay.jpg` for the coarse picture. To *read* the text and
*measure* coordinates, render zoomed, gridded tiles (rulers show page px):

```bash
python3 manga-translator-ptbr/scripts/overlay_tiles.py <out_dir>/detect/<stem>_detect.json <tiles_dir> --scale 0.6 [--region x,y,w,h]
python3 manga-translator-ptbr/scripts/block_sheets.py  <out_dir>/detect/<stem>_detect.json <sheets_dir> --scale 0.6   # per-block crops
```

Keep every tile <= ~1400x1900 px so the Read tool shows it unscaled -
coordinates read off a downscaled view are systematically wrong (this bit
us: a 2640 px tile displayed at 2000 px shifted every box by 1.32x).

Overrides go in `<out_dir>/detect/<stem>_manual.json`, then re-run B1
(the model is skipped when `replace` is present):

```json
{"drop": [[x,y,w,h], ...],            // detected boxes whose centre falls inside are removed
 "add":  [[x,y,w,h], ...],            // appended (after the detected ones)
 "replace": [[x,y,w,h], ...],         // use exactly this list, in this order
 "snap": false}                       // true = tighten each hand box to the ink inside it
```

`snap` works on plain backgrounds; leave it false over art / low-contrast
text. Detected blocks are sorted into reading order *before* the manual
list is applied, so a `replace`/`add` list keeps its own order - that order
is what the translation indices below refer to.

What to box: every printed text that is *part of the page* - titles,
captions, prices, form labels, stamps ("品切れ" -> "ESGOTADO"), ISBN
lines, copyright, page numbers. Skip text that is artwork inside
reproduced book-cover thumbnails.

### B1b. Merge columns into paragraphs (folder path)

```bash
python3 manga-translator-ptbr/scripts/merge_columns.py <out_dir>/detect/*_detect.json [--width 1000]
```

The detector returns one block per *vertical column* of Japanese text (a
dense interview page gives 100+ thin boxes) and sometimes one block per
*line* of a horizontal caption. Neither is a sensible Photoshop box for a
PT-BR translation, which runs horizontally. `merge_columns.py` unions:

- neighbouring columns of one paragraph (adjacent horizontally, sharing
  >= 60% of their height),
- fragments of a single column stacked vertically,
- a column group directly above/below another whose x-range contains it
  (columns interrupted by a photo),
- stacked lines of one horizontal caption.

It writes `<stem>_merged.json` (same schema, `text_blocks` in reading order:
rows top->bottom, right-to-left inside a row of columns, left-to-right for
horizontal text; the raw list is kept as `text_blocks_detected`) and
`<stem>_merged_overlay.jpg`, a `--width` px page with every block numbered.

**That overlay is the working document.** Read it to see the layout and the
box numbers; zoom into the page itself (crops via PIL at 0.7-1.2x) to
actually read the Japanese - the 1000 px overlay is too small for body text.

Merging is heuristic. On art-book caption pages it lands well; on dense
interview pages expect to hand-author the paragraph boxes with `replace`
(next step), which is often faster than fighting the merge.

### B2b. Translate against the overlay

Write `tr/<stem>.json`, one per page. **All coordinates are in OVERLAY
pixels** (the `--width` of the merged overlay, default 1000), so boxes can be
read straight off that image; `assemble_translation.py` scales them to page
px.

```json
{"texts":   {"1": "...", "3": "..."},
 "styles":  {"1": {"color": "#ffffff", "rotate": 90, "size": 40, "align": "left"}},
 "drop":    [2, 4],
 "box":     {"5": [x, y, w, h]},
 "add":     [[[x, y, w, h], "texto", {"color": "#d0202a"}]],
 "replace": [[[x, y, w, h], "texto", {}]]}
```

- `texts` / `styles` are keyed by the **merged overlay index** (1-based).
- `drop` discards a block: use it for false positives, for logos and artwork
  the detector boxed, and for handwritten margin scribbles you are not
  translating.
- `box` overrides one block's rectangle (a merged box that swallowed a
  neighbour, or a title box that should span the banner).
- `add` appends boxes the detector missed - display titles, spine text,
  section headers on dark bands.
- `replace` ignores detection entirely and uses exactly this list, in this
  order. **Use it for any page whose layout you are re-cutting** (dense
  interviews, contents pages, letter columns): it is the least fiddly way to
  express "here are the 18 paragraphs and what each says".

A box taller than 2.5x its width and under 150 page px wide gets
`rotate: 90` automatically unless a style says otherwise, so narrow side
captions still read down the column like the original did.

### B3. Translate (single-page path)

Write `<stem>.json` (one per page) with 1-based block indices:

```json
{"texts":  {"1": "Tenkuu Senki Shurato\nLIVRO MEMORIAL", "2": "Preço: ¥1.500 (¥1.456 + imposto)"},
 "styles": {"1": {"rotate": 90, "color": "#ffffff", "align": "left", "font": "Arial", "size": 40}}}
```

Style keys are optional per block: `rotate` 0/90/-90/180 (90 = reads
top-to-bottom, use it for spines and for Japanese vertical columns; a
section printed upside down is 180 for horizontal lines and -90 for its
columns), `color` (default: auto black/white from the block's luminance),
`size` px (default: auto-fit the text into the box), `align`, `font`
(default CCWildWords-Regular).

Translation conventions used so far: keep proper names/titles that are
already Latin (LOVE SONG, BANDAI, series names), Japanese titles in romaji
(天空戦記シュラト -> "Tenkuu Senki Shurato"), 大図鑑 -> "Grande
Enciclopédia", 定価 -> "Preço:", 本体 -> "(¥N + imposto)", 税込 -> "(com
imposto)", 予価 -> "preço previsto", 発売予定 -> "lançamento previsto",
品切れ -> "ESGOTADO", 残部僅少 -> "ÚLTIMAS UNIDADES", 注文書 -> "FORMULÁRIO
DE PEDIDO", 書店印 -> "Carimbo da livraria", 取次 -> "distribuidora".

From the Shurato art book (anime setting-material books generally):
設定資料集 -> "Coletânea de material de ambientação", 八部衆 -> "Os Oito
Guardiões", 神将 -> "general divino", 光流(ソーマ) -> "fluxo de luz (Soma)",
神甲冑(シャクティ) -> "Armadura Divina (Shakti)", 転生 -> "reencarnação",
必殺技 -> "golpe especial", 真言 -> "mantra", スタッフ表 -> "equipe",
声優 -> "dublador(a)", 絵コンテ -> "storyboard", 作画監督 -> "direção de
animação", 初期設定 -> "material de produção inicial", おまけ -> "extras",
同人誌 -> "fanzine". Titles of episodes/albums stay translated, series and
company names stay as they are (Tatsunoko, King Record, BANDAI). Mantras and
attack names stay in romaji ("On Shura Sowaka", "Shura Mahaken"). Keep the
book's chatty fan-magazine register - it jokes about the characters, so the
PT-BR should too. Song lyrics: leave a bracketed placeholder rather than
reproducing them.

A convenient way to keep boxes and texts together is a small python file
with `(box, text, style)` tuples that writes both `<stem>_manual.json`
(`replace`) and `<stem>.json` - see `Downloads/psd/translations/*_blocks.py`
style from the 2026-09-03 run (the merged `*_blocks.json` there are the
exact inputs used).

### B4. Build and verify

```bash
python3 manga-translator-ptbr/scripts/merge_translations.py <out_dir>/detect/<stem>_detect.json <stem>.json <stem>_blocks.json
node --max-old-space-size=4096 manga-translator-ptbr/scripts/build_translated_psd.mjs <source_for_psd> <stem>_blocks.json <out>/<stem>.psd
node --max-old-space-size=4096 manga-translator-ptbr/scripts/verify_translated_psd.mjs <out>/<stem>.psd <source_for_psd> <stem>_blocks.json
python3 manga-translator-ptbr/scripts/preview_psd_text.py <out>/<stem>.psd <source> <preview.jpg> --max 2600
```

`merge_translations.py` reports empty/unknown indices. `verify_*` checks
layer names, that Original/Copy are pixel-identical to the source, and that
every Text N carries its translation. `preview_psd_text.py` draws the boxes
and (approximate, DejaVu) text over the page - read it to catch a wrong
mapping (text in the wrong box), wrong rotation or a box that drifted.
`list_text_layers.mjs` now reports rotated boxes' page-space rect plus
`rotate`, `fontSize`, `color`, `font`, `align`.

Building is fast (a 71 MP page in ~4 s); a PSD is ~1.5-2.5x the JPEG size.

Photoshop notes (unchanged from the lettering pipeline): the font is
referenced by name only; Photoshop shows a one-time "update text layer"
prompt per box; the box is a fixed paragraph box so long translations may
need the font size lowered - the auto-fit is a heuristic (0.55 em advance).

### B4b. Build and verify (folder path)

```bash
python3 manga-translator-ptbr/scripts/assemble_translation.py <d>/<stem>_merged.json tr/<stem>.json final/<stem>_blocks.json
cp final/<stem>_blocks.json final/<stem>_detect.json      # clean_blocks reads a detect-shaped json
python3 manga-translator-ptbr/scripts/clean_blocks.py final/<stem>_detect.json
node --max-old-space-size=4096 manga-translator-ptbr/scripts/build_translated_psd.mjs <src>.png final/<stem>_blocks.json <out>/<stem>.psd --copy-image final/<stem>_cleaned.png
node --max-old-space-size=4096 manga-translator-ptbr/scripts/verify_translated_psd.mjs <out>/<stem>.psd <src>.png final/<stem>_blocks.json --copy-image final/<stem>_cleaned.png
python3 manga-translator-ptbr/scripts/preview_psd_text.py <out>/<stem>.psd <src>.png preview/<stem>.jpg --max 1400
```

`clean_blocks.py` erases only inside the **final** boxes, so anything you
dropped (art, handwriting, logos) survives untouched on the Copy layer. Per
block it unions the page-scale stroke segmentation with a crop-scale pass
(large display text is lost at page scale) and, when the block sits on a
uniform background, fills every pixel that differs from the sampled
background colour - which catches stylised titles the model misses. Each
component is then filled with the sampled surrounding colour (exact solid
fill - white/black/grey/red... backgrounds are never AI-touched), or, when
the surroundings are busy (art, screentone, hatching), **AI-inpainted with
LaMa** (`manga-translator-ptbr/scripts/inpaint_lama.py`, model `manga-translator-ptbr/models/lama_fp32.onnx`, downloaded
by `setup.sh`) - the equivalent of Photoshop's generative fill for text over
drawings. Without the model file (or with `INPAINT=telea`) it falls back to
`cv2.inpaint`. `<stem>_clean_overlay.jpg` tints what was touched (red = solid
fill, green = inpainted) - check it before building. The detect json records
`inpaint_method` (`lama`/`telea`/`qwen`).

### Local AI inpainting with Qwen-Image-Edit (`INPAINT=qwen`)

`INPAINT=qwen` (env, on `detect_text.py`, `clean_blocks.py` and the
`run_*_round.sh` scripts) erases text over art with **Qwen-Image-Edit-2511**
running locally in ComfyUI - free, offline, and much better at rebuilding
structure (hair, faces, armor, panel lines) where LaMa smears. It is a hybrid:
each region is measured and the ones sitting on **flat screentone** (halftone
dots, no line structure - where the model leaves a flat grey patch and LaMa
continues the dot pattern) go to LaMa, everything else to the model.
`INPAINT_ROUTE=qwen` disables that and sends every region to the model.
Plain-colour fills are untouched either way, and only masked pixels change.

Costs ~100 s per page that has text over art (RTX 3050 6 GB), against ~10 s
for LaMa; a page whose text is all in balloons costs nothing extra. A region the model leaves unerased, a crop it fails on, or an
unreachable server falls back to LaMa automatically (it says so on stderr).

Requires ComfyUI with the ComfyUI-GGUF node and the four Qwen model files
listed in `scripts/inpaint_qwen.py`'s header (not installed by `setup.sh`).
`COMFYUI_URL` (default `http://127.0.0.1:8188`) and `COMFYUI_SERVICE` (a
systemd `--user` unit the script starts when the server is down) configure it:

```bash
INPAINT=qwen COMFYUI_SERVICE=comfyui <repo>/venv/bin/python \
  manga-translator-ptbr/scripts/detect_text.py <out_dir> <img> [...]
```

Run the whole folder in chunks: detect+clean is ~15-25 s/page on 2 cores and
a shell call may be capped at ~3 min, so loop 5-8 pages per call with a
"skip if the output already exists" guard.

**Verify the batch at the end**, not just per page:
`node manga-translator-ptbr/scripts/validate_psds.mjs <out_dir>` asserts
Original+Copy exist, the Text layer count matches `final/<stem>_blocks.json`
(or `tr/<stem>.json`), no Type layer is empty and no "Lorem ipsum" is left.
An empty box means a `texts` key was forgotten - and note that `verify_*` failing
does *not* delete the PSD it just wrote, so a rerun guarded on "psd exists"
will skip the broken page. Fix the json, then rebuild that page explicitly.

Timings from the 117-page Shurato art book (2026-09-04): detect+merge+clean
~45 min total, 1011 text boxes, PSDs ~40 MB each (4.9 GB for the book).

### Big-scan folder run (2026-09-05/06, 138-page 7008x10208 CLAMP Saint Seiya doujinshi)

What worked for a 71 MP/page book inside a sandbox whose shell calls are
capped at ~3 min and whose background processes are killed between calls:

- `manga-translator-ptbr/scripts/run_detect_round.sh` and `manga-translator-ptbr/scripts/run_build_round.sh` are
  time-boxed, resumable rounds: call them repeatedly until they print
  `ALL DONE` (`FORMAT=xcf` on the build round for GIMP files). `run_build_round.sh` runs, per page, `ensure_upright.py`
  (recreates the EXIF-rotated PNG that was deleted to save disk) ->
  `assemble_translation.py` -> `clean_blocks.py --budget N` (LaMa pass stops
  at the deadline and saves `<stem>_pending_mask.png`; the next call resumes
  only the leftover regions) -> `build_translated_psd.mjs` -> verify ->
  `preview_psd_text.py`, then copies the PSD to the output folder and marks
  `final/<stem>.ok`. Use `PAGES="stem1 stem2"` to redo specific pages.
- `manga-translator-ptbr/scripts/page_views.py <stem>_merged.json <dir>` renders the page as 2
  strips at 1400 px with the block numbers and a ruler in OVERLAY units -
  legible enough to read handwritten doujinshi text and to measure `add` /
  `box` rectangles directly. This replaced zooming per block.
- `clean_blocks.py` gained: `--budget`, a colour-cluster fallback (k-means on
  the box when the model's stroke mask covers < 1 % or > 40 % of a non-plain
  box: white/coloured display titles over paintings and gradients), an
  either-polarity ink refinement (light text on dark art is kept), and the
  edge-touching-component rule for plain boxes (hatching/speed lines that
  run through a box are no longer wiped). `CLEAN_DEBUG=1` prints per-box
  coverage and writes `debug_mask.png`.
- Timings on 2 CPU cores: detect ~35 s/page, clean 1.5-3 min/page (LaMa),
  build+verify ~20 s; PSDs 110-330 MB each (30 GB for the book). Mounted
  output folders may forbid deleting files: overwrite with `cp -f` instead.
- Translation files with a merged index that is neither in `texts` nor in
  `drop` produce an EMPTY text layer and `verify` fails the page - scan for
  those before building (see the check in the session log).
