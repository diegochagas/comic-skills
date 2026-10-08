---
name: modernize-photos
description: Make an old photo look as if it had been taken today with a modern iPhone - sharp, clean, HDR, true-to-life colours, no grain, fading, colour cast or damage (black-and-white comes back in colour) - same people, same moment, same framing. Uses Higgsfield (Nano Banana Pro, online, 2 credits/photo) when its CLI is logged in and has credits, otherwise a local ComfyUI model (FLUX.2 klein / Qwen-Image-Edit, free, offline). Takes an image, several images or a folder. Results in ~/Downloads/photo-modernize/<folder name>/; sources never touched; the agent QCs every result with Diego. Use when Diego asks to "modernize this photo", "make it look like an iPhone photo", "make this old photo look new / recent / modern", "deixar a foto com cara de iPhone", "modernizar a foto", "como se fosse tirada hoje".
---

# modernize-photos — an old photo, shot again on an iPhone

You are the orchestrator and the visual QC reviewer. The model redraws the
whole photo in a modern-phone look; you make sure it is still the same
people and the same moment before it counts as done.

Script: `modernize-photos/scripts/modernize.py`, run with the repo venv
(`<repo>/venv/bin/python`, created by `<repo>/setup.sh`). It reuses
`restore-photos/scripts/` (crop, Higgsfield helpers, ComfyUI client, QC
sheets), so both skill folders must stay side by side in the repo.

**This is not a restoration.** `restore-photos` keeps every undamaged pixel
of the scan; here the whole picture is the model's - grain, blur and the
old colours are exactly what gets replaced. So faces *can* change and the
result is a new image of the old moment, not a record of it. Say so when
Diego might mix the two up (e.g. before sending anything to Immich).

## Arguments

`/modernize-photos <image-or-folder>... [what to do]`

- `image-or-folder`: one image, several images, or a folder
  (`--recursive` for its sub-folders). If nothing is given, ask.
- A damaged scan works directly (the prompt removes damage too), but a
  photo already restored by `restore-photos` (its `restored/` or Immich's
  final version) gives the model cleaner faces to keep - prefer it when it
  exists.

## Backend choice

| `--backend` | What happens |
| --- | --- |
| `auto` (default) | Higgsfield when the `higgsfield` CLI is installed, logged in and has credits for the whole run; else local klein. A photo Higgsfield refuses (NSFW filter: children in swimwear/bath) or fails on is redone locally in the same run and listed at the end |
| `higgsfield` | Higgsfield only, no fallback (a refusal is reported as FAILED) |
| `klein` / `qwen` | local ComfyUI only, free. `--local qwen` picks the fallback model for `auto` |

Higgsfield uploads the photos and spends credits (2/photo at 2k, 4 at
`--resolution 4k`): for more than ~10 photos run `--cost` first and tell
Diego the total and the balance before generating. Never try to get a
refused photo past the NSFW filter - it goes local, that is all.

Measured on a 1987 print (1 MP scan): Higgsfield kept both faces, the car
and the room, and came back at 1696×2447 (37 s). klein made the scan
modern too, but invented the background behind the door (a lit building, a
person behind the gate), changed the faces slightly and stays at the
scan's ~1 MP (2 min with a cold ComfyUI). So: Higgsfield for the photos
that matter, local when it is unavailable or out of credits - and when a
local result invented things, say which.

## Diego says → you run

| Diego says | Run |
| --- | --- |
| "modernize this photo / folder", "make it look like an iPhone photo" | `modernize.py <path>...` |
| "how much will it cost" / a big folder | `modernize.py <path> --cost` |
| "don't use credits", "do it locally", "offline" | `--backend klein` (or `qwen`, slower, changes faces more) |
| "only Higgsfield" | `--backend higgsfield` |
| "keep it black and white" | `--keep-bw` (default: a B&W or sepia photo comes back in colour) |
| "sharper", "bigger" | `--resolution 4k` (Higgsfield, 4 credits) |
| "the face changed" | `--ref <other photo of the same person>` (repeatable; face crops work best - a whole photo as reference can make Nano Banana Pro return that photo), then `--force` |
| "try another version" | `--force` (Higgsfield gives a new one each call; local: `--seed <other>`) |
| "also make it daylight / remove the person on the left" | `--extra "..."` (appended to the prompt) - but warn: that is editing the scene, not modernizing it |
| "keep the white border", "don't straighten" | `--no-crop` |

## Where results go

Root `~/Downloads/photo-modernize/` (`$PHOTO_MODERNIZE_OUT` replaces it,
`--output` names any directory):

| Input | Results |
| --- | --- |
| a folder `<name>/` | `<root>/<name>/modernized/`, `<root>/<name>/work/`, `<root>/<name>/sheets/qc_NN.jpg` |
| loose images | `<root>/modernized/`, `<root>/work/` |

`modernized/<name>.jpg` is the result (q95, the scan's EXIF copied - the
camera tags stay the original's, nothing claims it was shot on an iPhone);
`work/` holds `<name>.raw.png`, `<name>.compare.jpg` (scan | modern) and
`<name>.json` (backend, model, seed, prompt, size). Existing results are
skipped, so a folder run resumes; `--force` redoes.

## Workflow

1. Local backend in play (`--backend klein|qwen`, or `auto` without
   Higgsfield): `restore-photos/scripts/comfy_client.py --check` first; if
   ComfyUI is down, tell Diego the command that starts it
   (`systemctl --user start comfyui`).
2. Run the FIRST photo alone, open `work/<name>.compare.jpg` and check:
   (a) every face is the same person (same features, age, expression),
   (b) nobody and nothing was added or removed, clothes and objects are the
   same era, (c) it really looks like a phone photo now (sharp, clean, no
   cast). A colourised B&W: are skin tones and clothes plausible?
3. Run the rest (Higgsfield: 4 in parallel, ~40 s each; local: one at a
   time, 30-120 s). Open every `sheets/qc_NN.jpg` and judge each pair the
   same way; redo the doubtful ones (`--force`, `--ref`, or the other
   backend with `--output <root>/<name>/<backend>` to compare).
4. **Report:** which backend made each photo (the run log and
   `work/<name>.json` say it; list the ones that fell back to local and
   why), which faces you are not sure about, which photos had things
   invented, and where the results are. Show the compare images of the
   photos you are least sure about.
