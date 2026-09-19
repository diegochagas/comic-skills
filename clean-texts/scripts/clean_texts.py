#!/usr/bin/env python3
"""Erase the text of an image (or of every image in a folder) through the Higgsfield CLI.

The image model redraws the whole picture, so the script does the two things
that keep "remove the text, change nothing else" true:

  1. geometry  - the source is padded (mirrored edges) to the closest aspect
     ratio the model accepts, and the result is scaled back and cropped to the
     source's exact pixel size;
  2. restore   - only the regions the model really changed (the erased text)
     are taken from its output; every other pixel is copied from the original,
     at the original sharpness. `--no-restore` keeps the raw model output.

Results: <root>/<stem>.png for one image, <root>/<folder name> clean/<stem>.png
for a folder, <root> = $COMIC_OUTPUT_DIR or ~/Downloads (`--output` overrides:
a .png path for one image, a directory otherwise). The raw model output and
the restore mask (+ a numbered view of the erased regions) go to
`clean-texts-work/` next to the results. Sources are
never modified; an existing result is skipped unless --force.

One image = one generation = credits spent (`--cost` asks the price, `--dry-run`
prints the prompt and the plan, both spend nothing).

`--backend local` spends nothing: manga-translator-ptbr's text detector finds
the text, fills it with the exact background colour where that is one plain
colour (balloons, caption boxes) and redraws the art behind the rest with a
local Qwen-Image-Edit through ComfyUI (`--inpaint qwen`, the default; LaMa
when ComfyUI is unreachable or with `--inpaint lama`). Only detected text
pixels change. It erases what the detector finds — every language at once
(no --language/--keep/--fix) and it can miss stylised titles or sound
effects that the Higgsfield backend would erase.

Usage:
  clean_texts.py <image-or-folder>... [--language LANG] [--keep "what stays"]
                 [--fix "what the last try got wrong"] [--output PATH] [--force]
                 [--model gpt_image_2_5] [--quality low] [--resolution 2k]
                 [--no-restore] [--restore-threshold 48] [--reuse-raw]
                 [--drop-region N [N ...]] [--cost] [--dry-run]
  clean_texts.py <image-or-folder>... --backend local [--inpaint qwen|lama]
                 [--output PATH] [--force] [--dry-run]

  --language  erase ONLY the text written in this language (e.g. Japanese);
              text in any other language stays, letter by letter.
              Without it, ALL text is erased.
  --keep      text that must survive, in words ("the sound effects", "the logo")
  --fix       targeted correction appended to the prompt for a reroll
  --reuse-raw rebuild the result from the saved raw model output (free, no
              generation) — with --restore-threshold or --no-restore
  --drop-region  (one image) give these numbered regions of
              clean-texts-work/<stem>.regions.png back to the original: text
              the model should NOT have erased, or art it damaged. Free with
              --reuse-raw.
  --restore-threshold  how strong a change must be to count as erased text
              (0-255, default 48): lower it when the raw output erased a faint
              text that the result still shows, raise it when redrawn art leaks in
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HF = shutil.which("higgsfield") or str(Path.home() / "hf/node_modules/.bin/higgsfield")
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
ASPECTS = ["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9", "27:16", "16:27",
           "9:8", "8:9", "4:5", "5:4"]
UPLOAD_MAX_SIDE = 3072
WORK_DIR = "clean-texts-work"
TRANSLATOR_SCRIPTS = Path(__file__).resolve().parents[2] / "manga-translator-ptbr" / "scripts"
MASK_SIDE = 1536        # restore mask is computed at this long side
LOW_THR = 16            # local-density change (0-255) that counts as "changed"
SEED_THR = 48           # ...and a changed region needs a peak above this to be erased text

PROMPT_ALL = (
    "Edit the attached image. Make exactly this ONE change: erase ALL text from it — every letter, "
    "number, word and punctuation mark, in any language or script: dialogue inside speech and "
    "thought balloons, captions and narration boxes, sound effects and onomatopoeia, titles, "
    "logos, signs, labels, page numbers, credits, watermarks and signatures{keep}.")

PROMPT_LANG = (
    "Edit the attached image. It contains text in more than one language. Make exactly this ONE "
    "change: erase the text written in {lang}, and ONLY that text — {lang} dialogue inside speech "
    "and thought balloons, captions and narration boxes, sound effects and onomatopoeia, titles, "
    "logos, signs, labels and credits{keep}.\n\n"
    "SELECTIVE ERASE — before erasing any block of text, look at its script and language. If it "
    "is not {lang}, it MUST REMAIN in the image exactly as it is: same words letter by letter, "
    "same font, size, colour and position, its balloon still full. Text in every other language "
    "or alphabet is part of the artwork here. An output where ALL the text is gone is WRONG.")

PROMPT_RULES = (
    "Where text is erased, rebuild what was behind it: a balloon or caption box keeps its outline, "
    "shape and tail and becomes EMPTY, filled with its own flat background colour; text that sat "
    "over artwork is replaced by a seamless continuation of the drawing, screentone, texture or "
    "background underneath. Leave NO letters, no ghost strokes, no pseudo-text and no blur smudges "
    "behind, and never write any new text.\n\n"
    "DO NOT CHANGE anything else. Same composition, framing and crop; same panels and gutters; "
    "same characters, faces, expressions and poses; same linework, screentones, colours, shading "
    "and paper tone; same position of every element, pixel for pixel. Do not redraw, restyle, "
    "sharpen, recolour, clean up, upscale, add or remove anything that is not text. No watermarks, "
    "no signatures, no borders.")


def output_root() -> Path:
    return Path(os.environ.get("COMIC_OUTPUT_DIR") or Path.home() / "Downloads").expanduser()


def build_prompt(language: str | None, keep: str | None, fix: str | None) -> str:
    keep_txt = f" — EXCEPT {keep}, which must stay exactly as it is" if keep else ""
    head = (PROMPT_LANG.format(lang=language, keep=keep_txt) if language
            else PROMPT_ALL.format(keep=keep_txt))
    parts = [head, PROMPT_RULES]
    if fix:
        parts.append("CORRECTION FOR THIS ATTEMPT (the previous attempt got this wrong — fix it, "
                     "keep everything else as specified): " + fix)
    return "\n\n".join(parts)


def plan_outputs(sources: list[str], output: str | None) -> list[tuple[Path, Path]]:
    """[(source image, result png)] — never a path that is one of the sources."""
    root = output_root()
    out = Path(output).expanduser() if output else None
    jobs: list[tuple[Path, Path]] = []
    single = len(sources) == 1 and Path(sources[0]).expanduser().is_file()
    for s in sources:
        src = Path(s).expanduser().resolve()
        if src.is_dir():
            files = sorted(f for f in src.iterdir() if f.is_file() and f.suffix.lower() in EXTS)
            dest = out or root / f"{src.name} clean"
        elif src.is_file():
            files, dest = [src], out or root
        else:
            sys.exit(f"ERROR not found: {src}")
        if single and out and out.suffix.lower() == ".png":
            jobs.append((src, out))
            continue
        for f in files:
            jobs.append((f, dest / f"{f.stem}.png"))
    seen: dict[Path, Path] = {}
    fixed = []
    for src, dst in jobs:
        dst = dst.resolve()
        if dst == src:                       # a PNG that already sits in the results folder
            dst = dst.with_name(f"{src.stem} clean.png")
        if dst in seen and seen[dst] != src:  # page.jpg + page.png in the same folder
            dst = dst.with_name(f"{src.stem} ({src.suffix.lstrip('.').lower()}).png")
        seen[dst] = src
        fixed.append((src, dst))
    return fixed


def closest_aspect(w: int, h: int) -> tuple[str, float]:
    def ratio(a: str) -> float:
        x, y = a.split(":")
        return int(x) / int(y)
    name = min(ASPECTS, key=lambda a: abs(np.log((w / h) / ratio(a))))
    return name, ratio(name)


def load_rgb(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """RGB array (transparency flattened on white) + the alpha channel, if any."""
    with Image.open(path) as im:
        im.seek(0)
        if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            return np.array(flat), np.array(rgba.getchannel("A"))
        return np.array(im.convert("RGB")), None


def pad_to_aspect(img: np.ndarray, ratio: float) -> tuple[np.ndarray, tuple[int, int]]:
    """Mirror-pad to the exact aspect ratio; returns the canvas and the (x, y) of the image in it."""
    h, w = img.shape[:2]
    cw, ch = (w, round(w / ratio)) if w / h > ratio else (round(h * ratio), h)
    cw, ch = max(cw, w), max(ch, h)
    x, y = (cw - w) // 2, (ch - h) // 2
    canvas = cv2.copyMakeBorder(img, y, ch - h - y, x, cw - w - x, cv2.BORDER_REFLECT_101)
    return canvas, (x, y)


def restore_original(orig: np.ndarray, gen: np.ndarray, seed_thr: float,
                     drop: list[int]) -> tuple[np.ndarray, np.ndarray, list[tuple[int, int, int, int]]]:
    """Keep the model's pixels only where it erased text; everything else comes from the original.

    The model re-renders lines and screentones a little differently everywhere,
    but that keeps the LOCAL INK DENSITY; erased text does not. So the two
    images are compared heavily blurred: a region counts as changed when its
    density moved (> LOW) and is kept only if somewhere inside it moved a lot
    (> seed_thr, what a block of erased letters does; redrawn art stays below).
    Regions are numbered in reading order (top to bottom, then left to right);
    the ones listed in `drop` go back to the original too.
    Returns the result, the mask and the (x, y, w, h) of every numbered region.
    """
    h, w = orig.shape[:2]
    ws = min(1.0, MASK_SIDE / max(h, w))        # the model works at ~2k: finer detail is noise here

    def work(img: np.ndarray) -> np.ndarray:
        if ws < 1.0:
            img = cv2.resize(img, (round(w * ws), round(h * ws)), interpolation=cv2.INTER_AREA)
        return img.astype(np.float32)
    o, g = work(orig), work(gen)
    shift = np.median((o - g).reshape(-1, 3), axis=0)           # undo a global tone shift
    side = max(o.shape[:2])
    sigma = max(2.0, side * 0.012)
    diff = np.abs(cv2.GaussianBlur(o, (0, 0), sigma) - cv2.GaussianBlur(g + shift, (0, 0), sigma)).max(axis=2)

    def k(frac: float) -> np.ndarray:
        n = max(3, int(side * frac)) | 1
        return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))
    low = cv2.morphologyEx((diff > LOW_THR).astype(np.uint8), cv2.MORPH_OPEN, k(0.004))
    _, labels = cv2.connectedComponents(low)
    strong = np.unique(labels[(diff > seed_thr) & (low > 0)])
    mask = np.isin(labels, strong[strong > 0]).astype(np.uint8)
    mask = cv2.dilate(mask, k(0.024))                           # whole glyphs + a margin around them
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k(0.02))
    mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    order = sorted(range(1, n), key=lambda i: (stats[i][1] // max(1, h // 12), stats[i][0]))
    boxes = [tuple(int(v) for v in stats[i][:4]) for i in order]
    for num in drop:
        if not 1 <= num <= len(order):
            sys.exit(f"ERROR --drop-region {num}: this image has regions 1..{len(order)}")
        mask[labels == order[num - 1]] = 0
    # feather outwards only, so no original letter bleeds back in at the rim
    soft = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), max(1.0, max(h, w) * 0.003))
    soft = np.maximum(soft, mask.astype(np.float32))[..., None]
    final = orig.astype(np.float32) * (1 - soft) + np.clip(gen.astype(np.float32) + shift, 0, 255) * soft
    return final.round().astype(np.uint8), mask * 255, boxes


def draw_regions(orig: np.ndarray, mask: np.ndarray, boxes: list, drop: list[int]) -> np.ndarray:
    """The original with every region outlined and numbered (red = erased, blue = dropped)."""
    view = orig.copy()
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    t = max(2, max(orig.shape[:2]) // 400)
    cv2.drawContours(view, contours, -1, (230, 0, 0), t)
    for num, (x, y, bw, bh) in enumerate(boxes, 1):
        colour = (0, 90, 230) if num in drop else (230, 0, 0)
        if num in drop:
            cv2.rectangle(view, (x, y), (x + bw, y + bh), colour, t)
        scale = t * 0.45
        (tw, th), _ = cv2.getTextSize(str(num), cv2.FONT_HERSHEY_SIMPLEX, scale, t)
        cv2.rectangle(view, (x, y), (x + tw + 2 * t, y + th + 2 * t), colour, -1)
        cv2.putText(view, str(num), (x + t, y + th + t), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), t)
    return view


def find_url(o):
    if isinstance(o, str) and o.startswith("http") and \
            o.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp")):
        return o
    for v in (o.values() if isinstance(o, dict) else o if isinstance(o, list) else []):
        u = find_url(v)
        if u:
            return u
    return None


def run_generation(cmd: list[str]) -> str:
    """Run the CLI, retrying transient failures (503, intermittent nsfw filter)."""
    err = ""
    for _ in range(4):
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=660)
        out = (res.stdout or "").strip()
        err = ((res.stderr or "").strip() + " " + out).strip()
        if res.returncode == 0 and out:
            return out
        if re.search(r"unauthori[sz]ed|not logged in|auth login|\b401\b", err, re.I):
            sys.exit(f"ERROR auth — ask Diego to run `higgsfield auth login`: {err[:300]}")
        if not any(t in err.lower() for t in ("503", "service unavailable", "nsfw", "timeout", "temporarily")):
            sys.exit(f"ERROR {res.returncode} {err[:500]}")
        time.sleep(4)
    sys.exit(f"ERROR retries-exhausted {err[:300]}")


def generate(src: Path, orig: np.ndarray, prompt: str, a: argparse.Namespace) -> np.ndarray | None:
    """One generation, returned in the source's exact geometry (None with --cost)."""
    h, w = orig.shape[:2]
    aspect, ratio = closest_aspect(w, h)
    canvas, (px, py) = pad_to_aspect(orig, ratio)
    ch, cw = canvas.shape[:2]

    with tempfile.TemporaryDirectory(prefix="clean-texts-") as tmp:
        upload = Path(tmp) / f"{src.stem}.png"
        scale = min(1.0, UPLOAD_MAX_SIDE / max(cw, ch))
        small = canvas if scale == 1.0 else cv2.resize(
            canvas, (round(cw * scale), round(ch * scale)), interpolation=cv2.INTER_AREA)
        Image.fromarray(small).save(upload)

        cmd = [HF, "generate", "cost" if a.cost else "create", a.model, "--prompt", prompt,
               "--aspect_ratio", aspect, "--resolution", a.resolution, "--json",
               "--image-references", str(upload)]
        if a.model.startswith("gpt_image"):
            cmd += ["--quality", a.quality]
        if a.cost:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            print(f"{src.name}: {res.stdout.strip() or res.stderr.strip()}")
            return None
        out = run_generation(cmd + ["--wait", "--wait-timeout", "10m"])
        try:
            url = find_url(json.loads(out))
        except ValueError as e:
            sys.exit(f"PARSE_FAIL {e} {out[:400]}")
        if not url:
            sys.exit(f"NO_URL {out[:400]}")
        raw_file = Path(tmp) / "raw"
        urllib.request.urlretrieve(url, raw_file)
        with Image.open(raw_file) as im:
            gen = np.array(im.convert("RGB"))

    # back to the source's geometry: canvas size, then crop the padding away
    gen = cv2.resize(gen, (cw, ch), interpolation=cv2.INTER_LANCZOS4 if gen.shape[1] < cw else cv2.INTER_AREA)
    return gen[py:py + h, px:px + w]


def clean_one(src: Path, dst: Path, prompt: str, a: argparse.Namespace) -> None:
    orig, alpha = load_rgb(src)
    h, w = orig.shape[:2]
    work = dst.parent / WORK_DIR
    raw_path = work / f"{dst.stem}.raw.png"
    if a.reuse_raw:
        if not raw_path.exists():
            sys.exit(f"ERROR --reuse-raw: no previous model output at {raw_path}")
        with Image.open(raw_path) as im:
            gen = np.array(im.convert("RGB"))
        if gen.shape[:2] != (h, w):
            sys.exit(f"ERROR --reuse-raw: {raw_path} is not the size of {src}")
    else:
        gen = generate(src, orig, prompt, a)
        if gen is None:
            return
        work.mkdir(parents=True, exist_ok=True)
        Image.fromarray(gen).save(raw_path)

    note = "raw model output"
    final = gen
    if not a.no_restore:
        final, mask, boxes = restore_original(orig, gen, a.restore_threshold, a.drop_region)
        work.mkdir(parents=True, exist_ok=True)
        Image.fromarray(mask).save(work / f"{dst.stem}.mask.png")
        Image.fromarray(draw_regions(orig, mask, boxes, a.drop_region)).save(work / f"{dst.stem}.regions.png")
        coverage = float((mask > 0).mean())
        note = (f"{len(boxes) - len(a.drop_region)} erased region(s), {coverage:.0%} of the pixels "
                "from the model, the rest from the original")
        if not boxes:
            note += " — WARNING: nothing was erased (no text, or the model left it all)"
        if coverage > 0.6:
            note += " — WARNING: the model changed most of the image, compare it with the original"
    result = Image.fromarray(final)
    if alpha is not None:
        result.putalpha(Image.fromarray(alpha))
    result.save(dst)
    print(f"{dst}  ({w}x{h}, {closest_aspect(w, h)[0]}, {note})")


def clean_local(src: Path, dst: Path, detect_session) -> None:
    """Detector + exact fills + local inpainting (see --backend local)."""
    import detect_text
    orig, alpha = load_rgb(src)
    h, w = orig.shape[:2]
    work = dst.parent / WORK_DIR
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clean-texts-") as tmp:
        flat = Path(tmp) / f"{dst.stem}.png"          # transparency flattened on white, like the model backend
        Image.fromarray(orig).save(flat)
        detect_text.detect_page(detect_session, str(flat), str(work))
    info = json.loads((work / f"{dst.stem}_detect.json").read_text())
    result = Image.open(work / f"{dst.stem}_cleaned.png").convert("RGB")
    if alpha is not None:
        result.putalpha(Image.fromarray(alpha))
    result.save(dst)
    comps = info["components"]
    note = (f"{sum(c['method'] == 'fill' for c in comps)} plain fill + "
            f"{sum(c['method'] == 'inpaint' for c in comps)} {info['inpaint_method']} components; "
            f"what was touched: {work / (dst.stem + '_overlay.jpg')}")
    if not comps:
        note += " — WARNING: no text detected, the result is the original"
    print(f"{dst}  ({w}x{h}, {note})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", help="image file(s) and/or folder(s) (folders are not recursed)")
    ap.add_argument("--language")
    ap.add_argument("--keep")
    ap.add_argument("--fix")
    ap.add_argument("--output", help="result .png (one image) or results directory")
    ap.add_argument("--force", action="store_true", help="regenerate results that already exist")
    ap.add_argument("--model", default="gpt_image_2_5")
    ap.add_argument("--quality", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--resolution", default="2k", choices=["1k", "2k", "4k"])
    ap.add_argument("--no-restore", action="store_true")
    ap.add_argument("--restore-threshold", type=float, default=SEED_THR)
    ap.add_argument("--reuse-raw", action="store_true")
    ap.add_argument("--drop-region", type=int, nargs="+", default=[], metavar="N")
    ap.add_argument("--cost", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backend", default="higgsfield", choices=["higgsfield", "local"])
    ap.add_argument("--inpaint", default="qwen", choices=["qwen", "lama"],
                    help="--backend local: what redraws the art behind text (default qwen)")
    a = ap.parse_args()
    if a.backend == "local":
        bad = [f for f, v in (("--language", a.language), ("--keep", a.keep), ("--fix", a.fix),
                              ("--reuse-raw", a.reuse_raw), ("--drop-region", a.drop_region),
                              ("--no-restore", a.no_restore), ("--cost", a.cost)) if v]
        if bad:
            ap.error(f"{', '.join(bad)}: Higgsfield backend only")
        return main_local(a)

    prompt = build_prompt(a.language, a.keep, a.fix)
    jobs = plan_outputs(a.sources, a.output)
    if not jobs:
        sys.exit("ERROR no images found")
    if a.drop_region and (len(jobs) > 1 or a.no_restore):
        ap.error("--drop-region works on ONE image, with the restore step on")
    todo = [(s, d) for s, d in jobs if a.force or a.cost or a.reuse_raw or not d.exists()]
    for s, d in jobs:
        if (s, d) not in todo:
            print(f"skip (exists, --force to redo): {d}")

    if a.dry_run:
        print(f"# model: {a.model}   quality: {a.quality}   resolution: {a.resolution}   "
              f"images: {len(todo)} of {len(jobs)}")
        for s, d in todo:
            with Image.open(s) as im:
                print(f"#   {s}  ({im.width}x{im.height}, {closest_aspect(*im.size)[0]})  ->  {d}")
        print(prompt)
        return
    if not a.reuse_raw and not Path(HF).exists():
        sys.exit("ERROR higgsfield CLI not found — npm i -g @higgsfield/cli, then `higgsfield auth login`")

    for n, (s, d) in enumerate(todo, 1):
        if len(todo) > 1:
            print(f"[{n}/{len(todo)}] {s.name}", file=sys.stderr)
        if not a.cost:
            d.parent.mkdir(parents=True, exist_ok=True)
        clean_one(s, d, prompt, a)


def main_local(a: argparse.Namespace) -> None:
    jobs = plan_outputs(a.sources, a.output)
    if not jobs:
        sys.exit("ERROR no images found")
    todo = [(s, d) for s, d in jobs if a.force or not d.exists()]
    for s, d in jobs:
        if (s, d) not in todo:
            print(f"skip (exists, --force to redo): {d}")
    if a.dry_run:
        print(f"# backend: local   inpaint: {a.inpaint}   images: {len(todo)} of {len(jobs)}")
        for s, d in todo:
            print(f"#   {s}  ->  {d}")
        return
    os.environ["INPAINT"] = a.inpaint           # read by manga-translator-ptbr's inpaint_lama
    sys.path.insert(0, str(TRANSLATOR_SCRIPTS))
    import detect_text
    if a.inpaint == "qwen":
        import inpaint_qwen
        inpaint_qwen.available()                # starts COMFYUI_SERVICE / warns once, up front
    sess = detect_text.make_session()
    for n, (s, d) in enumerate(todo, 1):
        if len(todo) > 1:
            print(f"[{n}/{len(todo)}] {s.name}", file=sys.stderr)
        d.parent.mkdir(parents=True, exist_ok=True)
        clean_local(s, d, sess)


if __name__ == "__main__":
    main()
