---
name: restore-photos
description: Restore scanned photo prints - water/emulsion damage, stains, scratches, cut corners repainted by a local image-edit model (FLUX.2 klein or Qwen-Image-Edit in ComfyUI, free, offline) but ONLY inside the damaged areas, every undamaged pixel and every face kept from the original; then automatic colour/contrast fix for faded or colour-cast prints; re-save of broken JPEGs. Takes an image, several images or a folder of scans (optionally recursive). Results in ~/Downloads/photo-restore/<folder name>/, with QC contact sheets; sources never touched; the agent QCs every result with Diego. Use when Diego asks to "restore this photo / these photos / this folder of scans", "fix the damaged prints", "restaurar as fotos", "remove the white blotches / water damage", "fix the colours of the old photos", or "this file won't open properly".
---

# restore-photos — repair scanned prints, keep what is real

You are the orchestrator and the visual QC reviewer. The model repaints, the
scripts make sure it only repaints damage, you judge every result before it
counts as done. No LLM API usage; the only AI is local ComfyUI.

Scripts in `restore-photos/scripts/`, run with the repo venv
(`<repo>/venv/bin/python`, created by `<repo>/setup.sh`). `<repo>` is the
comic-skills checkout. All paths below are relative to it.

| Script | Does |
| --- | --- |
| `restore.py <image-or-folder>... [--mode full\|color] [--recursive]` | the restoration (model pass → damage mask → composite, original colours kept; `--fix-color` adds the colour fix; `--mode color` is the colour fix alone). A folder run ends with QC sheets in `<out>/sheets/` |
| `contact_sheet.py <originals> <restored>` | before/after sheets from two folders (when the results were moved around) |
| `fix_broken.py <jpg>... [--crop-strip]` | re-saves JPEGs with data-stream errors / truncated tails, EXIF kept |
| `inpaint.py <image> <mask> <out>` | native-resolution repaint of masked regions (big scans; `restore.py --hires` calls it) |
| `fix_color.py <image> <out>` | the colour fix alone, with all its knobs |
| `comfy_client.py --check` | is ComfyUI up, which backend has its models |
| `compare_server.py [--results DIR] [--originals DIR]...` | the review page for Diego: original \| restored side by side (or a slider), a pick per photo (original / restored / redo) and a note, saved to `<results>/preferences.json` |
| `higgsfield_restore.py <image-or-folder>... --output <results>/Higgsfield` | the same scans restored online by Higgsfield (Nano Banana Pro, 2 credits/photo at 2k, `--cost` estimates) - **paid and uploads the photos: only when Diego asks for it**. The whole picture is the model's (faces can change); the review page shows it as a third option next to every photo |
| `finalize.py <list.json> --output DIR` | the chosen versions with the originals' metadata (all tags + Immich sidecar, UTC offset) plus what the file names say: title/description, people and places from `~/.config/photo-restore/names.json` (outside the repo), "Restaurada" tag |
| `immich_replace.py <DIR> [--dry-run] [--only NAME]` | puts them back in Immich **through its API**: upload, same albums/favourite/rating, original to Immich's trash (30 days), log for resume/undo; `~/.config/photo-restore/immich.env` holds URL + API key. Always `--dry-run`, then one photo, then the rest |
| `crop.py`, `faces.py` | used by `restore.py`: straighten + cut white borders; find faces and keep them the scan's |

## Arguments

`/restore-photos <image-or-folder>... [what to do]`

- `image-or-folder`: one image, several images, or a folder of scans
  (`--recursive` for its sub-folders). If nothing is given, ask.
- what to do (optional): nothing = repair (mode full); "fix the colours" /
  "faded" = `--mode color`; "repair and fix the colours" = `--fix-color`.

## Where results go

Root `~/Downloads/photo-restore/` (`$PHOTO_RESTORE_OUT` replaces it,
`--output` names any directory):

| Input | Results |
| --- | --- |
| a folder `<name>/` | `<root>/<name>/restored/`, `<root>/<name>/work/`, `<root>/<name>/sheets/qc_NN.jpg` |
| loose images | `<root>/restored/`, `<root>/work/` |

`restored/<name>.jpg` is the result (q95, EXIF copied from the original);
`work/` holds `<name>.raw.png` (model output), `<name>.mask.png`,
`<name>.regions.jpg` (numbered regions on the original) and
`<name>.compare.jpg` (original | result); `sheets/` stacks the compare
images 4 per sheet after a folder run (`--no-sheets` skips it). Sources are
never written to. Existing results are
skipped, so a folder run resumes; `--force` redoes.

## Arguments Diego gives → what you run

| Diego says | Run |
| --- | --- |
| "restore this photo / these photos / this folder" | `restore.py <path>...` (mode full; `--recursive` when he says "including sub-folders") |
| "fix the colours", "the faded ones" | `restore.py <path> --mode color` (no model, ~1 s/photo) |
| "the sepia one should stay sepia", "don't neutralise the tone" | add `--wb 0` |
| "too strong / too contrasty" | `--contrast 0.6` (0 = off), `--strength` is on `fix_color.py` only |
| "repair and also fix the colours", a damaged print that is also faded | `--fix-color` (off by default: a repair keeps the original colours) |
| "use Qwen", "try the other model" | `--backend qwen` (~100 s/photo, changes faces more - klein is the default for a reason) |
| "try another version" | `--seed <other>` (and `--force`) |
| a light leak / burn (orange band, pale wash) the run left alone | run it in mode full (not `--mode color`); a strong orange band gets repainted, a pale wash over the scene does not — the models read it as light, and a prompt naming it (`--prompt`) did not help; say so |
| "it's a big scan / keep it sharp" | `--hires` (only matters above ~1.3 MP) |
| "cut this side, it's too destroyed", "cut the part where he appears" | `--cut top,right,bottom,left` (fractions of the photo, after the automatic crop; look at the scan with a 10 % grid and choose) |
| "fill the white corner", damage the mask missed | `--add x,y,w,h` (pixel box on the cropped photo) |
| "too dark", "too grainy" | `--fix-color --contrast 0.6`, `--denoise 5` (film grain; 8+ smooths detail) |
| "the face is different", "use another picture as reference" | `--ref <crop of the same people from a photo of the same day>` (repeatable; `--backend qwen` takes at most 2 - put several people side by side in one image), usually with `--threshold 15`; roll `--seed` 2-3 times and let Diego pick |
| "the raw is perfect", a print ruined almost everywhere | `--whole` (the model's picture as the result, no mask; `--reuse-raw --whole` turns an existing raw into the result) |
| "keep the white border" / "don't straighten" | `--no-crop` |
| "this file won't open / is corrupted / has a grey strip at the bottom" | `fix_broken.py <files>`; `--crop-strip` for a truncated file with a flat grey strip |

## Workflow

**Before the first model run of a session:** `comfy_client.py --check`. If
klein is unavailable: ComfyUI must be running (`COMFYUI_SERVICE` in
`~/.config/photo-restore/comfyui.env` lets the scripts start the systemd
--user unit; `COMFYUI_URL` for another host) — tell Diego what is missing,
never fall back to a paid service.

**A photo or a folder (the usual case):**

1. Run the FIRST photo alone (`restore.py <that image> --output
   <root>/<folder name>` so it lands with the rest), open its
   `work/<name>.compare.jpg` and `work/<name>.regions.jpg`, and check
   (a) every damaged area is red in the regions image, (b) nothing real is
   red — a face, a hand, a pattern the model "improved" — and (c) the
   repaired areas look like the rest of the photo. Fix the mask if needed
   (step 3). This is where you learn whether the folder wants `--fix-color`
   or a different `--wb`.
2. Run the folder (15–30 s per photo with klein on 0.3–1.2 MP scans; above
   ~10 photos run it in the background). Open every `sheets/qc_NN.jpg` and
   judge each pair the same way. For `--mode color` the sheets are all there
   is to check: watch for a print whose tone was on purpose (sepia, sunset)
   and for cream/orange surfaces pushed to cyan.
3. **Fixing one result** (free, no model call — `--reuse-raw`):
   - damage left untouched: it is under the threshold (white damage on white
     clothes is the usual case) → `--threshold 12` (measured: catches flakes on
     a white robe, but then a face or two show up as regions → `--drop` them;
     8 floods the whole photo, and a heavily damaged print already masks
     60 % at the default 22, so never lower it there), or `--include N` if it
     has a yellow number, or `--add x,y,w,h` (pixel box on the original) if it
     has none;
   - something real was repainted (green region on a face/hand/pattern) →
     `--drop N`; when it is part of one big region (the model shifted the
     whole scene a little, so the region wraps around the person) →
     `--protect x,y,w,h` with a box around the person, which keeps the
     original there;
   - the model's fill is wrong (a hallucinated object, a duplicated person) →
     `--seed <other> --force` for a new raw, then QC again; or `--backend qwen`.
   - a blotch that covered people comes back with invented
     people, and a cut print (heart, arch) comes back as a full rectangle with
     invented surroundings: that is the best any tool can do, and Diego must
     be told which photos had content invented, not just repaired.
   - colour fix too far (`--mode color` / `--fix-color`): `--wb 0.15–0.3` on
     prints with a cream or orange dominant surface, `--wb 0` for sepia,
     `--wb 1` for a real magenta/green cast, `--contrast 0` for harsh grain.
   A single-image re-run rewrites only that photo's files; look at its new
   `compare.jpg` rather than rebuilding the sheets.
4. **Diego's review:** set `COMPARE_RESULTS` (the run's output folder) and
   `COMPARE_ORIGINALS` (the folder(s) with the scans, `:`-separated) in
   `~/.config/photo-restore/compare.env` for this run - the file lives
   outside the repo, never put these paths in it - and start
   `compare_server.py` (http://localhost:8790; in the Claude desktop app via
   `.claude/launch.json`, elsewhere as a background process). Diego marks
   every photo original / restored / redo with a note; read
   `<results>/preferences.json` when he says he is done, re-run the redo ones
   into `<results>/Round N/` (copy their scans into `Round N/originals/`) and
   the page shows each new round first, with his last note on the photo.
   Higgsfield second pass on photos Diego still rejects: `--strict` (repair
   only, faces kept), `--ref` with **face crops only** (a whole same-day
   photo as reference makes Nano Banana Pro return that photo instead of
   the repair), `--resolution 4k` (4 credits) when he says blurry,
   `--angle` / `--cut` / `--extra "..."` for tilted scans, destroyed edges
   and specific fixes; output to `<results>/Higgsfield 2`. Photos of
   children in swimwear or bath are refused by Higgsfield's NSFW filter -
   do not try to get around it, they stay local-only.
   `Higgsfield/` and `Higgsfield 2/` results sub-folders (COMPARE_ALT) are
   not sets of their own: the page shows each as an extra pane with its own
   pick (keys 4, 5) next to every photo with the same name; the "Slider: original | alternative"
   mode compares it directly.
   What his notes taught so far: destroyed edges are cut, not invented;
   small damaged corners are repaired, not cut; every burn must go; any face
   change shows - use references, never deliver an invented face silently.
5. **Back to the library** (only when Diego asks): build the list of his
   final picks (`restored:<set>` / `alt` / `alt:<label>` in
   preferences.json), `finalize.py`, `immich_replace.py --dry-run`, one
   photo, check it in Immich (date/timezone, album, trash), then the rest.
   Immich names the new file "<name>+1.jpg" while the original sits in the
   trash. A date the original has only in Immich (edited there) is copied
   with `PUT /api/assets {dateTimeOriginal}`. Then set `"done": true` on
   those picks (stop the page server first) - the page hides them.
6. **Report:** approved / fixed / flagged (photos you could not get right;
   say what is wrong and which ones had content invented), and where the
   results are. Show Diego the compare sheets of the photos you changed the
   most.

## What "only the damage changes" means

The model repaints the whole photo (and, left alone, would smooth faces and
move details). `restore.py` aligns the output to the original, matches its
colours to the original, and takes the model's pixels only where the two
differ strongly (the damage it repaired) — feathered 4 px. Faces, hands,
clothes outside the mask are the scan's own pixels, in the scan's own
colours - the model's pixels are colour-matched to the scan before pasting,
so a repair does not shift the tone of the photo. The colour fix (`--mode
color`, or `--fix-color` on a repair) is a plain auto-levels + half
grey-world + light CLAHE, no AI, and runs only when asked. So a restored photo
is trustworthy as a record: what is new is exactly the red area in
`regions.jpg`, and you looked at it.

Before the composite, the scan is straightened and its white borders cut
(`crop.py`), and faces are guarded (`faces.py`: found with OpenCV's Haar
cascades on the model's output, kept only with enough skin tone): inside a
face the model may only fill pixels that are clearly lighter in the scan than
in the repair - flakes of lost emulsion - so eyes, mouth and a mustache stay
the scan's. Interior regions are Poisson-blended, edge regions colour-shifted
to the scan around them, face regions pasted as they are. With `--ref` it is
the other way round: the model knows who the people are, so a repaired face
is taken whole.

Do not deliver a photo whose face was inside the mask without telling Diego
that this face was redrawn.

## Broken files

A JPEG with a data-stream error still opens but some viewers complain, and
a truncated file shows a flat grey strip where the bytes are missing.
`fix_broken.py` re-encodes them into `<root>/restored/`; `--crop-strip`
cuts the grey rows off instead of keeping them. EXIF comes over through
exiftool (`restore-photos/setup.sh` warns if it is missing).
