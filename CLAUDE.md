# comic-skills

One skill per top-level folder (`<skill>/SKILL.md` + `<skill>/scripts/`),
harness-neutral: this file is read as `CLAUDE.md` (Claude Code) and, through
a symlink, as `AGENTS.md` (Codex). The README's table lists every skill and
its scripts; read a skill's `SKILL.md` before running any of its scripts.

Skills: `generate-comic-page` (AI comic studio, one reviewed page at a time), `manga-translator-ptbr` (scans →
letter-ready or PT-BR translated PSD/XCF files; every text box it writes is
sized to show its whole text and boxes that cover each other are moved
apart), `split-scans` (double-page scans → one flattened PSD per page,
scanner border cut, spine shadow turned back into paper; runs first inside
manga-translator-ptbr for `NNN-MMM` scans), `clean-texts` (Higgsfield
text eraser: textless PNG copies, every pixel outside the erased text restored
from the original; the agent QCs each result), `comic-downloader`
(pattern-based page downloads from JSON site profiles), `psd-sync` (two folders of
PSDs compared by their art layer: folder 2's copy is replaced only when the
art was not cropped, and only after Diego approves the dry run),
`psd-xcf-convert` (PSD ⇄ XCF for a file or folder with the text still
editable: Type layers ⇄ GIMP text layers with the same fonts, Layer Style
stroke/drop shadow ⇄ Filters > Text Styling), `docx-odt-convert` (Word
.docx ⇄ LibreOffice .odt through a headless LibreOffice, table of contents
updated with page numbers; run with the system `python3`, which has `uno`),
`restore-photos` (scanned prints repaired by a local ComfyUI model, its
pixels kept only inside the damage mask), `modernize-photos` (an old photo
as if shot today on an iPhone: Higgsfield, else local ComfyUI),
`describe-pages` (a local
vision model in Ollama describes every page under a folder into a named set
in `~/Downloads/<name> descriptions/`), `find-pages` (searches such a set and returns the
paths of the pages that show something; the agent verifies the top hits by
looking at them), `comic-archive`, `pdf-psd-convert`, `image-utils`
(CLI wrappers that pick flags from the request). `.claude/skills/` and
`.agents/skills/` contain symlinks to those folders; the `higgsfield-*`
entries there are vendored third-party skills (`skills-lock.json`) — never
edit them.

## Shared pieces at the repo root

- Root `setup.sh` creates the shared `venv/` (Python deps for all skills)
  and then runs each `<skill>/setup.sh`, which create the rest of what is
  not committed inside the skills: `manga-translator-ptbr/node_modules/`
  (ag-psd, canvas, pngjs; its package.json is `"type": "module"`),
  `comic-downloader/node_modules/` (axios), `psd-xcf-convert/node_modules/`
  (ag-psd only - no canvas) and `manga-translator-ptbr/models/`
  (comic-text-detector + LaMa ONNX).
  A skill that needs npm packages gets its own `package.json`; the root
  `.gitignore` already ignores any `node_modules/` and `package-lock.json`.
- All script paths in the skills are relative to the repo root; Python
  scripts expect `venv/bin/python`. Scripts find `venv/` (repo root, two
  levels up) and their skill's `models/` / `_template/` (one level up) from
  their own location, so run them from anywhere but don't move them out of
  `<skill>/scripts/`.
- `manga-translator-ptbr` has three modes (C placeholder, B translate, A
  fill) on one toolchain: `detect_text.py` / `inpaint_lama.py` are the
  detector + inpainter every mode uses; `build_translated_psd.mjs` writes
  PSDs and `build_translated_xcf.py` (headless flatpak GIMP, never with
  `-f`) writes XCFs from the same blocks JSON (`--placeholder` for mode C). The
  skill always runs its pipeline to the end and delivers PSDs; XCF only
  when the request explicitly asks for GIMP files.
- Erasing text over art (`manga-translator-ptbr`'s `inpaint_lama.py`, used by
  `detect_text.py`/`clean_blocks.py` and by `clean-texts --backend local`) has
  three backends, chosen with `INPAINT`: LaMa (default), `telea`, and `qwen` =
  `inpaint_qwen.py`, a local Qwen-Image-Edit-2511 in ComfyUI (free, offline,
  best on structured art; needs a running server, see that file's header).
  `qwen` is really a hybrid: flat-screentone regions still go to LaMa
  (`INPAINT_ROUTE=qwen` forces the model everywhere), and anything the model
  misses or fails on falls back to LaMa on its own.

- `split-scans` is pure OpenCV geometry (`page_geometry.py`: paper mask from
  the scanner background colour, gutter valley per row band, envelope line
  fits of the paper edges, homography per page, illumination map of the
  shadow band) plus `psd_io.mjs` (ag-psd dump/build so every raster layer is
  warped the same way and text layers travel with their page). The gutter
  core reuses manga-translator-ptbr's `inpaint_lama.py`. Automatic spread
  detection is deliberately conservative: a picture that spans two pages but
  keeps its inner margins is the agent's call (`--spread yes --only STEM`).
  Reading order is a flag (`--rtl` = first number on the right page), never
  guessed from file names. Output is always a PSD with the page as scanned
  under the fixed page.
- `manga-translator-ptbr` text boxes: `fit_boxes.mjs` + `text_metrics.mjs`
  (node-canvas with the real font found through `fc-match`) grow every box
  it writes until its text fits - height rule calibrated on boxes Diego
  sized by hand in Photoshop: ascent + (lines-1) * 1.2 em + 1 px - and move
  boxes whose written text would collide; `build_translated_psd.mjs`,
  `set_text_layers.mjs` and `add_and_fill_text_layers.mjs` call it
  (`--no-fit` skips it), `fit_text_boxes.mjs` applies it to finished PSDs.

- `psd-xcf-convert` splits the work by who can do it: GIMP's own PSD
  loader/exporter carries pixels, groups, masks and modes; ag-psd
  (`psd_text_info.mjs`, `write_psd_text.mjs`) reads and writes what GIMP drops
  (Type layers, Layer Styles); `gimp_convert_job.py` (headless flatpak GIMP,
  never with `-f`) builds or describes the text layers and `gegl:styles`
  filters; `convert.py` maps fonts with `fc-list`. What GIMP cannot store
  (rotation angle, width squeeze of a non-uniformly transformed Type layer,
  the ink nudge that lines a box up with Photoshop's first line, original
  Photoshop font name) rides in a layer parasite `psd-xcf-convert`. GIMP traps found here: parasite bytes come back signed
  (mask with `& 0xFF`), a selection saved in the XCF turns every transform
  into a floating layer (`Selection.none` after load), selections are clipped
  to the canvas (measure ink with the layer at 0,0), markup `size=` is
  1024ths of a point at the image resolution.

- `generate-comic-page` keeps its comic projects OUTSIDE the repo:
  `~/Downloads/<project>/` (`COMIC_PROJECTS_DIR` overrides the root, `-p
  <path>` points at a project anywhere) with `project.json`, `PROJECT.md`,
  `charmap.json`, `scripts_src/`, `refs/`, `jobs/`, `work/`, `out/`. New
  projects start from `generate-comic-page/_template/` via `new_project.py`.

## generate-comic-page rules (apply to every project)

- ONE page per turn. Generate, self-QC, show Diego, stop. The next page is
  generated only after he approved the current one AND said yes to the next.
  No batch mode — don't write one.
- At most 2 generations per turn without Diego seeing a result. Panel mode
  (`"generation": "panels"`): the panels of ONE page per turn, max one fix
  per panel, then show the assembled page. In panel mode comic pages reach
  the image model only as describe-pages text; attached images are model
  sheets and scenarios (found by searching the original manga's set).
- The image model never writes text. EVERY page is generated textless
  (story pages with empty balloons) and delivered as a GIMP `.xcf` whose
  text is native text layers: `make_layout.py` (OpenCV balloon detection +
  the script's exact lines) → agent fixes the pairing on the numbered
  overlay → `build_xcf.py`. Balloon font: `CCWildWords Regular`
  (`"lettering_font"`). Text lines are copied from the job, never retyped.
  `"lettering": "ai"` exists only for projects finished before this.
- `gpt_image_2_5` at `quality low` / `resolution 2k` is the default — check
  the real price with `gen_page.py ... --cost`; bump `--quality` only if a
  page keeps failing on text/detail. `nano_banana_flash` = cheap layout-only
  reroll, `nano_banana_pro` = better editor. NEVER `gpt_image_2` (the older
  model, 7 credits) or video models.
- Check `higgsfield account status` before generating; warn under 100
  credits, stop and ask under 40.
- The agent is the orchestrator and QC reviewer; there is NO LLM API usage.
- Project-specific rules (language, continuity, fonts) live in each
  project's `PROJECT.md` — always read it before generating.

## Conventions

- Results go to `~/Downloads`, never into the repo or the source folder: a
  script's default output is `<root>/<source folder name>...` with `<root>` =
  `$COMIC_OUTPUT_DIR` or `~/Downloads` (`$COMIC_PROJECTS_DIR` for
  `generate-comic-page` projects), plus an `--output` / `OUT=` override.
  Scripts never modify their input files. New scripts follow the same rule;
  the README's "Rules" table lists each skill's default location.

- `describe-pages` writes `~/Downloads/<name> descriptions/` (same
  `COMIC_OUTPUT_DIR` rule); `find-pages` looks sets up by that name and
  writes nothing. Only Ollama (`qwen3-vl:4b`, pulled by its `setup.sh`) — no
  cloud model, and the source folder is read-only.

- `psd-sync` is the exception to "never touch the input": it trashes and
  moves the very files it is pointed at. It runs as a dry run first, Diego
  sees the list, and only then the same command with `--apply`; deleting is
  always `gio trash`/`trash-put`, never `rm`.

- Keep scripts inside their skill folder and document new ones in both the
  skill's `SKILL.md` and the README table.
- Don't commit generated content (scans, PSDs, renders, worklists); the
  `.gitignore` already covers the usual folders.

- `restore-photos` / `modernize-photos` (from the photo-restore repo):
  `modernize-photos/scripts/modernize.py` imports from
  `restore-photos/scripts/` (crop, Higgsfield helpers, ComfyUI client,
  sheets), so the two folders stay side by side. Settings live in
  `~/.config/photo-restore/` (kept under that name). In `restore-photos` the
  model's pixels are used only inside the damage mask (`restore.py`
  `diff_regions` → `composite`), colour-matched to the scan first; keep that
  contract. Results go to `~/Downloads/photo-restore/<folder name>` and
  `~/Downloads/photo-modernize/<folder name>`; no script writes next to its
  input or into a photo library, and no personal path, host or library
  location belongs in the repo.
