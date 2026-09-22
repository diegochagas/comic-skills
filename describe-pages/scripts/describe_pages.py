#!/usr/bin/env python3
"""Describe every page image under a folder with a local vision model (Ollama)
and save the descriptions as a named, searchable set in ~/Downloads/<name> descriptions/.

    venv/bin/python describe-pages/scripts/describe_pages.py "<folder>" [--name NAME] [--model MODEL]

The folder is walked recursively; every .jpg/.jpeg/.png/.webp/.gif/.bmp/.tif
is a page. Each page is downscaled to --max-side px (default 1024) in memory -
the source files are never touched - and sent to the model, which answers a
fixed JSON form (page_type, summary, objects, characters, setting,
text_summary). One line per page is appended to

    ~/Downloads/<name> descriptions/pages.jsonl   (COMIC_OUTPUT_DIR replaces ~/Downloads, --output a folder)
    ~/Downloads/<name> descriptions/index.json    source folder, model, counts

so an interrupted run resumes where it stopped (already described pages are
skipped unless --force). The name defaults to a slug of the last two path
components of the folder ("(1998-2003) V-Tamer 01/Japanese" ->
"1998-2003-v-tamer-01-japanese"); find-pages searches a set by that name.
Nothing is written into the repo or the source folder.

Model: --model or $DESCRIBE_MODEL, else the first installed model of
PREFERRED_MODELS (vision-capable Ollama models, best first). Ollama must be
running at $OLLAMA_URL (default http://localhost:11434). No other service, no
API key, nothing leaves the machine.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

SKILL_DIR = Path(__file__).resolve().parent.parent
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
PREFERRED_MODELS = [
    "qwen3-vl:8b", "qwen3-vl:4b", "qwen2.5vl:7b", "qwen2.5vl:3b",
    "gemma3:12b", "gemma3:4b", "minicpm-v:latest", "llava:13b", "llava:7b",
]
PAGE_TYPES = "story, cover, title, contents, ad, insert, bio, sketch, blank, other"

PROMPT = f"""You index comic and manga pages so that people can later search them by what is drawn on them.
Look at this page carefully and answer in JSON with exactly these keys:
"page_type": one of {PAGE_TYPES}
"summary": 2-3 sentences describing what is happening on the page
"objects": list of every concrete thing visible, foreground and background - devices (computer, laptop, monitor, keyboard, phone, camera, television, game console, watch), vehicles, animals, creatures, monsters, robots, buildings, furniture, tools, weapons, food, clothing, accessories, plants, natural features. Be specific (say "sword" and "laptop", not "weapon" and "device"), simple generic English nouns, singular, no duplicates, 5 to 20 items, only what is really drawn.
"characters": list of short descriptions of each person or creature (appearance, clothing, what they are doing). Do not guess names.
"setting": where the scene takes place (indoors/outdoors, kind of place, time of day if visible)
"text_summary": one sentence saying what the visible text is about, if any (do not transcribe it)
Do not invent anything that is not on the page. JSON only, no commentary."""

_ERR_OLLAMA = ("Ollama is not reachable at {url}. Start it (`ollama serve`, or the "
               "ollama systemd service) and run the same command again.")


def slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s) or "pages"


def default_name(folder: Path) -> str:
    parts = [p for p in folder.parts if p not in ("/", "")]
    return slugify(" ".join(parts[-2:]))


SET_SUFFIX = " descriptions"


def output_root() -> Path:
    env = os.environ.get("COMIC_OUTPUT_DIR")
    return Path(env).expanduser() if env else Path.home() / "Downloads"


def list_sets() -> list[Path]:
    root = output_root()
    return sorted(p.parent for p in root.glob(f"*{SET_SUFFIX}/index.json")) if root.exists() else []


def list_images(folder: Path) -> list[Path]:
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS
             and not p.name.startswith(".")]
    return sorted(files, key=lambda p: [natural_key(part) for part in p.relative_to(folder).parts])


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------- Ollama ---

def ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")


def ollama_get(path: str, timeout: float = 5):
    with urllib.request.urlopen(ollama_url() + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ollama_post(path: str, payload: dict, timeout: float = 600):
    req = urllib.request.Request(ollama_url() + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def installed_models() -> list[str]:
    try:
        return [m["name"] for m in ollama_get("/api/tags").get("models", [])]
    except (urllib.error.URLError, OSError) as e:
        sys.exit(_ERR_OLLAMA.format(url=ollama_url()) + f" ({e})")


def has_vision(model: str) -> bool:
    try:
        info = ollama_post("/api/show", {"model": model}, timeout=30)
    except (urllib.error.URLError, OSError):
        return False
    return "vision" in info.get("capabilities", [])


def pick_model(requested: str | None) -> str:
    installed = installed_models()
    if requested:
        if requested not in installed and requested + ":latest" not in installed:
            sys.exit(f"Model {requested!r} is not installed. `ollama pull {requested}` first, "
                     f"or pick one of: {', '.join(installed) or '(none)'}")
        if not has_vision(requested):
            sys.exit(f"Model {requested!r} has no vision capability - it cannot look at images.")
        return requested
    for m in PREFERRED_MODELS:
        if m in installed or (m.endswith(":latest") and m[:-7] in installed):
            return m
    vision = [m for m in installed if has_vision(m)]
    if vision:
        return vision[0]
    sys.exit("No vision model installed in Ollama. Run `ollama pull qwen3-vl:4b` "
             "(3.3 GB) and try again.")


# ---------------------------------------------------------------- pages ---

def encode_image(path: Path, max_side: int) -> tuple[str, tuple[int, int]]:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        size = im.size
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode(), size


def normalise(desc: dict) -> dict:
    """Make the model's answer uniform: strings stripped, lists of strings."""
    out = {}
    out["page_type"] = str(desc.get("page_type", "other")).strip().lower() or "other"
    for k in ("summary", "setting", "text_summary"):
        v = desc.get(k, "")
        out[k] = v.strip() if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    for k in ("objects", "characters"):
        v = desc.get(k, [])
        if isinstance(v, str):
            v = [x for x in re.split(r"[,;\n]+", v)]
        items = []
        for x in v if isinstance(v, list) else []:
            if isinstance(x, dict):
                x = " ".join(str(y) for y in x.values())
            x = str(x).strip().strip(".")
            if x and x.lower() not in {i.lower() for i in items}:
                items.append(x)
        out[k] = items
    return out


def extract_json(text: str) -> dict | None:
    """First JSON object in the text (models sometimes wrap it in prose or <think> tags)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def describe_one(model: str, b64: str, num_predict: int) -> tuple[dict, dict]:
    # think=False: qwen3-vl otherwise spends the whole budget in <think>. Ollama's
    # qwen3-vl template still puts the answer in "thinking" instead of "response"
    # sometimes, so both fields are read.
    payload = {
        "model": model, "prompt": PROMPT, "images": [b64], "stream": False, "format": "json",
        "think": False,
        "options": {"temperature": 0.1, "num_predict": num_predict, "repeat_penalty": 1.15},
    }
    last = ""
    for attempt in range(2):
        resp = ollama_post("/api/generate", payload)
        text = resp.get("response") or resp.get("thinking") or ""
        data = extract_json(text)
        if data and data.get("summary"):
            return normalise(data), resp
        last = text
        payload["options"]["temperature"] = 0.3      # nudge the retry
    raise ValueError(f"model did not return usable JSON: {last[:200]!r}")


def load_done(pages_file: Path) -> dict[str, dict]:
    done = {}
    if pages_file.exists():
        with pages_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not rec.get("error"):
                    done[rec["path"]] = rec
    return done


def rewrite_without(pages_file: Path, paths: set[str]) -> None:
    if not pages_file.exists():
        return
    keep = [l for l in pages_file.read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l).get("path") not in paths]
    pages_file.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")


def write_index(set_dir: Path, meta: dict, pages_file: Path) -> None:
    total = errors = 0
    types: dict[str, int] = {}
    if pages_file.exists():
        for line in pages_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("error"):
                errors += 1
            else:
                total += 1
                t = rec["description"]["page_type"]
                types[t] = types.get(t, 0) + 1
    meta.update({"described": total, "failed": errors, "page_types": types,
                 "updated": time.strftime("%Y-%m-%d %H:%M:%S")})
    (set_dir / "index.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")


def cmd_list() -> None:
    sets = list_sets()
    if not sets:
        print(f"No description sets in {output_root()}")
        return
    for d in sets:
        meta = json.loads((d / "index.json").read_text(encoding="utf-8"))
        print(f"{meta.get('name', d.name)}: {meta.get('described', 0)}/{meta.get('images', '?')} pages, "
              f"model {meta.get('model')}, source {meta.get('source')}  [{d}]")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", help="folder of page images (walked recursively)")
    ap.add_argument("--name", help="name of the description set (default: slug of the last two path parts)")
    ap.add_argument("--output", help="folder to write the set into (default ~/Downloads/<name> descriptions/)")
    ap.add_argument("--model", default=os.environ.get("DESCRIBE_MODEL"),
                    help="Ollama vision model (default: $DESCRIBE_MODEL or the best installed one)")
    ap.add_argument("--max-side", type=int, default=1024, help="downscale pages to this many px on the long side (default 1024)")
    ap.add_argument("--num-predict", type=int, default=700, help="max tokens per description (default 700)")
    ap.add_argument("--limit", type=int, help="describe at most N new pages, then stop (for a test run)")
    ap.add_argument("--only", nargs="+", help="describe only pages whose relative path contains one of these strings")
    ap.add_argument("--force", action="store_true", help="re-describe pages that already have a description")
    ap.add_argument("--list", action="store_true", help="list the description sets that exist and exit")
    args = ap.parse_args()

    if args.list:
        cmd_list()
        return
    if not args.folder:
        ap.error("folder is required (or --list)")

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")
    images = list_images(folder)
    if not images:
        sys.exit(f"No images under {folder}")
    if args.only:
        images = [p for p in images if any(s in str(p.relative_to(folder)) for s in args.only)]
        if not images:
            sys.exit("No image matches --only")

    model = pick_model(args.model)
    name = slugify(args.name) if args.name else default_name(folder)
    set_dir = Path(args.output).expanduser().resolve() if args.output else output_root() / (name + SET_SUFFIX)
    if set_dir == folder or folder in set_dir.parents:
        sys.exit("The set cannot be written inside the source folder; use --output elsewhere.")
    set_dir.mkdir(parents=True, exist_ok=True)
    pages_file = set_dir / "pages.jsonl"
    index_file = set_dir / "index.json"

    meta = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
    if meta.get("source") and Path(meta["source"]) != folder:
        sys.exit(f"Set {name!r} already describes {meta['source']}. Use --name to give this folder its own set.")
    meta.update({"name": name, "source": str(folder), "model": model, "images": len(list_images(folder)),
                 "max_side": args.max_side, "created": meta.get("created") or time.strftime("%Y-%m-%d %H:%M:%S")})

    if args.force:
        rewrite_without(pages_file, {str(p) for p in images})
    done = load_done(pages_file)
    todo = [p for p in images if str(p) not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(f"Set {name!r} -> {set_dir}")
    print(f"Source {folder}: {len(images)} images, {len(done)} already described, {len(todo)} to do, model {model}")
    write_index(set_dir, meta, pages_file)
    if not todo:
        print("Nothing to do.")
        return

    t_start = time.time()
    failed = 0
    with pages_file.open("a", encoding="utf-8") as out:
        for i, path in enumerate(todo, 1):
            rel = str(path.relative_to(folder))
            t0 = time.time()
            rec = {"path": str(path), "rel": rel, "folder": str(path.parent.relative_to(folder)) or ".",
                   "model": model}
            try:
                b64, size = encode_image(path, args.max_side)
                desc, resp = describe_one(model, b64, args.num_predict)
                rec.update({"size": list(size), "description": desc,
                            "seconds": round(time.time() - t0, 1),
                            "tokens": resp.get("eval_count")})
                # clear an earlier failure line for this path, if any
                status = f"{desc['page_type']:8s} {', '.join(desc['objects'][:6])}"
            except Exception as e:  # noqa: BLE001 - one bad page must not kill a 5-hour run
                failed += 1
                rec.update({"error": str(e)[:300], "seconds": round(time.time() - t0, 1)})
                status = f"ERROR    {e}"
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            elapsed = time.time() - t_start
            eta = elapsed / i * (len(todo) - i)
            print(f"[{i}/{len(todo)}] {rec['seconds']:5.1f}s  eta {eta/60:5.1f} min  {rel}  ->  {status}", flush=True)
            if i % 10 == 0 or i == len(todo):
                write_index(set_dir, meta, pages_file)

    write_index(set_dir, meta, pages_file)
    total = len(todo) - failed
    print(f"\nDone: {total} described, {failed} failed, {(time.time() - t_start) / 60:.1f} min. "
          f"Set {name!r} in {set_dir}")
    if failed:
        print("Failed pages are retried on the next run of the same command.")


if __name__ == "__main__":
    main()
