#!/usr/bin/env python3
"""Search a description set written by describe-pages and print the paths of
the pages that match.

    venv/bin/python find-pages/scripts/find_pages.py <set-name> <term> [<term> ...] [flags]

<set-name> is the name describe-pages gave the set (the folder
~/Downloads/<name> descriptions/; COMIC_OUTPUT_DIR replaces ~/Downloads) or
the path of such a folder; a unique prefix or substring of the name is
enough. Nothing is written. Terms are matched
case-insensitively as whole words, with a light stem so that "computer" also
finds "computers"; a term with a space is a phrase. Every term must match
(AND) unless --any. The search runs over the summary, objects, characters,
setting and text_summary of each page; --field limits it and --type filters
by page_type.

Output: one absolute image path per line (feed it to anything), or --show for
the matching text next to each path, or --json for the full records. Pages
are ranked: a hit in objects counts more than one in the summary.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SET_SUFFIX = " descriptions"
FIELDS = ("objects", "characters", "summary", "setting", "text_summary")
WEIGHTS = {"objects": 3, "characters": 2, "summary": 1, "setting": 1, "text_summary": 1}


def output_root() -> Path:
    env = os.environ.get("COMIC_OUTPUT_DIR")
    return Path(env).expanduser() if env else Path.home() / "Downloads"


def list_sets() -> list[Path]:
    root = output_root()
    return sorted(p.parent for p in root.glob(f"*{SET_SUFFIX}/pages.jsonl")) if root.exists() else []


def set_name(d: Path) -> str:
    return d.name[: -len(SET_SUFFIX)] if d.name.endswith(SET_SUFFIX) else d.name


def resolve_set(name: str) -> Path:
    p = Path(name).expanduser()
    if p.is_dir() and (p / "pages.jsonl").exists():
        return p
    root = output_root()
    for cand in (root / name, root / (name + SET_SUFFIX)):
        if (cand / "pages.jsonl").exists():
            return cand
    sets = list_sets()
    names = {set_name(d): d for d in sets}
    low = name.lower()
    hits = [n for n in names if n.lower().startswith(low)] or [n for n in names if low in n.lower()]
    if len(hits) == 1:
        return names[hits[0]]
    if not names:
        sys.exit(f"No description sets ('* descriptions/' folders) in {root}. Run describe-pages first.")
    if len(hits) > 1:
        sys.exit(f"Ambiguous set name {name!r}: " + ", ".join(hits))
    sys.exit(f"No description set called {name!r}. Available: " + ", ".join(names))


def load_pages(set_dir: Path) -> tuple[dict, list[dict]]:
    meta = {}
    idx = set_dir / "index.json"
    if idx.exists():
        meta = json.loads(idx.read_text(encoding="utf-8"))
    pages = []
    with (set_dir / "pages.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("description"):
                pages.append(rec)
    return meta, pages


def stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ies", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return w


def term_pattern(term: str) -> re.Pattern:
    words = [re.escape(stem(w)) for w in re.findall(r"\w+", term)]
    if not words:
        return re.compile(r"(?!x)x")
    # each word may carry a plural/verb suffix after its stem
    body = r"\W+".join(w + r"(?:s|es|ies|ed|ing)?" for w in words)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.I)


def field_text(desc: dict, field: str) -> str:
    v = desc.get(field, "")
    return "; ".join(v) if isinstance(v, list) else str(v)


def score_page(rec: dict, patterns: list[re.Pattern], fields: tuple[str, ...], require_all: bool):
    desc = rec["description"]
    total = 0
    matched_terms = 0
    snippets = []
    for pat in patterns:
        term_hit = False
        for field in fields:
            text = field_text(desc, field)
            m = pat.search(text)
            if not m:
                continue
            term_hit = True
            total += WEIGHTS[field]
            if field in ("objects", "characters"):
                items = [x for x in desc.get(field, []) if pat.search(x)]
                snippets.append(f"{field}: " + ", ".join(items))
            else:
                a, b = max(0, m.start() - 60), min(len(text), m.end() + 60)
                snippets.append(f"{field}: …{text[a:b].strip()}…")
        if term_hit:
            matched_terms += 1
    if matched_terms == 0 or (require_all and matched_terms < len(patterns)):
        return None
    return total + matched_terms * 10, snippets


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", nargs="?", help="description set name (or a unique part of it, or its folder)")
    ap.add_argument("terms", nargs="*", help="search terms; quote a phrase; all must match unless --any")
    ap.add_argument("--any", action="store_true", help="a page matches if ANY term matches (default: all)")
    ap.add_argument("--field", choices=FIELDS, action="append",
                    help="search only these fields (repeatable; default: all)")
    ap.add_argument("--type", action="append", help="only pages of this page_type (repeatable): story, cover, bio…")
    ap.add_argument("--limit", type=int, default=0, help="print at most N pages (default: all)")
    ap.add_argument("--show", action="store_true", help="print the matching text under each path")
    ap.add_argument("--json", action="store_true", help="print the full records as a JSON list")
    ap.add_argument("--list", action="store_true", help="list the description sets and exit")
    ap.add_argument("--types", action="store_true", help="show the page_type counts of the set and exit")
    args = ap.parse_args()

    if args.list:
        sets = list_sets()
        if not sets:
            print(f"No description sets in {output_root()}")
        for d in sets:
            idx = d / "index.json"
            meta = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else {}
            print(f"{set_name(d)}: {meta.get('described', '?')}/{meta.get('images', '?')} pages, "
                  f"model {meta.get('model')}, source {meta.get('source')}  [{d}]")
        return
    if not args.set:
        ap.error("set name is required (or --list)")

    set_dir = resolve_set(args.set)
    meta, pages = load_pages(set_dir)
    if args.types:
        counts: dict[str, int] = {}
        for rec in pages:
            t = rec["description"].get("page_type", "other")
            counts[t] = counts.get(t, 0) + 1
        for t, n in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"{n:5d}  {t}")
        return
    if not args.terms:
        ap.error("give at least one search term")

    fields = tuple(args.field) if args.field else FIELDS
    patterns = [term_pattern(t) for t in args.terms]
    wanted_types = {t.lower() for t in args.type} if args.type else None

    results = []
    for rec in pages:
        if wanted_types and rec["description"].get("page_type", "other") not in wanted_types:
            continue
        scored = score_page(rec, patterns, fields, not args.any)
        if scored:
            score, snippets = scored
            results.append((score, rec, snippets))
    results.sort(key=lambda r: (-r[0], r[1]["rel"]))
    if args.limit:
        results = results[: args.limit]

    if args.json:
        print(json.dumps([{**rec, "score": score, "matches": snippets} for score, rec, snippets in results],
                         ensure_ascii=False, indent=1))
        return
    for score, rec, snippets in results:
        print(rec["path"])
        if args.show:
            for s in snippets:
                print(f"    {s}")
    query = (" OR " if args.any else " AND ").join(repr(t) for t in args.terms)
    print(f"# {len(results)} of {len(pages)} described pages match {query} in set {set_name(set_dir)!r} "
          f"({len(pages)}/{meta.get('images', '?')} pages of the source are described)", file=sys.stderr)


if __name__ == "__main__":
    main()
