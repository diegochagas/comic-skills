# comic-skills

Diego's agent skills for making and working with comics and manga: an AI
comic studio (page scripts → AI-generated pages reviewed one by one, cover
and editorial as editable GIMP `.xcf` → `.cbz`), manga scans →
letter-ready or PT-BR-translated layered PSD/XCF files, a pattern-based
page downloader, and small CLI skills for comic archives, PDF/PSD
conversion, image batches and Japanese OCR, an AI text eraser
(textless copies of pages, nothing else changed), a PSD folder sync that
only replaces pages whose art was not cropped, a PSD ⇄ XCF converter that
keeps the lettering editable on both sides, and a local page index: a vision
model describes every page of a folder so that pages can be searched by what
is drawn on them.

> These skills are tailored to this machine (flatpak GIMP 3, a Higgsfield
> Plus subscription, Brazilian Portuguese as the target language). Treat them
> as examples and adapt rather than reuse verbatim.

## Layout

Flat, one directory per skill:

```
<skill>/SKILL.md      what the agent reads (workflow, how to pick flags from the request)
<skill>/README.md     human overview, only where the skill is big enough to need one
<skill>/scripts/      every script that skill runs (nothing lives outside its skill)
<skill>/setup.sh      that skill's own setup (models, tessdata, node_modules, tool checks), if it needs any
<skill>/examples/, _template/, sites/, models/, tessdata/   skill-owned assets (the last two are downloaded by setup.sh, not committed)
```

The only shared, machine-generated piece at the repo root is `venv/` (Python
deps for every skill), created by **`./setup.sh`**, which then runs every
`<skill>/setup.sh`. Everything else a skill needs lives inside it: `node_modules/` from its own
`package.json` (`manga-translator-ptbr`, `comic-downloader`, `psd-xcf-convert`),
`manga-translator-ptbr/models/`, `japanese-ocr-translate/tessdata/` (all
installed by `setup.sh`, all gitignored by the root `.gitignore`). The AI
comic projects of `generate-comic-page` live outside the repo, in
`~/Downloads/<project>/`. All commands in the skills are written relative to
the repo root.

## Skills

| Skill | Scripts | What it does |
| --- | --- | --- |
| [`generate-comic-page`](generate-comic-page/) ([README](generate-comic-page/README.md)) | `new_project.py`, `import_refs.py`, `split_scripts.py`, `gen_page.py`, `plan_panels.py`, `gen_panel.py`, `assemble_page.py`, `refdesc.py`, `make_layout.py`, `build_xcf.py`, `page_state.py`, `gimp_layout_job.py`, `status.py`, `make_lettering_guide.py`, `assemble_cbz.py`, `common.py`, `_template/` | AI comic studio, ONE page at a time with Diego approving each: imports a folder of character model-sheet examples (the agent writes a design description per character that locks every prompt), splits per-issue scripts into page jobs, generates a single TEXTLESS page through Higgsfield (`gpt_image_2_5`, empty balloons), self-QCs it, letters it for free — OpenCV finds the empty balloons, headless GIMP writes an `.xcf` with one native editable text box per balloon in CCWildWords — shows the lettered preview, then approves / applies the requested changes (single-change edits, rerolls, or free text-only layout edits) / imports more examples — and asks before generating the next page. Cover and editorial get their logo/title/body as text layers the same way. Every page's deliverable is an `.xcf`. Packs approved pages into `.cbz`. Optional panel mode: each panel is its own generation (model sheets + scenario images found in the original manga's describe-pages set), comic pages go in only as describe-pages text, and the page is assembled by code. Projects live in `~/Downloads/<project>/`. |
| [`manga-translator-ptbr`](manga-translator-ptbr/) ([README](manga-translator-ptbr/README.md)) | `detect_text.py`, `inpaint_lama.py`, `inpaint_qwen.py`, `detect_blocks.py`, `merge_columns.py`, `overlay_tiles.py`, `block_sheets.py`, `page_views.py`, `assemble_translation.py`, `merge_translations.py`, `clean_blocks.py`, `ensure_upright.py`, `build_translated_psd.mjs`, `build_translated_xcf.py`, `gimp_xcf_job.py`, `verify_translated_psd.mjs`, `validate_psds.mjs`, `preview_psd_text.py`, `build_two_source_psd.mjs`, `list_layers.mjs`, `list_text_layers.mjs`, `export_layer.mjs`, `set_text_layers.mjs`, `add_and_fill_text_layers.mjs`, `scan_placeholders.mjs`, `annotate_text_boxes.py`, `run_letter_round.sh`, `run_detect_round.sh`, `run_build_round.sh`, `run_apply_round.sh`, `examples/` | Manga/doujinshi/art-book scans → layered PSDs (Photoshop text boxes) or XCFs (native GIMP text layers, via headless GIMP): `Original` + `Copy` with the text erased (solid fill on plain backgrounds, LaMa - or a local Qwen-Image-Edit with `INPAINT=qwen` - inpainting over art) + one editable Photoshop paragraph text box per block. Mode C leaves "Lorem ipsum" in CCWildWords for a human letterer, fully automatic. Mode B (any scan size, tiled detection for 7000×10000 pages with tiny print) has the agent review the boxes, merge Japanese columns into paragraphs and write the Brazilian Portuguese itself. Mode A fills the placeholder boxes of existing PSDs. ONNX detection + ag-psd; GIMP only for XCF output. |
| [`clean-texts`](clean-texts/) | `clean_texts.py` | Erases the text of an image or of every image in a folder through Higgsfield (`gpt_image_2_5`) - or, with `--backend local`, free and offline through the text detector + a local Qwen-Image-Edit in ComfyUI - and changes nothing else: the source is mirror-padded to an aspect ratio the model accepts and the output brought back to its exact geometry, then only the regions where text was erased are taken from the model — every other pixel is the original's. `--language` erases only the text in one language; `--keep` protects what is named; `--reuse-raw` with `--drop-region` / `--restore-threshold` fixes a result for free. The agent QCs every result. PNGs with the original names. |
| [`comic-downloader`](comic-downloader/) ([README](comic-downloader/README.md)) | `download.cjs`, `sites/<name>/download.config.json` | Downloads comic/magazine page images whose URLs follow a pattern (numbered pages, issues with dates, galleries, URL lists) from JSON site profiles; dry-run first, skips existing files. Bundled profile: Dorothee Magazine. |
| [`comic-archive`](comic-archive/) | `images_to_cbr.py`, `cbr_to_images.py` | Pack image folders into `.cbr`/`.cbz` (optional JPEG conversion, max height, quality) and unpack `.cbr`/`.cbz`/`.zip` archives (RAR via unrar/7z; `--first-only` for covers). The SKILL.md maps what the user asks for to the flags. |
| [`pdf-psd-convert`](pdf-psd-convert/) | `pdf_to_images.py`, `psd_to_jpg.py` | PDF pages → JPG at any DPI (one folder per PDF or one shared folder); `.psd`/`.psb` → JPG recursively, keeping folder structure, with matte color and an optional all-layers-visible render. |
| [`psd-sync`](psd-sync/) | `sync_psds.py` | Compares the same-named PSDs of two folders by their art layer (bottom-most pixel layer covering the canvas, `Original` in Diego's files) and replaces folder 2's copy with folder 1's — old copy to the trash, new file moved in — only where the artwork was **not** cropped. Pages whose art was cut on any side (with or without a resize afterwards, found down to half a side and reported in pixels) stay where they are in both folders. Dry run by default; `--apply` is the only thing that moves anything. |
| [`psd-xcf-convert`](psd-xcf-convert/) | `convert.py`, `psd_text_info.mjs`, `gimp_convert_job.py`, `write_psd_text.mjs` | Photoshop PSD ⇄ GIMP XCF for a file or a folder (a folder with both kinds converts each file to the other format) with the text still **editable**: Type layers ⇄ native GIMP text layers — same font (PostScript name ⇄ `Family Style` through fontconfig, missing fonts substituted and reported), size, colour, justification, leading, tracking, paragraph box or point text, rotation, mixed bold/italic/colour runs — and the text outline both ways: GIMP's Filters > Text Styling (`gegl:styles`) outline/shadow ⇄ Photoshop's live Layer Styles Stroke/Drop Shadow, on text and on any other layer. Pixels, groups, masks, blend modes and opacity travel through GIMP's own PSD import/export (headless GIMP, one start per 15 files); ag-psd reads and writes the Type layers and Layer Styles. A PSD → XCF → PSD trip comes back with its own fonts, boxes and angles. |
| [`docx-odt-convert`](docx-odt-convert/) | `convert.py` | Word `.docx`/`.doc` ⇄ LibreOffice `.odt` for a file or a folder through a private headless LibreOffice, with the **table of contents updated** (every entry with its page number; a generated `.docx` only has an empty TOC field) and `Heading N` styles given their outline levels (without them LibreOffice's TOC stays empty); optional removal of hint paragraphs; new folder in `~/Downloads`, or `--in-place --trash` to switch a document library over. |
| [`describe-pages`](describe-pages/) | `describe_pages.py` | Describes every page image under a folder (recursive) with a local vision model in Ollama (Qwen3-VL 4B, `qwen3-vl:4b`, free, offline, ~5 s/page; see "Local models" below): page type, summary, the objects drawn, characters, setting, what the text is about. Saved as a named set in `~/Downloads/<name> descriptions/` (`pages.jsonl` + `index.json`), resumable, source never touched. |
| [`find-pages`](find-pages/) | `find_pages.py` | Searches a describe-pages set for one or more terms ("computer", "boy with goggles"; AND, `--any`, `--type cover`, `--field objects`) and prints the paths of the original page images, ranked, with the matching text (`--show`); the agent verifies the top hits against the images before reporting. |
| [`image-utils`](image-utils/) | `rotate_images.py`, `stretch_pngs.py` | Rotate every image in a folder in place by N degrees; stretch every PNG to exact W×H into `output/`. |
| [`japanese-ocr-translate`](japanese-ocr-translate/) | `transcribe_japanese_images.py`, `translate_japanese_texts_ptbr.py`, `tessdata/` | Tesseract OCR of a folder of Japanese scans into one block-per-page TXT, then Google-translate it to PT-BR keeping the blocks — a rough reading pass, not lettering. |

Each `SKILL.md` documents the scripts' flags and, for the CLI skills, a table
of "what the user says → which flags to pass".

## Consumers

Each harness's `skills/` directory in this repo is a real directory whose
entries are symlinks into the skill folders, so one edit reaches all of them:

| Harness | Skills directory | How it links |
| --- | --- | --- |
| Claude Code | `.claude/skills/` | per-skill symlinks → `../../<skill>` (own skills) and → `../../.agents/skills/higgsfield-*` (vendored) |
| Codex / shared | `.agents/skills/` | per-skill symlinks → `../../<skill>`, plus the vendored `higgsfield-*` skills as real directories |
| both | `CLAUDE.md`, `AGENTS.md` | `AGENTS.md` is a symlink to `CLAUDE.md`; the text is harness-neutral |

The skills are project-scoped: they load when an agent is started inside
this repo. To use them from anywhere, symlink the skill folders into the
global directories, the same way:

```sh
for s in generate-comic-page manga-translator-ptbr clean-texts comic-downloader comic-archive pdf-psd-convert psd-sync psd-xcf-convert docx-odt-convert describe-pages find-pages image-utils japanese-ocr-translate; do
  for h in ~/.claude/skills ~/.agents/skills ~/.codex/skills; do
    mkdir -p "$h" && ln -sfn ~/Projects/comic-skills/$s "$h/$s"
  done
done
```

Harnesses read `SKILL.md` at startup, so restart a running agent to pick up
a newly added skill.

## External components

| Component | Source | Where it lives | Update procedure |
| --- | --- | --- | --- |
| `higgsfield-*` skills (brandkit, generate, marketplace-cards, product-photoshoot, soul-id, video-explainer, websites, youtube-thumbnail) | [higgsfield-ai/skills](https://github.com/higgsfield-ai/skills) via `npx skills add higgsfield-ai/skills` | `.agents/skills/higgsfield-*/` (real dirs, tracked in `skills-lock.json`), symlinked from `.claude/skills/` | `npx skills add higgsfield-ai/skills` again; never edit them here |
| Higgsfield CLI | npm `@higgsfield/cli` | global npm | `npm i -g @higgsfield/cli`, then `higgsfield auth login` |
| comic-text-detector model | [manga-image-translator release beta-0.3](https://github.com/zyddnys/manga-image-translator/releases/tag/beta-0.3) | `manga-translator-ptbr/models/comictextdetector.pt.onnx` | `manga-translator-ptbr/setup.sh` re-downloads if missing |
| LaMa inpainting model | [Carve/LaMa-ONNX](https://huggingface.co/Carve/LaMa-ONNX) | `manga-translator-ptbr/models/lama_fp32.onnx` | `manga-translator-ptbr/setup.sh` |
| Japanese Tesseract data | [tesseract-ocr/tessdata](https://github.com/tesseract-ocr/tessdata) | `japanese-ocr-translate/tessdata/` | `japanese-ocr-translate/setup.sh` |
| ag-psd, canvas, pngjs | npm (`manga-translator-ptbr/package.json`) | `manga-translator-ptbr/node_modules/` | `<skill>/setup.sh` (npm install) |
| ag-psd | npm (`psd-xcf-convert/package.json`) | `psd-xcf-convert/node_modules/` | `<skill>/setup.sh` (npm install) |
| axios | npm (`comic-downloader/package.json`) | `comic-downloader/node_modules/` | `<skill>/setup.sh` (npm install) |
| Qwen3-VL 4B vision model | [Ollama library `qwen3-vl:4b`](https://ollama.com/library/qwen3-vl) | Ollama's model store (3.3 GB) | `describe-pages/setup.sh` (`ollama pull qwen3-vl:4b`) |

## Local models used for the page descriptions

`describe-pages` runs entirely on this machine through
[Ollama](https://ollama.com); no cloud model, no API key, nothing leaves the
computer. `find-pages` uses no model at all (plain word matching over the
saved descriptions).

| Model | Ollama tag | Size | Role |
| --- | --- | --- | --- |
| **Qwen3-VL 4B** (Alibaba, Q4_K_M) | `qwen3-vl:4b` | 3.3 GB, ~4 GB VRAM | **Default.** Pulled by `describe-pages/setup.sh`. ~5–7 s per page on an RTX 3050 6 GB at 1024 px. Accurate object lists on the test pages; called with thinking off. |
| Qwen3-VL 8B | `qwen3-vl:8b` | 6.1 GB, ~7 GB VRAM | Better detail, slower; pull it and pass `--model qwen3-vl:8b` (or `DESCRIBE_MODEL`). Needs more VRAM than the 3050 has. |
| Gemma 3 4B | `gemma3:4b` | ~3.3 GB | Works (already installed here) but invented objects on the test page (a sword and a scroll that were not drawn) — fallback only. |

If `--model` is not given, the script takes the first installed model of
`qwen3-vl:8b`, `qwen3-vl:4b`, `qwen2.5vl:7b`, `qwen2.5vl:3b`, `gemma3:12b`,
`gemma3:4b`, `minicpm-v`, `llava`, then any other model Ollama reports as
vision-capable. Changing the model does not touch an existing set; re-run
with `--force` to redo it. `index.json` records which model wrote each set.

## Setup

```bash
git clone <this repo> ~/Projects/comic-skills
cd ~/Projects/comic-skills
./setup.sh     # shared venv + python deps, then every <skill>/setup.sh (node_modules, ONNX models, tessdata, GIMP/tesseract checks)
```

System requirements, by skill:

- `manga-translator-ptbr`: Python 3.10+, Node.js + npm, ~300 MB for the
  two models (in the skill's `models/`). [flatpak GIMP 3](https://flathub.org/apps/org.gimp.GIMP) only
  for XCF output. The `CCWildWords-Regular` font on the machine that opens
  the files in Photoshop/GIMP.
- `psd-xcf-convert`: Node.js + npm, flatpak GIMP 3, fontconfig (`fc-list`);
  the fonts of the files installed where they are opened (missing ones are
  substituted and reported).
- `docx-odt-convert`: LibreOffice (`soffice`) and `python3-uno` (the system
  `python3`, not the venv); `gio` for `--trash`.
- `comic-downloader`: Node.js only.
- `clean-texts`: the Higgsfield CLI logged in (same account as below).
- `generate-comic-page`: the Higgsfield CLI logged in to a Higgsfield account
  (Plus plan, 1000 credits/month); flatpak GIMP 3 and the `CCWildWords`
  font visible to it (every page is delivered as an `.xcf`).
- `japanese-ocr-translate`: `tesseract` on `PATH` (`sudo apt install tesseract-ocr`).
- `comic-archive`: `unrar` or `7z` only for RAR-based `.cbr` files.
- `describe-pages`: [Ollama](https://ollama.com) running, with `qwen3-vl:4b`
  (any vision-capable model works via `--model`); a GPU with ~4 GB free
  makes it ~5 s per page. `find-pages` needs nothing beyond Python.

## Rules

`.gitignore` blocks `venv/`, every `node_modules/` and `package-lock.json`,
`manga-translator-ptbr/models/`, `japanese-ocr-translate/tessdata/`,
`tmp_worklists/` and `__pycache__/`.

**Every skill writes its results to `~/Downloads`**, never into the repo and
never into the source folder it was pointed at (scans, archives and PSDs
stay untouched; rotating images no longer overwrites them). Two environment
variables move everything: `COMIC_OUTPUT_DIR` replaces `~/Downloads` for the
tool skills, `COMIC_PROJECTS_DIR` for `generate-comic-page` projects; each
script also takes an explicit `--output` / `OUT=` when you want a specific
place.

| Skill | Default result location |
| --- | --- |
| `generate-comic-page` | `~/Downloads/<project>/` (`out/xcf/`, `out/*.cbz`) |
| `manga-translator-ptbr` | `~/Downloads/<source folder name>/` (PSD/XCF + `preview/`, `detect/`, `work/`) |
| `clean-texts` | `~/Downloads/<name>.png` (one image), `~/Downloads/<folder> clean/<name>.png` (+ `clean-texts-work/`) |
| `comic-downloader` | `~/Downloads/<profile outputDir>/` |
| `comic-archive` | `~/Downloads/<folder>.cbr`, `~/Downloads/<folder>/<chapter>.cbr`; unpacked: `~/Downloads/<folder>/<archive>/` |
| `pdf-psd-convert` | `~/Downloads/<folder>/<PdfName>/`; `~/Downloads/<folder> JPG/` |
| `psd-sync` | nothing by default (it moves files between the two folders it is given); `--json` writes `~/Downloads/<folder1 name>-psd-sync.json` |
| `psd-xcf-convert` | `~/Downloads/<folder or file name> converted/` (`.xcf` for every PSD, `.psd` for every XCF, `_preview/` with `--preview`) |
| `docx-odt-convert` | `~/Downloads/<folder or file name> converted/` (`.odt` for every `.docx`/`.doc`, `.docx` for every `.odt`); with `--in-place`, next to each source |
| `describe-pages` | `~/Downloads/<name> descriptions/` (`pages.jsonl`, `index.json`; `<name>` = slug of the source's last two path parts) |
| `find-pages` | nothing written; prints paths of the original images |
| `image-utils` | `~/Downloads/<folder> rotated <deg>/`, `~/Downloads/<folder> <W>x<H>/` |
| `japanese-ocr-translate` | `~/Downloads/<folder>/japanese_transcription.txt` (+ `_pt_br.txt`) |

If you point an output at a syncing cloud drive (Nextcloud, Dropbox)
instead, the sync client can race a fresh PSD write and revert it within
seconds — verify a moment after writing.
