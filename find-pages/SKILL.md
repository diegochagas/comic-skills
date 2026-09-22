---
name: find-pages
description: Search a set of page descriptions written by describe-pages and return the image paths of the pages that match a search term - "computer", "motorcycle", "beach", "boy with goggles" - ranked, with the matching text, after checking the top hits against the actual images. Takes the set name (a unique part of it is enough) and one or more terms. Use when Diego asks "which pages have a computer?", "find pages with a dragon in <set/volume>", "procura páginas com computador", "list the covers", "show me examples of X in that manga", or wants image paths of pages showing something, once the folder was described with describe-pages (if it was not, run that first).
---

# find-pages — image paths of the pages that show something

One script, `find-pages/scripts/find_pages.py`, run with the repo venv
(`<repo>/venv/bin/python`; stdlib only). `<repo>` is the comic-skills
checkout. It reads `~/Downloads/<set> descriptions/pages.jsonl`
(`COMIC_OUTPUT_DIR` replaces `~/Downloads`; a folder path works too), writes
nothing, and prints paths of the ORIGINAL files in the folder describe-pages
was pointed at.

```bash
<repo>/venv/bin/python find-pages/scripts/find_pages.py <set> <term> [<term>...] [--any] [--type T] [--show] [--json] [--limit N]
```

## Arguments

`/find-pages <set> <what to look for>`

- `set`: the name describe-pages printed (`1998-2003-v-tamer-01-japanese`);
  a unique prefix or substring works (`v-tamer-01`). `--list` shows the
  sets; if the one Diego means does not exist, run describe-pages on the
  folder first (ask which folder if unclear) — do not search a set that
  describes another folder.
- `terms`: whole words, case-insensitive, singular or plural (`computer`
  finds `computers`); quote a phrase (`"boy with goggles"`). All terms must
  match; `--any` makes it OR.

| Flag | Use |
| --- | --- |
| `--any` | any term matches instead of all |
| `--type T` | only `story`, `cover`, `bio`, `insert`, `ad`, `contents`, `title`, `sketch`, `blank`, `other` (repeatable) |
| `--field F` | search only `objects`, `characters`, `summary`, `setting` or `text_summary` (repeatable) |
| `--show` | print the matching text under each path (use it — it is what tells you why a page matched) |
| `--json` | full records with score and matches |
| `--limit N` | best N only |
| `--types` | page_type counts of the set |

Ranking: a hit in `objects` counts 3, `characters` 2, the text fields 1,
plus 10 per matched term; ties by path.

## Workflow

1. Run with `--show`. Zero hits → try synonyms with `--any` before saying
   there are none (`computer laptop monitor screen keyboard PC`; `car
   vehicle truck`; `beach sea ocean shore`). Too many → add a term, restrict
   with `--type story` or `--field objects`.
2. **Verify before reporting.** The descriptions come from a small local
   vision model that sometimes invents an item. Read the top hits (up to ~6
   images; more only if Diego wants an exhaustive list) and keep only the
   pages where the thing is really drawn. Say which ones you checked.
3. Report the verified paths as a list (full paths, one per line, in a code
   block so they can be copied), one line each on what is shown, then the
   count of unverified further hits and how to get them (`--limit 0`, the
   default, prints all). Offer to copy the pages somewhere (`~/Downloads/`)
   only if asked.

## Example

```bash
cd <repo>
venv/bin/python find-pages/scripts/find_pages.py v-tamer-01-japanese computer laptop monitor --any --show
venv/bin/python find-pages/scripts/find_pages.py v-tamer-01-japanese --type cover
venv/bin/python find-pages/scripts/find_pages.py v-tamer-01-japanese "boy with goggles" dinosaur --field characters
```
