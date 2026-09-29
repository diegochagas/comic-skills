---
name: generate-comic-page
description: Generate an AI comic ONE page at a time through Higgsfield (optionally panel by panel, with comic pages sent as describe-pages text and scenarios found in the original manga's descriptions), with Diego reviewing every page before the next one. EVERY page is delivered as a GIMP .xcf with editable text - story pages are drawn with EMPTY speech balloons and get one native GIMP text box per balloon in the CCWildWords font, and the COVER and the EDITORIAL get their logo/title/body copy as text layers too. Takes a folder of character model-sheet examples the first time, then loops per page - generate, self-QC, show Diego, and on his answer either approve (and ask whether to generate the next page), apply the specific changes he asks for, or import more examples and retry. Use when the user asks to generate/continue/fix a comic page, make the cover or the editorial of an issue, start a new AI comic from model sheets, or says things like "/generate-comic-page megaman-nam 17", "gera a próxima página", "faz a capa da edição 18", "gera o editorial", "continua o quadrinho", "aqui tem mais exemplos do personagem".
---

# generate-comic-page

You orchestrate an AI comic studio. No LLM API is used: you (the agent
session) build the prompts, look at every result and talk to Diego;
Higgsfield draws, through its CLI, billed in credits to Diego's Plus plan
(1000 credits/month).

**The image model never writes the text.** Every page is generated
TEXTLESS — story pages with empty balloons and caption boxes — and the
text goes in afterwards as native GIMP text layers (font `CCWildWords
Regular`), so the deliverable of every page is an `.xcf` Diego can retype
and restyle, and no credit is ever burned on a misspelled balloon.

**The rule that shapes everything: ONE page per turn, and Diego approves
every page.** The image model hallucinates character designs often enough
that unattended batches waste credits and produce pages Diego rejects later.
So: generate one page, check it yourself, show it, then STOP and wait for
his answer. Never start the next page without an explicit yes.

`<repo>` is the comic-skills checkout (the folder holding `setup.sh`); run
the scripts from there with `venv/bin/python` (plain `python3` works too for
everything except `assemble_cbz.py`/`build_xcf.py`/`make_layout.py`, which
need the venv's Pillow / OpenCV).

## Where things live

Comic projects live OUTSIDE the repo, in `~/Downloads/<project>/`
(`COMIC_PROJECTS_DIR` overrides the root; every script also accepts
`-p <path to a project folder>` for a project kept elsewhere):

```
~/Downloads/<project>/
  project.json      formats: issues, script pattern, aspect, language, lettering, page_kinds, cbz naming
  PROJECT.md        this comic's own rules (style, continuity, QC priorities) - read it first
  charmap.json      character keyword -> model sheets + written design description
  scenemap.json     panel mode: location keyword -> scenario images (original manga) + description
  scripts_src/      one page-by-page script per issue
  refs/model-sheets/   character examples (imported from the folder Diego gives)
  refs/style/          2-4 style anchor pages
  refs/scenarios/      panel mode: pages/crops of the original manga showing each location
  jobs/<issue>/page_NN.json      one job per page (made by split_scripts.py)
  work/<issue>/gen/              every art attempt (textless): page_NN_tryK.png
  work/<issue>/panels/page_NN/   panel mode: plan.json + panel_KK_tryT.png (+ .prompt.txt)
  work/<issue>/layout/           page_NN.balloons.jpg (numbered overlay), page_NN.layout.json, page_NN_preview.jpg
  work/<issue>/approved/         the approved lettered page_NN.jpg (what goes in the .cbz)
  work/<issue>/state.json        per-page status
  out/xcf/<issue>/page_NN.xcf    THE DELIVERABLE: art + editable text layers
  out/                           .cbz, lettering guides
```

## Arguments

`/generate-comic-page [project] <issue> [page | cover | editorial] [folder of examples]`

- `project`: name under `~/Downloads/` or a path. Omitted + only one project
  exists → use it; otherwise ask.
- page omitted → `page_state.py -p <project> next <issue>` tells you which
  page is next (an `awaiting_review` page first, else the first `pending`).
- `cover` / `editorial` → the page of that kind in the issue (normally 1 / 2).
- A folder (or images) of examples → import them first (next section).

## First time for a comic: the examples folder

Diego gives a folder with model-sheet examples of the characters.

1. No project yet? `new_project.py <name> --title "..."`, then fill
   `project.json` / `PROJECT.md` with him and put the scripts in
   `scripts_src/` (format: see `_template/PROJECT.md`).
2. `import_refs.py -p <project> <folder>` copies the images into
   `refs/model-sheets/` (slugified names, duplicates skipped) and lists the
   sheets that are not in `charmap.json` yet. Style anchor pages go in with
   `--style`.
3. **Look at every new sheet** (Read the image) and register it in
   `charmap.json`: `keywords` (how the scripts name the character, most
   specific entries first), `sheets`, and a `description` — one prose
   sentence or two with the design facts a drawing can get wrong: hair
   shape and colour, eye colour, each outfit piece with its colour, emblems,
   proportions, what changes between variants. For a sheet showing a group,
   describe EACH member. `gen_page.py` injects these descriptions into the
   prompt as a "character design lock" next to the attached sheets — this is
   the main defence against design hallucination, so write what you SEE, not
   what you remember of the franchise. If you can't tell who a sheet shows,
   ask Diego.
4. `split_scripts.py -p <project> [issue]` (re)builds the page jobs; page
   states already recorded are preserved.

The same steps 2-3 run whenever Diego passes more examples later.

## Before generating (once per session)

1. Read the project's `PROJECT.md` and `project.json`. Follow them.
2. `higgsfield account status` — warn Diego under 100 credits, stop and ask
   under 40. Auth errors: stop and ask him to run `higgsfield auth login`;
   never work around auth.
3. Model & cost: `gpt_image_2_5`, `quality low`, `resolution 2k`, up to 14
   image references — the default in `gen_page.py`. Check the real price
   with `gen_page.py ... --cost` (free). `--quality medium|high` only when a
   page keeps failing on text/detail. `--model nano_banana_flash` is the
   cheap fallback for layout-only rerolls; `--model nano_banana_pro` is the
   better EDITOR. Never `gpt_image_2` (old, 7 credits) or video models.

## The page loop

### 1. Generate — one attempt

```
venv/bin/python generate-comic-page/scripts/gen_page.py -p <project> <issue> <page> \
    [--extra-ref <approved page or new example>]... [--fix "..."]
```

It builds the prompt (issue preamble + page prompt + character design lock +
the "draw every balloon EMPTY" suffix), attaches model sheets → extra refs
→ style refs (14 max, so on big ensemble pages the style refs are what gets
dropped — it warns about every dropped file), runs Higgsfield, saves `work/<issue>/gen/page_NN_tryK.png`, sets the page `awaiting_review`
and prints the path. `--dry-run` shows prompt + references without spending.

Always pass as `--extra-ref` the ART of the previously approved page when
the scene continues (backgrounds, costumes, helmet on/off), and of an
approved page showing any `no_sheet_characters` of this page (their look
exists nowhere else). Use the textless art — the `art` path that
`page_state.py show <issue> <page>` prints — not the lettered JPG in
`approved/`: a reference full of text invites the model to write text.

### 2. Self-QC — look at the image (Read it) before showing it

In this order:
- **Characters on-model** — compare EACH character against its model sheet
  (open the sheet if unsure): face, hair, eye colour, outfit pieces and
  colours, emblems, count of group members. This is the #1 failure.
- **Zero letters anywhere** — balloons, captions, signs and logo areas
  are empty flat white: no words, no gibberish, no style labels or
  character-name labels on the art (SFX drawn as art only where the script
  asks for one).
- **Balloon inventory** — one balloon/caption per line of the job's
  `dialogue_exact`, right shape for its type (speech / thought / scream /
  caption), tail pointing at the right speaker, big enough for its line,
  closed outline, not leaking into the gutter.
- **Story beats** — every beat of the page prompt is there.
- **Continuity & style** — against the approved pages and `refs/style/`.

If the page has an OBJECTIVE hard failure (wrong/missing text, missing
character, broken anatomy, letters on the art), you may fix it once before showing it (the
tools of step 5). **At most 2 generations per turn without Diego seeing a result** —
then show what you have, problems included.

### 3. Letter it → `.xcf` (free, no credits)

1. `venv/bin/python generate-comic-page/scripts/make_layout.py -p <project>
   <issue> <page>` finds the empty balloons of the latest attempt, writes a
   numbered overlay `layout/page_NN.balloons.jpg` and a draft
   `layout/page_NN.layout.json`: one text layer per balloon (CCWildWords
   Regular, black, centred, auto-sized to ONE uniform size per page) filled
   with the `dialogue_exact` lines in script order.
2. **Read the overlay** and fix the draft JSON — the pairing is only
   "reading order vs script order":
   - move each text to the balloon of its speaker (swap the `text` values;
     never retype a line — copy it from the job so accents stay exact);
   - delete layers that sit on false positives (white clothes, clouds,
     flashes);
   - add a layer by hand for a balloon the detector missed (it leaks into
     the gutter, or is not white): `box` = `[x, y, w, h]` in pixels of the
     art (`make_layout.py` prints the overlay → art pixel ratio);
   - a line with no balloon at all → the art fails QC: edit/reroll asking
     for that balloon;
   - optional per-layer style: `"font": "CCWildWords Bold Italic"` for
     shouts, `"CCWildWords Italic"` for thoughts/narration, `"size"` to
     override the uniform size.
   `make_layout.py` keeps an edited layout on re-runs (`--force` redrafts).
3. `venv/bin/python generate-comic-page/scripts/build_xcf.py
   <layout.json>` (~15 s, headless GIMP) writes `out/xcf/<issue>/page_NN.xcf`
   + `layout/page_NN_preview.jpg`, reloads the XCF and fails unless every
   text is still a native text layer with the exact string. Notes to act
   on: font not installed, "may OVERFLOW".
4. Read the preview: every line inside its balloon, nothing clipped,
   right speaker, even size. Fix the JSON and rebuild until it is.

### 4. Show Diego, then stop

Send him the lettered preview and the `.xcf` path (SendUserFile when the
harness has it, else the paths)
with a short honest QC report: what you verified, and every doubt or flaw
you noticed — he decides, don't hide problems to get an approval. State the
tries/credits used so far. Then end the turn and wait.

### 5. His answer

- **OK** → `page_state.py -p <project> approve <issue> <page>` (copies the
  lettered preview to `approved/page_NN.jpg` and records the `.xcf`; it
  refuses if the XCF is missing or older than the latest art). If he later
  restyles the XCF in GIMP he re-exports over that JPG. Then
  **ask** whether to generate the next page, naming it ("Página 12 —
  <title> — gero agora?"). Generate it only after a yes. When the issue is
  complete, offer `assemble_cbz.py`.
- **Specific changes** → pick the cheapest tool that can work:
  - *Edit* (art is good, one thing is wrong):
    `gen_page.py ... --edit-from <the page> --instruction "<ONE change>" [--model nano_banana_pro]`.
    ONE change per edit, anchored by content not position ("the panel with
    the 'Mentira!' balloon"), with DO-NOT-CHANGE guards for anything a
    previous edit damaged. Several requested changes = several edits in
    sequence, or a reroll.
  - *Reroll* (layout/composition/many problems): `gen_page.py ... --fix
    "<what was wrong and what it must be>"`.
  - Character design complaints → make the fix durable: correct that
    character's `description` in `charmap.json` first, then reroll.
  - Text-only requests (wording, size, font, position) → edit the layout
    JSON and rebuild the XCF: free, no generation.
  After new art: re-run the self-QC on the WHOLE page (edits break other
  things), then `make_layout.py --force` + `build_xcf.py` again, show the
  result, stop again.
- **More examples** (folder or images) → `import_refs.py`, look at them,
  update `charmap.json` (new sheets + sharper description), regenerate the
  page with them attached, show, stop.
- **Skip / come back later** → `page_state.py ... set <issue> <page>
  --status needs_review --note "<why>"`.

Record anything worth remembering with `page_state.py ... set --note`.
Rules that apply to the whole comic (a continuity fact, a recurring failure
and its fix) go in the project's `PROJECT.md`.

## Panel mode (`"generation": "panels"` in project.json)

The page is not drawn in one generation: **every panel is its own
generation** and the page is assembled by code. And **comic pages never go
to the image model as images** — only model sheets and scenarios are
attached; comic pages travel as the WORDS that `describe-pages` wrote for
them:

| Goes in as | What |
| --- | --- |
| image | model sheets (`charmap.json`), scenario images (`scenemap.json`, `refs/scenarios/`) |
| words | panel prompt with EMPTY balloons, character design lock, scenario notes, descriptions of the original manga pages picked as staging refs (`source_refs`), descriptions of this comic's earlier panels + previous approved page (continuity, described on demand by describe-pages into `~/Downloads/<project> descriptions/`) |

project.json: `"generation": "panels"`, `"description_sets": {"source":
"<describe-pages set of the ORIGINAL manga>"}`, `"page_size"`,
`"page_margin"`, `"panel_gutter"`, `"panel_border"`, `"panel_resolution"`
(`1k` = 0.25 credit per panel with `gpt_image_2_5` low), and `"panel_style"`
(the base style written for ONE panel — replaces the issue preamble, which
usually talks about whole pages).

**Scenarios come from the original manga, found through its descriptions**
(so backgrounds stay consistent with the original). Per location:
1. `venv/bin/python find-pages/scripts/find_pages.py <source set> <place words> --field setting --type story --show`
   (also try objects: `tree`, `building`, `computer`…).
2. LOOK at the best hits (a contact sheet is quickest); pick pages where the
   place fills the frame with little lettering and few figures.
3. `import_refs.py -p P --scenario <page> [--crop x,y,w,h]` — crop to the
   panel that shows the place.
4. Add a `scenemap.json` entry: `keywords` (how the scripts name the place,
   matched like charmap keywords), `images`, `description` (what you SEE:
   terrain, vegetation, architecture, how it is inked — plus what the script
   adds, e.g. "pixel-edged leaves"), `source_pages`.

**The page loop in panel mode** (replaces step 1 of the page loop; steps
2-5 stay):
1. `plan_panels.py -p P <issue> <page>` drafts
   `work/<issue>/panels/page_NN/plan.json`: one panel per STORY BEATS bullet,
   each quoted line replaced by "an EMPTY balloon sized for about N words",
   that panel's exact `dialogue`, its `scenarios`.
2. Edit the plan like a manga artist: restage the boxes (wide establishing
   shot, big reveal, small reaction), rewrite each `prompt` as rich prose
   (camera, pose, expression, where the balloon goes), and fill
   `source_refs` with 0-2 original pages per panel found with find-pages
   (`"lying on the ground" boy`, `"looking up" sky`…). The 4B describer is
   noisy — skip a hit whose description is mostly about something else.
   `gen_panel.py ... --dry-run` shows the exact prompt; `--cost` the price.
3. `gen_panel.py -p P <issue> <page> <panel>` for each panel in order (each
   one describes the panels before it for continuity — needs Ollama). Read
   every panel right after it lands. An objective failure (duplicated or
   missing character, wrong balloon count, letters) gets ONE `--fix` reroll
   (or `--edit-from <panel png> --instruction "..."`) before showing; set
   `"chosen"` in the plan to pick an older try.
4. `assemble_page.py -p P <issue> <page>` composes the chosen tries into
   `gen/page_NN_tryK.png` (+ `.panels.json` sidecar). Adjust a panel's `box`
   or `focus` (0..1, which part survives the crop) when a balloon or face is
   cut, and re-assemble (free). `--preview-only` for a quick look.
5. `make_layout.py` pairs balloons with lines PER PANEL from the sidecar;
   then `build_xcf.py` as usual.

Limits in panel mode: all panels of ONE page per turn (not one generation),
max one fix per panel before Diego sees the page; the page is still
approved as a whole with `page_state.py approve`.

Field notes (first test, Ryo Saga 1 p5, 2026-09-28): 6 panels + 1 fix = 1.75
credits; characters and forest stayed on-model; a continuity description of
the previous panel made the model draw that panel's Agumon AGAIN (fixed with
an explicit "exactly ONE Agumon" `--fix`); generated balloons come out small
for long lines — fit the text with per-layer `size` and boxes that follow the
oval (the auto-fit can overflow narrow boxes); text printed on a model sheet
(the "RYO" strap) comes back as garbled letters.

## Cover and editorial

Both are pages of the issue (`kind` in the job: `cover` / `editorial`, from
`project.json` `"page_kinds"` or the page title). Same loop, same `.xcf`
deliverable, but their type is DESIGNED over the art instead of dropped
into balloons, so they are reviewed in **two stages**:

**Stage "art"** — `gen_page.py` generates the cover as full-bleed textless
art with calm areas reserved for logo, title burst and small print, and the
editorial as the page's visual frame (paper, header band, framed
illustration boxes) with empty text areas. Self-QC: ZERO letters, characters
on-model, room left for the text. Show Diego the ART; changes/examples work
as in step 5. When he OKs it: `page_state.py ... set <issue> <page> --stage
layout`. An editorial that needs no art can skip this stage and use a flat
paper `"background"` in the layout (0 credits).

**Stage "layout"** — free, iterate as much as needed:
1. `make_layout.py -p <project> <issue> <page>` drafts the layout with the
   page's exact text lines stacked in placeholder boxes (no balloon
   detection here). Cover: logo, issue/number line, price, title burst,
   credits. Editorial: header, body (merge the body lines into ONE layer,
   keeping paragraph breaks as `\n`), captions, footer.
2. Position and style every layer (format: `build_xcf.py --help`): `box`
   in pixels (or set `"units": "fraction"` and use 0..1), `size: null` to
   auto-fit, `color`, `align`, `valign` (`"top"` for body copy), `outline`
   for type over art, optional `image` layers (a real logo file, fan art).
   Fonts: exact names from `build_xcf.py --list-fonts [filter]` (e.g.
   `Impact Regular`, `CCShoutOut Heavy`, `FreeMono Bold`); display type is
   the one place where CCWildWords is not the default — follow the
   project's `PROJECT.md` when it names fonts.
3. `build_xcf.py <layout.json>`, read the preview: legible, nothing over
   faces, nothing clipped, hierarchy right. Rebuild until it is, then show
   Diego the preview + `.xcf` path. Text layers are never rotated (a
   rotated text layer stops being cleanly editable) — tell him if a tilted
   burst would look better so he can rotate it in GIMP.
4. His OK → `page_state.py ... approve <issue> <page>`, then ask about the
   next page.

## Field-tested lessons

- **Group images need itemized descriptions.** A sheet showing a team is
  not enough — the model draws 2-3 of 5. Describe each member (in the
  charmap `description` and, when it still fails, in `--fix`).
- **Never put character names as list labels in a panel plan** — they get
  printed on the page as floating text. Describe panels in prose.
- **Whack-a-mole is real:** a full reroll that fixes one thing often breaks
  another (a character vanishes, an outfit changes). After 2 full
  generations of the same page prefer EDIT mode.
- **Edits: ONE change per call.** Two changes in one edit caused a full
  relayout. Anchor by content, add DO-NOT-CHANGE guards.
- **Edits can silently no-op** (image comes back identical). Unchanged
  after 1 retry → full reroll with the specific error called out.
- **Gibberish in a balloon** is the most common textless failure: one edit
  ("make the inside of that balloon plain empty white") usually clears it.
- **Balloons that overlap the gutter** merge with the page white and the
  detector misses them — box them by hand in the layout, no reroll needed.
- **Style-label leak** ("NAM STYLE" printed on the page): set
  `"style_label"` in `project.json` — `gen_page.py` then replaces the label
  in the prompt. If it still leaks, crop/edit it out rather than reroll.
- **Continuity comes from approved pages**, not from sheets: helmet on/off,
  damage, eye colours, how many aliens. Attach the relevant approved page
  and still re-verify every character in the result.
- **NSFW filter is combo-sensitive** (some page + reference combinations,
  not content alone): swap the offending reference or drop image refs for
  that attempt instead of rewording; avoid "corpse"/"husk" wording.

## Legacy: AI-rendered text

Projects finished before this workflow have `"lettering": "ai"` in their
`project.json` (the model rendered the dialogue into the image; no XCF for
story pages). Leave them as they are. New projects never use it — the
template says `"lettering": "xcf"`. `make_lettering_guide.py -p <project>
<issue>` still writes `out/lettering_<issue>.md` (every line per page, with
its context) for anyone lettering by hand.

## Scripts

| Script | Use |
| --- | --- |
| `new_project.py <name> [--title]` | start `~/Downloads/<name>/` from `_template/` |
| `import_refs.py -p P <folder/images> [--style \| --scenario [--crop x,y,w,h]]` | import examples into `refs/` (scenarios: pages of the original manga), list sheets missing from `charmap.json` |
| `split_scripts.py -p P [issues]` | issue scripts → `jobs/<issue>/page_NN.json` + `state.json` (keeps recorded states) |
| `gen_page.py -p P <issue> <page> [...]` | ONE generation / edit / `--cost` / `--dry-run` |
| `page_state.py -p P next\|show\|set\|approve ...` | what's next, notes, stage, approval |
| `plan_panels.py -p P <issue> <page> [--force]` | panel mode: draft the page's panel plan (boxes, prompts with empty balloons, lines, scenarios) |
| `gen_panel.py -p P <issue> <page> <panel> [...]` | panel mode: ONE panel generation — sheets + scenarios as images, source pages and earlier panels as describe-pages text |
| `assemble_page.py -p P <issue> <page> [--preview-only]` | panel mode: compose the chosen panel tries into the page art (free) |
| `make_layout.py -p P <issue> <page> [--force]` | detect the empty balloons (numbered overlay) and draft the layout JSON with the exact script lines |
| `build_xcf.py <layout.json>` / `--list-fonts` | layout → `.xcf` with native GIMP text layers + preview JPG (every page) |
| `status.py [-p P]` | approved / pending / awaiting_review per issue |
| `make_lettering_guide.py -p P <issue>` | per-page list of every line, for hand lettering |
| `assemble_cbz.py -p P <issue>` | approved pages → `out/*.cbz` |
