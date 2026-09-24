---
name: docx-odt-convert
description: Convert Word documents (.docx/.doc) to LibreOffice .odt and .odt back to .docx through a private headless LibreOffice, with the table of contents UPDATED so it lists the chapters with page numbers (a generated .docx only has an empty TOC field until someone updates it in Word) and heading styles given their outline levels (otherwise LibreOffice's TOC stays empty). Takes a file or a folder, writes a new folder in ~/Downloads, or converts in place and moves the originals to the trash when the user wants a document library switched over. Use when Diego asks to "convert this docx to odt", "salva em formato LibreOffice", "passa os documentos pra odt", "turn the books into LibreOffice files", "convert this odt to Word/docx", or when a generated .docx book must be delivered as .odt.
---

# docx-odt-convert — Word .docx ⇄ LibreOffice .odt, TOC with page numbers

One command, `docx-odt-convert/scripts/convert.py`. It needs LibreOffice
(`soffice`) and its Python binding `uno` (package `python3-uno`), which only
the system `python3` has — run it with `python3`, not the repo venv (from the
venv it re-launches itself with `/usr/bin/python3`). `<repo>` is the
comic-skills checkout; `docx-odt-convert/setup.sh` checks the requirements.

```bash
python3 <repo>/docx-odt-convert/scripts/convert.py "<file or folder>" [flags]
```

- a `.docx`/`.doc` becomes an `.odt`, an `.odt` becomes a `.docx`; a folder
  with both kinds converts every file to the other format in one run;
- results go to `~/Downloads/<source name> converted/` — the folder's name,
  or a single file's name without its extension (`COMIC_OUTPUT_DIR` replaces
  `~/Downloads`, `--output` the whole path); inputs are never touched...
- ...unless `--in-place`: each new file is written next to its source, and
  `--trash` then moves the source to the desktop trash (`gio trash`,
  recoverable). Use it only when Diego asks to switch the documents of a
  library (e.g. his Nextcloud books) to the other format;
- a private LibreOffice with its own temporary profile does the work, so an
  open LibreOffice window is not disturbed; 1-60 s per document (a 740-page
  book takes about a minute).

## What the conversion fixes

| Problem in the source | What the script does |
| --- | --- |
| `Heading N` styles without an outline level (the `docx` npm package writes them that way) — LibreOffice's table of contents finds no headings | gives `Heading 1..6` outline level 1..6 when theirs is 0 |
| the table of contents is an empty field ("update the field to see the pages") | updates every index twice after layout, so the TOC shows every entry with its page number |
| a hint paragraph such as `(No Word, clique ... "Atualizar campo" ...)` | `--drop-paragraph "<start of the paragraph>"` removes it (repeatable) |

Images, tables, styles, headers/footers and page layout are LibreOffice's own
import/export. An `.odt` stores an image once even when the document shows it
several times, so it can be smaller than the `.docx` with every image still in
place.

## Flags

| Flag | Use |
| --- | --- |
| `--to odt` / `--to docx` | only one direction when the folder has both kinds |
| `--recursive` | include subfolders, structure kept |
| `--in-place` | write next to each source instead of a new folder |
| `--trash` | with `--in-place`: move each source to the trash after its conversion succeeded |
| `--drop-paragraph PREFIX` | remove paragraphs starting with that text |
| `--overwrite` | redo files whose converted copy already exists (default: skip them) |
| `--output DIR` | output folder |

| Diego says | Run |
| --- | --- |
| "convert this docx to odt" / "pro LibreOffice" | `convert.py "<file.docx>"` |
| "convert this odt to Word" | `convert.py "<file.odt>"` |
| "switch these documents to LibreOffice format" (his library, replacing the files) | `convert.py "<folder>" --to odt --in-place --trash` |
| "and the subfolders" | add `--recursive` |

## Report

Print the output folder (or "next to the sources"), the per-file line the
script prints (pages, indexes updated, heading styles fixed, paragraphs
dropped) and any FAIL line. Exit status 0 when every file converted, 2
otherwise. When a book's table of contents matters, open one converted file
(or render it: `soffice --headless --convert-to pdf`, then `pdftotext -f 2 -l 3`)
and check that the TOC lists entries with page numbers.
