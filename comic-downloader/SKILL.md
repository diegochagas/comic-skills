---
name: comic-downloader
description: Download comic/magazine page images from websites whose image URLs follow a pattern (numbered pages, numbered issues with dates, galleries, or plain URL lists), driven by JSON site profiles in sites/<name>/download.config.json. Use when the user asks to "download the pages/issues of <site>", "grab issues 500 to 520", "fetch these images", wants a new site profile written from a URL pattern, or mentions the Dorothee Magazine downloads. Always dry-runs first; picks --site/--config/--dry-run/--overwrite and edits or creates the profile from what the user says.
---

# comic-downloader — pattern-based page downloads

One Node script, `comic-downloader/scripts/download.cjs`, plus one JSON
profile per website in `comic-downloader/sites/<name>/download.config.json`.
Needs `comic-downloader/node_modules` (axios; `comic-downloader/setup.sh`
or `npm install` inside `comic-downloader/`, also run by `<repo>/setup.sh`). `<repo>` is the image-skills checkout; run
everything from there. Files are written to `~/Downloads/<outputDir>/`
(`COMIC_OUTPUT_DIR` replaces `~/Downloads`): a profile's `outputDir` is a
folder name under that root (`"dorothee"` → `~/Downloads/dorothee/`), defaults
to the profile's folder name when omitted, and is taken literally only when
it is absolute or starts with `~/` — use that when the user names a place.

```bash
node comic-downloader/scripts/download.cjs --site <name> --dry-run   # preview URLs -> paths
node comic-downloader/scripts/download.cjs --site <name>             # download (skips existing files)
node comic-downloader/scripts/download.cjs --config <path/to/download.config.json> [--dry-run] [--overwrite]
```

## Choosing the flags and the profile

| The user says... | Do |
| --- | --- |
| "download <site>" and a profile exists in `sites/` | `--site <name> --dry-run`, show the first and last few `url -> path` lines and the total, then run without `--dry-run` |
| "issues 500 to 520", "only 2004", "pages 1-40" | edit the profile's `issueRanges` / `itemRanges` / `items` / `pages` to that subset (or copy it to a sibling profile), dry-run, run |
| "download again", "replace the files", "the files are corrupt" | `--overwrite` (otherwise existing files are skipped) |
| "just show me what it would download" | `--dry-run` only |
| a profile outside `sites/` | `--config <path>` (`outputDir` still resolves under `~/Downloads`) |
| a new site | write `sites/<name>/download.config.json` from the URL pattern (below), keep any page source the user pasted in `sites/<name>/reference/`, dry-run, show it, then run |

Exit status is 1 if any download failed; the summary line prints saved /
skipped / failed / dry-run counts. Default HTTP timeout 30 s
(`request.timeoutMs` in the profile).

## Writing a profile from a URL pattern

Start from `sites/example/download.config.json`. Work out from one or two
real image URLs which parts vary (issue number, page number, date) and
express them as templates:

- **Numbered pages of one thing** — `baseUrl` + `downloads[].urlPathTemplate`
  with `{page}` / `{pagePadded}` and `pages: {"from": 1, "to": N}`;
  `pageNumberPadding` sets the zero padding.
- **Numbered issues/volumes** — `issueRanges: [{"startIssue", "endIssue",
  "startDate": "YYYY-MM", "endDate"}]` (monthly step by default,
  `dateStepMonths` to change) or `itemRanges` / explicit `items`. Each issue
  gets a folder from `itemFolderTemplate` (e.g. `"{issue} - {dateLabel}"`,
  with `monthNames` for a localized month); `{issuePadded}` /
  `{itemPadded}` in URL templates.
- **Galleries with slugs** — `items: [{"id", "slug"}]` and `{slug}` in the
  templates.
- **Plain lists** — `downloads[].urls: [{"url", "fileName"}]`.

Template values available: `{siteName}` `{baseUrl}` `{item}` `{itemId}`
`{itemNumber}` `{itemPadded}` `{issue}` `{issueNumber}` `{issuePadded}`
`{date}` `{year}` `{month}` `{day}` `{monthName}` `{dateLabel}` `{page}`
`{pagePadded}` `{downloadName}` `{urlPath}` `{url}`, plus any key in
`variables` (site or download level) and on the item. A missing value is a
hard error, so dry-run before downloading. `fileNameTemplate` overrides the
name inferred from the URL; `outputSubdirTemplate` adds a subfolder per
download target. Several `downloads` entries can target different page
groups of the same issue (the Dorothee profile fetches pages 35–38 of each
issue that way).

Sites that need cookies, logins or JavaScript-built URLs are out of scope
for this script; say so and suggest saving the page source under
`reference/` so the pattern can be derived from it.

## Report

Profile used, number of targets, where the files landed, and the failed URLs
(if any) with their error messages.
