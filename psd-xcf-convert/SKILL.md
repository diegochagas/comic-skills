---
name: psd-xcf-convert
description: Convert Photoshop PSD files to GIMP XCF and GIMP XCF files to Photoshop PSD, keeping the text EDITABLE on the other side - Type layers become native GIMP text layers and back, with the same fonts, size, colour, justification, leading, tracking, paragraph box or point text, rotation and mixed bold/italic/colour runs - and converting the text outline both ways, GIMP's Filters > Text Styling (gegl:styles) outline/shadow to Photoshop's live Layer Styles Stroke/Drop Shadow and back. Takes a file or a folder (a folder with both kinds converts each file to the other format) and writes a new folder in ~/Downloads; pixels, groups, masks, blend modes and opacity are carried by GIMP's own PSD import/export. Use when Diego asks to "convert these PSDs to XCF", "convert this XCF to PSD", "converte os PSDs pra GIMP", "abrir esse xcf no Photoshop", "pass this file to Photoshop/GIMP with the text editable", or wants lettered pages moved between Photoshop and GIMP without the text being rasterized.
---

# psd-xcf-convert — PSD ⇄ XCF with the lettering still editable

One command, `psd-xcf-convert/scripts/convert.py` (standard-library Python,
any `python3`). It needs Node + this skill's `node_modules/` (ag-psd, from
`psd-xcf-convert/setup.sh`), flatpak GIMP 3 (`GIMP_CMD` overrides the launcher)
and `fc-list`/`fc-match`. `<repo>` is the comic-skills checkout.

```bash
python3 <repo>/psd-xcf-convert/scripts/convert.py "<file or folder>" [flags]
```

- a `.psd`/`.psb` becomes an `.xcf`, an `.xcf` becomes a `.psd`; a folder that
  holds both kinds converts every file to the other format in one run;
- results go to `~/Downloads/<source name> converted/` — the folder's name,
  or a single file's name without its extension (`COMIC_OUTPUT_DIR` replaces
  `~/Downloads`, `--output` the whole path); inputs are never touched;
- one headless GIMP start per 15 files, a few seconds per page.

## What is converted

| Photoshop | GIMP | Notes |
| --- | --- | --- |
| Type layer, paragraph (box) text | text layer, fixed box | same box, GIMP wraps inside it; GIMP starts the first line 5-15 px lower than Photoshop (the two read a font's ascent differently), so the box is nudged until the ink sits where Photoshop drew it - the nudge is stored and undone on the way back |
| Type layer, point text | text layer, dynamic | placed so the ink lands where Photoshop drew it |
| font (PostScript name) | font (`Family Style`) | matched through fontconfig, see Fonts |
| size × the layer's transform scale, colour, justification, tracking, leading, first-line indent | font size (px), colour, justify, letter spacing, line spacing, indent | |
| text language (Character panel, e.g. Portuguese: Brazilian = Adobe code 11) | text layer language `pt-br` | both directions always write Portuguese: Brazilian, whatever the source said |
| style runs (other font / size / colour, faux bold/italic, underline, strikethrough) | Pango markup on the text layer | bold/italic of a family with a real cut use that cut (`CCWildWords-BoldItalic`), faux otherwise |
| All Caps / Small Caps | the text itself in capitals | GIMP has no caps attribute |
| Type layer squeezed/stretched with Free Transform, or Character horizontal/vertical scale (SFX, tall titles) | text laid out at full height in a wider box, then the layer's width scaled to the same ratio | same line breaks and shape as Photoshop; like a rotation, editing the text in GIMP re-renders it unsqueezed (the `note:` gives the factor for the Scale tool) |
| rotated text | text layer rotated the same angle | GIMP flags a rotated text layer as modified: editing its text re-renders it unrotated, rotate again after editing |
| Layer Style **Stroke** | Filters > Text Styling (`gegl:styles`): Enable Outline, grow radius = stroke size, outline colour + opacity | on text AND on any other layer; the filter stays editable (non-destructive) |
| Layer Style **Drop Shadow** (or Outer Glow) | same filter: shadow/glow opacity, X/Y, colour, blur, grow | angle + distance ⇄ X/Y |
| Layer Style **Color Overlay** (Normal or Multiply) | same filter: colour overlay, policy Solid Color / Multiply | always at 100%; PSD → XCF skips an overlay in the colour the text already has (no visible change); other blend modes are reported, not converted |
| — | GIMP's own text outline (text tool > Outline) | XCF → PSD: becomes a Stroke with the same direction (outer/inner/centred); outline-only text gets Fill 0% |
| — | `gegl:dropshadow` filter | XCF → PSD: Drop Shadow |
| pixels, groups, masks, blend modes, opacity, visibility | same | GIMP's own PSD loader / exporter |

On the PSD side the Type layers keep GIMP's rendering as their pixels and are
flagged for redraw, so Photoshop asks to update the text layers when the file
opens: say yes, that is Photoshop drawing the text with its own engine. The
flattened image stored in the PSD (what file managers, psd-tools and
`psd_to_jpg.py` show) is GIMP's full render, outlines and shadows included.

A layer parasite `psd-xcf-convert` inside the XCF remembers what GIMP cannot
store (the rotation angle, the width squeeze, the ink nudge of a box, the
Photoshop name of a substituted font), so a
PSD → XCF → PSD trip comes back with its own fonts, boxes and angles (text
origin within ~1 px).

## Flags

| Flag | Use |
| --- | --- |
| `--to xcf` / `--to psd` | only one direction when the folder has both kinds |
| `--recursive` | include subfolders, structure kept |
| `--preview` | also write `_preview/<file>.jpg`, GIMP's render of the XCF side — use it to QC |
| `--keep-raster` | PSD → XCF: keep Photoshop's rendering of each text as a hidden layer `<name> (Photoshop render)` under the new text layer |
| `--font-map "PSName=GIMP Font Name"` | PSD → XCF: force a font, repeatable: `--font-map "ArialMT=Liberation Sans Regular"` |
| `--overwrite` | redo files already in the output folder (default: skip them) |
| `--output DIR` | output folder |
| `--keep-work`, `--timeout S` | debugging: keep the `.work-*` folder (job, log, GIMP's raw export); seconds per GIMP batch |

| Diego says | Run |
| --- | --- |
| "convert this folder of PSDs to XCF" / "pra GIMP" | `convert.py "<folder>"` |
| "convert this xcf to psd" / "pro Photoshop" | `convert.py "<file.xcf>"` |
| folder has both and he names one direction | add `--to xcf` or `--to psd` |
| "and the subfolders" | add `--recursive` |
| "use font X instead of Y" | `--font-map "Y-PostScriptName=X Style"` |

## Fonts

Photoshop stores PostScript names (`CCWildWords-BoldItalic`), GIMP lists
`Family Style` (`CCWildWords Bold Italic`); `fc-list` gives both for every
installed font and that is the whole mapping. A font that is not installed on
this machine (Arial, from a PSD made on Windows) is replaced by fontconfig's
closest match (Liberation Sans) and reported as a `note:`; the original name is
kept in the parasite and written back on the way to PSD. Going to PSD, the font
only has to exist where Photoshop runs — a missing one shows Photoshop's usual
"missing fonts" dialog and the layer stays editable.

## How to run it

1. Run `convert.py` on the path (add `--preview` unless the batch is huge).
2. Read the output: one `OK`/`FAIL` line per file, `note:` lines under it for
   everything approximated — substituted fonts, a Layer Style with no GIMP
   equivalent (bevel, gradient/pattern overlay, inner shadow…: not converted),
   a GIMP filter with no Layer Style equivalent (merged into the pixels), a
   stroke position other than Outside, a text layer that was scaled or freely
   rotated in GIMP (its angle is not stored anywhere: kept as pixels, not
   text; quarter turns ARE recognized), vertical text (becomes horizontal).
   A `width scaled to N%` note is informational: the layer looks like the
   source, it just re-renders at full width if its text is edited in GIMP.
3. With `--preview`, look at one or two `_preview/*.jpg` next to the source
   (for a PSD: `pdf-psd-convert/scripts/psd_to_jpg.py`) when the notes mention
   fonts or text — a substituted font wraps differently.
4. Report: the output folder, counts, and every note that changes what Diego
   will see (group identical notes; don't list per file what is the same in
   all of them).

Exit status 0 = every file converted, 2 = at least one failed (its partial
output is removed; the rest of the batch still runs).

## Before converting a folder: are the fonts installed?

A font that is missing on this machine is the one thing that visibly changes
a page (thin Noto Sans instead of a heavy title face). The `note:` lines name
every missing font; install it (`~/.local/share/fonts`, then `fc-cache -f`;
Diego also keeps a copy in `linux-mint-setup/steps/fonts/fonts/`) and rerun
just those pages with `--overwrite --output "<same folder>"`. A GIMP that is
already open does not see a newly installed font until it is restarted.

## Limits worth knowing

- Text is re-rendered by the other program, so line breaks inside a box can
  move by a word when the font metrics differ (always the case with a
  substituted font), and GIMP does not hyphenate where Photoshop did
  (`TECNO-LOGIA`), so a narrow caption can take one line more. GIMP clips
  what does not fit a box: PSD → XCF makes such a box taller (a `note:` says
  by how much) so no line disappears - only for text Photoshop really rendered
  and only by a line or two; an unlettered placeholder that overflows its
  balloon-sized box overflows in Photoshop too and keeps its box.
- One stroke, one shadow per layer. Photoshop's extra strokes, inner
  shadow/glow, bevel, satin, gradient and pattern overlays are reported and
  skipped; Text Styling's bevel, inner glow and image overlay likewise.
- Smart objects, adjustment layers and vector shapes are whatever GIMP's PSD
  loader makes of them (pixels or nothing); the other way, GIMP's PSD export
  merges any non-convertible filter into the layer pixels.
- 8-bit RGB/grayscale documents are the tested case.

## Scripts

| Script | Role |
| --- | --- |
| `convert.py` | the command: collects files, font table, batches GIMP, report |
| `psd_text_info.mjs` | PSD → JSON: Type layers (runs, box, transform) + Layer Styles |
| `gimp_convert_job.py` | runs inside GIMP (python-fu-eval, never with `-f`): PSD → XCF builds text layers + Text Styling filters; XCF → PSD describes them, hides the converted filters and exports GIMP's PSD |
| `write_psd_text.mjs` | GIMP's PSD export + that description → final PSD with Type layers and Layer Styles (raster bytes passed through untouched) |
