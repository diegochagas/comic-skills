---
name: describe-pages
description: Describe every page image under a folder (walked recursively - a whole manga volume, a series, a folder of scans or inserts) with a local vision model in Ollama, free and offline, and save the descriptions as a named set in ~/Downloads/<name> descriptions/ that find-pages can search afterwards ("which pages show a computer?"). Each page gets a page_type, a summary, the list of objects drawn on it, its characters, setting and what its text is about. Resumes where it stopped. Use when Diego gives a folder of pages and asks to "describe the pages", "index this folder", "generate descriptions of all pages", "descreve as páginas", "cataloga esse volume", or wants to be able to search a comic by what is drawn on its pages.
---

# describe-pages — descriptions of every page, for searching later

One script, `describe-pages/scripts/describe_pages.py`, run with the repo
venv (`<repo>/venv/bin/python`; needs only Pillow). `<repo>` is the
comic-skills checkout. The model is **Qwen3-VL 4B in Ollama** (`qwen3-vl:4b`,
installed by `describe-pages/setup.sh`), running on this machine: no API,
no credits, nothing leaves the computer. There is NO LLM API usage.

```bash
<repo>/venv/bin/python describe-pages/scripts/describe_pages.py "<folder>" [--name NAME]
```

## Arguments

`/describe-pages <folder> [name]`

- `folder`: the root to describe. Every `.jpg/.jpeg/.png/.webp/.gif/.bmp/.tif`
  under it, at any depth, is a page. If missing, ask.
- `name` (optional) → `--name`. Default: a slug of the last two path parts,
  e.g. `.../(1998-2003) V-Tamer 01/Japanese` → `1998-2003-v-tamer-01-japanese`.
  Tell Diego the name at the end: it is what find-pages takes.

| Flag | Use |
| --- | --- |
| `--name NAME` | name of the set (slugified) |
| `--model MODEL` | another Ollama vision model (`$DESCRIBE_MODEL`; default: best installed of `qwen3-vl:8b`, `qwen3-vl:4b`, `gemma3:*`…) |
| `--limit N` | describe only N new pages, then stop — for a test run |
| `--only STR...` | only pages whose relative path contains one of the strings |
| `--force` | re-describe pages already in the set (after a prompt or model change) |
| `--max-side PX` | downscale sent to the model (default 1024; the files are never touched) |
| `--output DIR` | write the set into this folder instead |
| `--list` | list the existing sets |

## Where results go

`~/Downloads/<name> descriptions/` (`COMIC_OUTPUT_DIR` replaces
`~/Downloads`; `--output` takes any folder), like every other skill — never
the repo, never the source folder. find-pages finds a set by that name. Inside:

- `pages.jsonl` — one line per page: `path` (absolute), `rel`, `folder`,
  `size`, `description` = `{page_type, summary, objects[], characters[],
  setting, text_summary}`, `seconds`, `tokens`; failed pages carry `error`.
- `index.json` — source folder, model, image count, described/failed counts,
  page_type counts, timestamps.

The source folder is never written to. A set is bound to its source folder:
pointing the same name at another folder is refused (use another name).

## Workflow

1. Check the model is there: `ollama list | grep qwen3-vl` (if not, run
   `describe-pages/setup.sh`; Ollama itself must be running).
2. **Test run first** on a big folder: `--limit 5`, then Read 2 of the
   described pages next to their JSON lines and check the descriptions are
   about the right page (object list matches what is drawn, page_type is
   right). ~5 s per page on the RTX 3050; the first page includes the model
   load (~30 s).
3. **Full run** — the same command without `--limit`. Above ~50 pages run it
   in the background (a 1500-page volume is ~2 hours) and report the ETA the
   script prints. It appends as it goes, so an interrupted run resumes with
   the same command; pages that errored are retried too.
4. Report: set name, where it is, pages described / failed, page_type
   counts (`index.json`), and the find-pages command to search it.

## What the descriptions are good for (and not)

- Concrete nouns drawn on the page (computer, motorcycle, cactus, goggles),
  what characters look like and do, indoors/outdoors, what kind of page it is
  (story, cover, bio, insert, ad, contents). That is what find-pages searches.
- The model does **not** know character names and is told not to guess
  them; it reads Japanese/English text only enough to say what it is about.
  It can still hallucinate an item now and then: find-pages results are
  always verified by looking at the images before being reported.
- A 4B model at 1024 px misses tiny background details; `--model qwen3-vl:8b`
  (needs ~7 GB VRAM, slower) or `--max-side 1536` when precision matters more
  than time. Changing either means `--force` to redo the set.
