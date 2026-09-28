---
name: psd-sync
description: Given folder1 (the new PSDs) and folder2 (the older folder being updated), compare their same-named PSDs by art layer and replace folder2's copy with folder1's, but only for pages whose artwork was not cropped - the old copy goes to the trash and the new file takes its place, while every page whose art was cut (left, right, top or bottom, with or without a resize afterwards) is left untouched in both folders for a human to look at. Use when Diego gives two folders of PSDs and asks to "compare these PSDs", "check if they were cut/cropped", "replace the ones that are the same", "compara os PSDs", "move os que não foram cortados", or wants a newer batch of PSDs moved over an older working folder without overwriting a page whose framing changed.
---

# psd-sync — replace PSDs only where the art was not cut

One script, `psd-sync/scripts/sync_psds.py`, run with the repo venv
(`<repo>/venv/bin/python`; needs psd-tools, OpenCV, numpy — all in the shared
venv). `<repo>` is the comic-skills checkout.

```bash
<repo>/venv/bin/python psd-sync/scripts/sync_psds.py "<folder1>" "<folder2>" [--apply]
```

- `folder1` — the new PSDs. Files **move out of here** when they are applied.
- `folder2` — the folder being updated. Its copy of an applied page goes to
  the trash and the folder-1 file takes its place, same name.

Only names present in both folders are compared; `.psd` only, no recursion.
This is the one skill in the repo that moves and trashes files instead of
writing to `~/Downloads` — so it is the one skill that Diego approves before
anything happens.

## How to run it

0. **Need both folder paths before doing anything else.** If Diego invokes
   this (including bare `/psd-sync`) without giving folder1 and folder2,
   ask for both paths and wait for his reply — never guess a folder, and
   never run the dry run on partial input.
1. **Dry run first, always.** Without `--apply` nothing is touched; the script
   prints one line per file and the counts.
2. **Show Diego the result** — how many would be replaced, and the name plus
   the cut sides of every page that would be kept. Cut pages are the whole
   point of the check: they mean the two files frame the art differently and
   somebody has to decide.
3. **Only then re-run the exact same command with `--apply`.** Never run
   `--apply` on the first pass, never in the same turn as the dry run, and
   never for a subset Diego did not ask for.

Deleting is always `gio trash` / `trash-put`, never `rm`, so a wrong call is
undone from the desktop trash. The script refuses to run if folder1 and
folder2 are the same folder, and it verifies the size of every moved file.

After `--apply` (full runs only — this is skipped when `--only` was used,
since a partial run never looks at the rest of folder1), any `.psd` still
physically left in folder1 — kept pages (`cut`/`different`/`no_counterpart`/
`error`) as well as any page whose apply failed — is moved to `~/Downloads`
(`COMIC_OUTPUT_DIR` overrides the root) instead of being left behind. If
folder1 is empty after that, it gets trashed too: its parent as well, when
folder1 was that parent's only entry (the common `Name/Name/` download-
extraction wrapper) — but never a parent that has other content in it.

## What gets compared

The **art layer**: the bottom-most pixel layer that covers the canvas (in
Diego's PSDs that is `Original`, the scan under the lettering), found
automatically inside groups too; `--layer "<name>"` picks another one, and a
PSD with no pixel layer falls back to the flattened page. Text layers, the
lettering and the retouching above the art are ignored on purpose: a page
whose balloons were cleaned or re-lettered is still the same page.

| Status | Meaning | Action |
| --- | --- | --- |
| `identical` | same size, same pixels | replace |
| `same_framing` | same artwork, same framing — only edited and/or resized | replace |
| `cut` | one side (or more) of the art is missing in one of the two | keep both |
| `different` | the art does not line up well enough to judge | keep both |
| `no_counterpart` | that name is not in folder2 | keep both |
| `error` | the PSD could not be read | keep both |

`cut` says which folder lost the art and how much, in that page's own pixels:
`folder1 art is cut: missing top 120px, right 60px`. Crops are found down to
half a side, with or without a resize afterwards; a crop that was then
*stretched* out of proportion lands in `different` instead — still kept, still
flagged, just without the margins.

`different` also catches a page that was heavily repainted over the art layer
(roughly a quarter of the page or more). That is deliberate: when the script
cannot tell a crop from a repaint it keeps both files and lets Diego look.

## Flags

| Flag | Use |
| --- | --- |
| `--apply` | do the moves (trash folder2's copy, move folder1's file in) |
| `--only NAME...` | limit to some files: `--only 089 090.psd` |
| `--layer "NAME"` | compare a named layer instead of the auto-detected art layer |
| `--json [PATH]` | write the full report as JSON (default `~/Downloads/<folder1 name>-psd-sync.json`, `COMIC_OUTPUT_DIR` replaces the root) |

## Example

```bash
cd <repo>
venv/bin/python psd-sync/scripts/sync_psds.py \
  "/home/diego/Downloads/Golden Age Cap05 PSDs/Golden Age Cap05 PSDs" \
  "/mnt/data/winboat/shared/psd"            # dry run, show Diego
venv/bin/python psd-sync/scripts/sync_psds.py \
  "/home/diego/Downloads/Golden Age Cap05 PSDs/Golden Age Cap05 PSDs" \
  "/mnt/data/winboat/shared/psd" --apply    # only after he says yes
```

## Report

Counts first (`N replaced, M kept`), then every kept page with its reason —
cut sides in pixels, unreadable file, or missing counterpart — and where the
trashed copies went (desktop trash). If `--apply` fails on a file, that pair
is left exactly as it was and the failure is reported per file; the rest of
the batch still runs. Last, on a full (non `--only`) `--apply` run: which
leftover files moved to `~/Downloads`, and whether folder1 (or its wrapper)
was trashed or — if something is still inside it — why not.
