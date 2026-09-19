---
name: clean-texts
description: Erase the text from an image or from every image in a folder (comic/manga pages, covers, art-book scans, screenshots, posters) through Higgsfield, or free and offline with the local backend (text detector + Qwen-Image-Edit in ComfyUI), changing nothing else - balloons stay but empty, art behind the letters is rebuilt, every pixel outside the erased text is copied back from the original. With a language ("only the Japanese", "remove the English text") only the text in that language is erased and the rest stays letter by letter. Results are PNGs with the original file names in ~/Downloads. Use when Diego asks to "clean the texts", "remove/erase the text from this image/page/folder", "limpa os textos", "tira o texto", "apaga só o japonês", or wants textless versions of pages. For letter-ready PSD/XCF files with text boxes use manga-translator-ptbr instead.
---

# clean-texts — textless copies of images, nothing else changed

You are the orchestrator and visual QC reviewer. Two backends do the erasing;
there is no LLM API usage.

| | `--backend higgsfield` (default) | `--backend local` |
| --- | --- | --- |
| Erases | any text the model sees, incl. stylised titles, SFX, tiny print | only what the detector finds (it misses some titles/SFX) |
| One language only | yes (`--language`, `--keep`) | no — all detected text goes |
| Cost / speed | ~1 credit, ~30 s per image | free, ~40 s + ~100 s per page that has text over art |
| Erases art behind text with | the model, whole page | exact fill (balloons) + Qwen (art) + LaMa (flat screentone) |
| Needs | `higgsfield` CLI logged in | ComfyUI running (see below) |

Pick `local` when Diego asks for free/offline/local, when the balance is low,
or for a big folder; Higgsfield when a single language must go, or when the
local run leaves stylised text behind.

One script, `clean-texts/scripts/clean_texts.py`, run with the repo venv
(`<repo>/venv/bin/python`; needs Pillow, OpenCV, numpy — all in the shared
venv). `<repo>` is the comic-skills checkout.

## Arguments

`/clean-texts <image-or-folder> [language]`

- `image-or-folder`: one image, several images, or a folder (not recursed).
  If missing, ask.
- `language` (optional): erase ONLY the text in that language → `--language
  "<Language>"` (English name: `Japanese`, `Chinese`, `Brazilian Portuguese`…).
  Without it ALL text goes: dialogue, captions, sound effects, titles, logos,
  signs, page numbers, watermarks, signatures.
- Anything Diego wants to survive ("keep the sound effects", "leave the
  logo") → `--keep "the sound effects"`.

## Where results go

`~/Downloads/<original name>.png` for a single image;
`~/Downloads/<folder name> clean/<original name>.png` for a folder
(`COMIC_OUTPUT_DIR` replaces `~/Downloads`; `--output` takes a `.png` path
for one image or a directory). Always PNG, always the source's exact pixel
size; a source's transparency is kept. Sources are never modified — a PNG
that already sits in the results folder is written as `<name> clean.png`.
`clean-texts-work/` next to the results holds, per image, `<name>.raw.png`
(the model's output), `<name>.mask.png` and `<name>.regions.png` (the
original with every erased region outlined and numbered). Delete that folder
once Diego approved the results.

## How "nothing else changes" is enforced

The image model redraws the whole picture, so the script:

1. mirror-pads the source to the closest aspect ratio the model accepts, and
   scales/crops the output back to the source's exact geometry;
2. **restore step** — compares original and output by local ink density and
   takes the model's pixels ONLY in the regions where text was erased; every
   other pixel is the original's, at the original sharpness (a 7000 px scan
   stays 7000 px sharp). The script prints how many regions were erased and
   the share of pixels that came from the model.

`--no-restore` delivers the raw model output instead (only if Diego asks).

## `--backend local` (free, offline)

```bash
<repo>/venv/bin/python clean-texts/scripts/clean_texts.py "<image-or-folder>" --backend local
```

Per image: `manga-translator-ptbr`'s detector finds the text, fills it with
the exact sampled background colour where the background is one plain colour
(balloons, caption boxes — pixel-perfect, no AI), and hands the rest (text
over art) to **Qwen-Image-Edit-2511** running locally in ComfyUI
(`manga-translator-ptbr/scripts/inpaint_qwen.py`) — except regions sitting on
flat screentone, which go to LaMa, which continues a dot pattern better
(`INPAINT_ROUTE=qwen` sends those to the model too). Every pixel outside the
detected text keeps its original value, so there is no restore step and no
`--reuse-raw` / `--drop-region` / `--restore-threshold` / `--language` /
`--keep` / `--fix` / `--cost` (the script rejects them).

ComfyUI must be running with the ComfyUI-GGUF node and the four Qwen files
(see `inpaint_qwen.py`'s header for names). `COMFYUI_SERVICE=<systemd --user
unit>` lets the script start it; `COMFYUI_URL` points elsewhere. If it is
unreachable the script says so and falls back to LaMa (`--inpaint lama`
forces that). QC as usual, plus: art over which text sat was redrawn by the
model — check it looks like the surrounding drawing.
`clean-texts-work/<name>_overlay.jpg` tints what was touched (red = exact
fill, green = model, yellow = strokes the detector left alone).

## Before the first generation of a session

1. `higgsfield account status` — warn Diego under 100 credits, stop and ask
   under 40.
2. Price: `clean_texts.py <source> --cost` (spends nothing). `gpt_image_2_5`
   at `--quality low --resolution 2k` is the default (~1–2 credits/image,
   verified 2026-09-18). Bump `--quality medium` only when an image keeps
   failing. NEVER `gpt_image_2` (older, 7 credits) or video models. For a
   folder: N images × ~1.3 attempts × price — if the balance can't cover it,
   tell Diego before generating.
3. Auth errors → stop and ask Diego to run `higgsfield auth login`; never
   work around auth.

## Workflow (Higgsfield backend)

```bash
<repo>/venv/bin/python clean-texts/scripts/clean_texts.py "<image-or-folder>" [--language "Japanese"] [--keep "..."]
```

**Single image:** run, QC, fix if needed, show Diego the result.

**Folder:** run the FIRST image alone (pass its path with `--output
"~/Downloads/<folder name> clean"`), QC it — if the prompt needs a `--keep`
or the language mode misbehaves, better to learn it for 1 credit. Then run
the folder: existing results are skipped, so the same command resumes after a
timeout. ~30 s per image: above ~15 images run it in the background. QC every
result; report after the run: approved / fixed / flagged, credits spent,
credits left.

### QC — Read the result AND the original (and `<name>.regions.png`)

a. **Text gone**: every text that had to go is gone — no letters, no ghost
   strokes, no pseudo-text, no grey smudge. Balloons and caption boxes are
   intact (outline, tail) and empty; art that was under text is continued
   plausibly (lines connect, screentone matches).
b. **Text kept** (language / `--keep` mode): every other text is still there.
c. **Regions**: each numbered region in `regions.png` sits on text that had
   to be erased. A region over art with no text = the model changed the
   drawing there.
d. **No text region missing**: text still visible in the result → check
   `raw.png`: erased there → restore threshold; still there → reroll.

### Fixes, cheapest first

| Problem | Fix | Cost |
| --- | --- | --- |
| A region erased text that should stay, or sits on damaged art | `--reuse-raw --drop-region N [N...]` — those regions go back to the original, pixel-perfect | free |
| Text erased in `raw.png` but still in the result (faint/small/coloured text) | `--reuse-raw --restore-threshold 30` (default 48; lower = more sensitive) | free |
| Redrawn art leaking in as regions all over | `--reuse-raw --restore-threshold 70`, or drop those regions | free |
| Model left some text, or rebuilt the art badly | `--force --fix "the sign above the door still has its text"` — name the exact defect, anchor by content, never by coordinates | 1 generation |
| Still failing after 2 rerolls | `--quality medium`; then flag it, show Diego the best try, move on | more |

Combine flags freely with `--reuse-raw`; pass the same source path (and
`--output`) as the original run so the script finds the saved raw. Max 3
generations per image.

## Field lessons

- Language mode at `quality low` sometimes erases everything. Don't reroll
  first: `--reuse-raw --drop-region` on the regions of the other language
  restores that text exactly, for free.
- If a call hits the shell timeout, DON'T resubmit blindly — `higgsfield
  generate list --json` first: the job may still be `in_progress` (resubmitting
  double-spends). Finished results are skipped on the rerun anyway.
- Whack-a-mole is real: after every reroll re-check the WHOLE image, not
  only the defect you targeted.
- `--backend local` never invents text and never changes the geometry, but a
  stylised title the detector misses stays: say so and offer the Higgsfield
  backend for that page.
- Tiny print on huge scans (the model sees at most ~3000 px) may survive:
  crop that part to its own image, clean it, and tell Diego — or use
  `manga-translator-ptbr` (detector + LaMa) for those pages.

## Report

Number of images cleaned, the results folder (full path), any image flagged
and why, credits spent and credits left.
