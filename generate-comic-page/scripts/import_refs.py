#!/usr/bin/env python3
"""Copy example images (character model sheets, style pages or scenarios) into a project.

Diego hands over a folder of examples the first time a comic is generated,
and more examples whenever a character keeps coming out wrong. This copies
every image into <project>/refs/model-sheets/ (or refs/style/ with --style):
file names are slugified, byte-identical files are skipped, a different file
with the same name gets a numeric suffix. Prints the files that are NEW —
the agent must then LOOK at each one and register it in charmap.json
(keywords, sheets, description).

--scenario imports into refs/scenarios/ (panel mode): pages of the ORIGINAL
manga showing a location, found by searching its description set with
find-pages (--field setting) and checked by eye. --crop x,y,w,h (source px)
keeps just the panel that shows the place. Register them in scenemap.json
(keywords, images, description, source_pages).

Usage: import_refs.py -p <project> <folder-or-image> [more...] [--style | --scenario [--crop x,y,w,h]]
"""
import argparse
import hashlib
import re
import shutil
import sys
import unicodedata
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import IMAGE_EXTS, load_charmap, resolve_project


def slug(stem: str) -> str:
    s = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "ref"


def sha(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", "-p", default=None)
    ap.add_argument("sources", nargs="+")
    kind = ap.add_mutually_exclusive_group()
    kind.add_argument("--style", action="store_true", help="import as style anchors (refs/style/)")
    kind.add_argument("--scenario", action="store_true", help="import as location references (refs/scenarios/)")
    ap.add_argument("--crop", help="x,y,w,h in source pixels: import only this region (one image only)")
    a = ap.parse_args()

    _name, _cfg, pdir = resolve_project(a.project)
    dest = pdir / "refs" / ("style" if a.style else "scenarios" if a.scenario else "model-sheets")
    dest.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for src in (Path(s).expanduser() for s in a.sources):
        if src.is_dir():
            files += sorted(f for f in src.rglob("*") if f.suffix.lower() in IMAGE_EXTS)
        elif src.is_file() and src.suffix.lower() in IMAGE_EXTS:
            files.append(src)
        else:
            sys.exit(f"Not a folder or image: {src}")
    if not files:
        sys.exit("No images found (png, jpg, jpeg, webp)")
    if a.crop and len(files) != 1:
        sys.exit("--crop takes exactly one image")

    have = {sha(f): f.name for f in dest.iterdir() if f.is_file()}
    new: list[str] = []
    for f in files:
        crop = None
        if a.crop:
            x, y, w, h = (int(v) for v in a.crop.split(","))
            crop = Image.open(f).convert("RGB").crop((x, y, x + w, y + h))
            digest = hashlib.sha1(crop.tobytes() + a.crop.encode()).hexdigest()
        else:
            digest = sha(f)
        if digest in have:
            print(f"skip (already imported as {have[digest]}): {f.name}")
            continue
        stem = slug(f"{f.parent.name}-{f.stem}") if a.scenario else slug(f.stem)
        ext = ".png" if crop else f.suffix.lower()
        name, n = f"{stem}{ext}", 1
        while (dest / name).exists():
            n += 1
            name = f"{stem}-{n}{ext}"
        if crop:
            crop.save(dest / name)
        else:
            shutil.copy2(f, dest / name)
        have[digest] = name
        new.append(name)
        print(f"imported: {f.name} -> {dest / name}")

    if new and a.scenario:
        print(f"\n{len(new)} scenario image(s) imported — look at each, then add/extend its entry in "
              f"{pdir / 'scenemap.json'} (keywords, images, description, source_pages)")
    if new and not a.style and not a.scenario:
        mapped = {s for e in load_charmap(pdir).get("map", []) for s in e["sheets"]}
        todo = [n for n in new if n not in mapped]
        print(f"\n{len(todo)} sheet(s) not in charmap.json yet — look at each image, then add/extend its "
              f"entry in {pdir / 'charmap.json'} (keywords, sheets, description):")
        print("\n".join(f"  {dest / n}" for n in todo))


if __name__ == "__main__":
    main()
